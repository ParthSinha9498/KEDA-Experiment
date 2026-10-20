"""One worker for every stage; the STAGE env var picks which.

    STAGE=ocr     ocr_queue    -> llm_queue     (CPU-bound, one doc at a time)
    STAGE=llm     llm_queue    -> render_queue  (asyncio pool, batched, rate-limited)
    STAGE=render  render_queue -> results       (I/O-bound, one doc at a time)

Run from the repo root: STAGE=ocr python -m src.worker
"""

import asyncio
import json
import os
import signal
import uuid

from src.common import (
    LLM_CONFIG,
    LLM_QUEUE,
    OCR_QUEUE,
    RENDER_QUEUE,
    RESULTS,
    WORKER_ID,
    connect,
    connect_async,
    log,
    now,
)
from src.limits import SharedLimiter
from src.mock_workloads import (
    SYSTEM_PROMPT_TOKENS,
    call_input_tokens,
    call_output_tokens,
    doc_input_tokens,
    mock_llm_call,
    mock_ocr,
    mock_render,
)

# LLM batching: fill to the token ceiling or flush after a timeout. 32k is this
# experiment's setting; a real deployment derives it from the model's limits.
LLM_MAX_INPUT_TOKENS = int(os.environ.get("LLM_MAX_INPUT_TOKENS", 32_000))
LLM_FLUSH_S = float(os.environ.get("LLM_FLUSH_S", 2.0))

# Experiment defaults are set high so the LLM stage doesn't bind;
# scripts/checks.py verifies that against a trial run.
LLM_RPM = float(os.environ.get("LLM_RPM", 100_000))
LLM_TPM = float(os.environ.get("LLM_TPM", 50_000_000))
LLM_CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", 64))

_SYNC_STAGES = {
    "ocr": (OCR_QUEUE, LLM_QUEUE, mock_ocr),
    "render": (RENDER_QUEUE, RESULTS, mock_render),
}


def run_sync_stage(stage: str) -> None:
    """Blocking pop -> process -> push. Scaled by adding replicas."""
    in_queue, out_queue, process = _SYNC_STAGES[stage]
    r = connect()
    stopping = False

    def stop(signum, frame):
        # Finish the doc in hand, then exit. Work popped but not finished is
        # still lost on a hard kill; acknowledge-and-requeue is Part 2.
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(stage, f"worker {WORKER_ID} started")

    while not stopping:
        item = r.brpop(in_queue, timeout=1)
        if item is None:
            continue
        doc = json.loads(item[1])
        start = now()
        doc.update(process(doc))
        end = now()
        doc.update({f"{stage}_start": start, f"{stage}_end": end, f"{stage}_worker": WORKER_ID})
        if stage == "render":
            doc["completed_at"] = end
        r.lpush(out_queue, json.dumps(doc))

    log(stage, f"worker {WORKER_ID} stopped")


async def _batcher(r, batches: asyncio.Queue, stopping: asyncio.Event) -> None:
    """Pull docs into batches capped by input tokens, flushing on timeout.

    batches holds at most one ready batch, so when every call slot is busy the
    batcher blocks and the backlog stays visible in Redis instead of piling up
    in memory.
    """
    batch, tokens, first_at = [], SYSTEM_PROMPT_TOKENS, 0.0

    async def flush():
        nonlocal batch, tokens
        if batch:
            await batches.put(batch)
        batch, tokens = [], SYSTEM_PROMPT_TOKENS

    while not stopping.is_set():
        item = await r.brpop(LLM_QUEUE, timeout=0.1)
        if item is not None:
            doc = json.loads(item[1])
            doc_tokens = doc_input_tokens(doc)
            if batch and tokens + doc_tokens > LLM_MAX_INPUT_TOKENS:
                await flush()
            if not batch:
                first_at = now()
            batch.append(doc)
            tokens += doc_tokens
        if batch and now() - first_at >= LLM_FLUSH_S:
            await flush()
    await flush()


async def _caller(r, limiter: SharedLimiter, batches: asyncio.Queue) -> None:
    """Send one batch at a time; stream its docs onward as soon as it returns."""
    while True:
        batch = await batches.get()
        if batch is None:
            return
        input_tokens, output_tokens = call_input_tokens(batch), call_output_tokens(batch)
        async with limiter.slot(input_tokens + output_tokens) as lease:
            start = now()
            outputs = await mock_llm_call(batch)
            end = now()
        call = {
            "llm_batch_id": uuid.uuid4().hex[:12],
            "llm_batch_docs": len(batch),
            "llm_input_tokens": input_tokens,
            "llm_output_tokens": output_tokens,
            "llm_start": start,
            "llm_end": end,
            "llm_worker": WORKER_ID,
            **{f"llm_wait_{k}_s": v for k, v in lease.waits.items()},
        }
        pipe = r.pipeline()
        for doc, out in zip(batch, outputs):
            doc.update(out)
            doc.update(call)
            pipe.lpush(RENDER_QUEUE, json.dumps(doc))
        await pipe.execute()


async def run_llm_stage() -> None:
    """Batch to the quota, keep up to LLM_CONCURRENCY calls in flight, stream results."""
    r = connect_async()
    limiter = SharedLimiter(r, LLM_RPM, LLM_TPM, LLM_CONCURRENCY)
    batches: asyncio.Queue = asyncio.Queue(maxsize=1)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)

    config = {
        "rpm": LLM_RPM,
        "tpm": LLM_TPM,
        "concurrency": LLM_CONCURRENCY,
        "max_input_tokens": LLM_MAX_INPUT_TOKENS,
        "flush_s": LLM_FLUSH_S,
    }
    await r.hset(LLM_CONFIG, mapping=config)
    log("llm", f"worker {WORKER_ID} started: " + " ".join(f"{k}={v:g}" for k, v in config.items()))
    callers = [asyncio.create_task(_caller(r, limiter, batches)) for _ in range(LLM_CONCURRENCY)]
    await _batcher(r, batches, stopping)
    # Let in-flight and queued batches finish before exiting.
    for _ in callers:
        await batches.put(None)
    await asyncio.gather(*callers)
    await r.aclose()
    log("llm", f"worker {WORKER_ID} stopped")


def main() -> None:
    stage = os.environ["STAGE"]
    if stage == "llm":
        asyncio.run(run_llm_stage())
    elif stage in _SYNC_STAGES:
        run_sync_stage(stage)
    else:
        raise SystemExit(f"unknown STAGE {stage!r}; expected ocr, llm or render")


if __name__ == "__main__":
    main()
