"""Design checks: confirm the setup tests what the experiment claims.

    python -m scripts.checks unit [--out checks/unit.txt]
    python -m scripts.checks run [--run-dir runs/fixed-15] [--out checks/run.txt]
    python -m scripts.checks scaledown --run-dir runs/keda-1 --min-replicas 1 [--out ...]

`unit` checks the workloads and limiter directly (needs Redis for the
concurrency check). `run` checks a finished run: from a collector run
directory if given (which also enables the render-drain check), otherwise from
Redis. `scaledown` checks a KEDA run's replicas fall back after each burst.
Thresholds are pass/fail criteria fixed before running, not predictions.
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

from scripts.report import load_run
from src import worker
from src.common import LLM_CONFIG, RESULTS, RUN_META, connect, connect_async, now
from src.limits import SharedLimiter
from src.mock_workloads import (
    call_input_tokens,
    call_output_tokens,
    llm_latency_s,
    mock_llm_call,
    mock_ocr,
    ocr_cost_s,
)

MODEL_TOLERANCE = 0.10       # measured vs. model, +/-10%
CONCURRENCY_TARGET = 5       # the limit the concurrency check exercises
LLM_HEADROOM = 2.0           # LLM capacity must be >= 2x measured OCR throughput
RENDER_MAX_BUSY = 0.80       # render busy fraction must stay below this
RENDER_DRAIN_MAX_S = 5.0     # render_queue empty within this long of a burst's last LLM batch
# Replicas back at the minimum within this long of their queue emptying:
# 30s stabilization window (or cooldownPeriod) + 15s autoscaler sync + 15s slack.
# The default 300s stabilization window would fail it.
SCALEDOWN_MAX_S = 60.0

DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs.json"
_lines: list[str] = []
_failed = False


def out(line: str = "") -> None:
    _lines.append(line)
    print(line, flush=True)


def verdict(name: str, ok: bool | None, detail: str) -> None:
    global _failed
    label = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    _failed |= ok is False
    out(f"[{label}] {name}: {detail}")


def within(measured: float, model: float) -> bool:
    return abs(measured - model) <= MODEL_TOLERANCE * model


# --- unit checks ---------------------------------------------------------

def check_ocr_model(samples: list[dict]) -> None:
    out("OCR CPU time per doc vs. model (0.05s/KB):")
    worst = 0.0
    for doc in samples:
        model = ocr_cost_s(doc)
        measured = mock_ocr(doc)["ocr_cpu_s"]
        worst = max(worst, abs(measured - model) / model)
        out(f"  {doc['size_kb']:>2}KB  model {model:.3f}s  measured {measured:.3f}s")
    verdict("OCR matches the model", worst <= MODEL_TOLERANCE, f"worst deviation {worst:.1%}")


async def check_llm_model(samples: list[dict]) -> None:
    out("LLM call latency vs. token formula:")
    worst = 0.0
    for n in (1, 4, 12):
        docs = [samples[i % len(samples)] for i in range(n)]
        model = llm_latency_s(call_input_tokens(docs), call_output_tokens(docs))
        start = now()
        await mock_llm_call(docs)
        measured = now() - start
        worst = max(worst, abs(measured - model) / model)
        out(f"  {n:>2} docs  model {model:.3f}s  measured {measured:.3f}s")
    verdict("LLM matches the model", worst <= MODEL_TOLERANCE, f"worst deviation {worst:.1%}")


async def check_concurrency() -> None:
    r = connect_async()
    limiter = SharedLimiter(r, rpm=1e9, tpm=1e12, concurrency=CONCURRENCY_TARGET, prefix="check_limits")
    await limiter.reset()
    intervals = []

    async def call():
        async with limiter.slot(1):
            start = now()
            await asyncio.sleep(0.3)
            intervals.append((start, now()))

    await asyncio.gather(*(call() for _ in range(4 * CONCURRENCY_TARGET)))
    await limiter.reset()
    await r.aclose()
    peak = max_overlap(intervals)
    verdict("LLM concurrency works", peak == CONCURRENCY_TARGET,
            f"peak calls in flight {peak} with limit {CONCURRENCY_TARGET} (must reach it, never exceed it)")


def max_overlap(intervals: list[tuple[float, float]]) -> int:
    events = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals])
    peak = cur = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


# --- run checks (a finished run) ------------------------------------------

def by_burst(docs: list[dict]) -> dict[int, list[dict]]:
    groups = defaultdict(list)
    for d in docs:
        groups[d["burst"]].append(d)
    return groups


def check_llm_not_binding(docs: list[dict], limits: dict) -> None:
    calls = {d["llm_batch_id"]: d for d in docs}.values()
    n_calls = len(calls)
    mean_docs = sum(c["llm_batch_docs"] for c in calls) / n_calls
    mean_latency = sum(c["llm_end"] - c["llm_start"] for c in calls) / n_calls
    mean_tokens = sum(c["llm_input_tokens"] + c["llm_output_tokens"] for c in calls) / n_calls
    waits = {k: sum(c[f"llm_wait_{k}_s"] for c in calls) for k in ("concurrency", "rpm", "tpm")}

    # The most docs/s the configured limits allow at the batch sizes and call
    # latency actually observed in this run.
    capacity = min(
        limits["concurrency"] * mean_docs / mean_latency,
        limits["rpm"] / 60 * mean_docs,
        limits["tpm"] / 60 * mean_docs / mean_tokens,
    )
    # OCR throughput while OCR was busy, per burst; the fastest burst counts.
    ocr_rate = max(
        len(g) / (max(d["ocr_end"] for d in g) - min(d["ocr_start"] for d in g))
        for g in by_burst(docs).values()
    )
    out(f"  calls {n_calls}, mean {mean_docs:.1f} docs/call, mean latency {mean_latency:.2f}s, "
        f"mean {mean_tokens:.0f} tokens/call")
    out("  limiter wait totals: " + ", ".join(f"{k} {v:.1f}s" for k, v in waits.items()))
    out(f"  LLM capacity at observed batches {capacity:.1f} docs/s vs. OCR {ocr_rate:.1f} docs/s")
    verdict("The LLM doesn't bind", capacity >= LLM_HEADROOM * ocr_rate,
            f"margin {capacity / ocr_rate:.1f}x (need >= {LLM_HEADROOM:g}x)")


def check_render_busy(docs: list[dict]) -> None:
    # Busy fraction per render worker over each burst's render window.
    busy = defaultdict(float)
    span = 0.0
    for g in by_burst(docs).values():
        span += max(d["render_end"] for d in g) - min(d["render_start"] for d in g)
        for d in g:
            busy[d["render_worker"]] += d["render_end"] - d["render_start"]
    worst = max(busy.values()) / span
    out(f"  {len(busy)} render workers, busiest at {worst:.0%} of the render window")
    verdict("Render busy fraction", worst < RENDER_MAX_BUSY,
            f"busiest worker {worst:.0%} (need < {RENDER_MAX_BUSY:.0%}); meaningful for the fixed-15 run")


def check_render_drain(docs: list[dict], series: list[dict], start: float) -> None:
    # After a burst's last LLM batch lands, render_queue should empty quickly
    # rather than trend up.
    worst = 0.0
    for b, g in sorted(by_burst(docs).items()):
        last_llm = max(d["llm_end"] for d in g) - start
        empty_at = next((row["t"] for row in series if row["t"] >= last_llm and row["render_queue"] == 0), None)
        if empty_at is None:
            verdict("Render queue drains", False, f"burst {b}: render_queue never emptied after the last LLM batch")
            return
        worst = max(worst, empty_at - last_llm)
        out(f"  burst {b}: render_queue empty {empty_at - last_llm:.1f}s after the last LLM batch")
    verdict("Render queue drains", worst <= RENDER_DRAIN_MAX_S,
            f"slowest drain {worst:.1f}s (need <= {RENDER_DRAIN_MAX_S:g}s); meaningful for the fixed-15 run")


def run_checks(run_dir: str | None) -> None:
    if run_dir:
        run = load_run(run_dir)
        docs, run_meta, limits = run.docs, run.meta["run"], run.meta["llm_config"]
        source = run_dir
    else:
        r = connect()
        run_meta = r.hgetall(RUN_META)
        docs = [json.loads(x) for x in r.lrange(RESULTS, 0, -1)]
        recorded = r.hgetall(LLM_CONFIG)
        limits = {k: float(v) for k, v in recorded.items()} if recorded else {
            "rpm": worker.LLM_RPM, "tpm": worker.LLM_TPM, "concurrency": worker.LLM_CONCURRENCY}
        source = "Redis"
    total = int(float(run_meta.get("total", 0)))
    if not docs or len(docs) < total:
        raise SystemExit(f"run not finished: {len(docs)}/{total} docs in results")

    out(f"Run ({source}): {run_meta.get('bursts')} bursts x {run_meta.get('burst_size')} docs, "
        f"{len(docs)} results")
    out(f"LLM limits: rpm={limits['rpm']:g} tpm={limits['tpm']:g} concurrency={limits['concurrency']:g}")
    check_llm_not_binding(docs, limits)
    check_render_busy(docs)
    if run_dir:
        check_render_drain(docs, run.series, run.start)
    else:
        verdict("Render queue drains", None, "needs a collector run directory (--run-dir)")


# --- scale-down (a finished KEDA run) -------------------------------------

def check_scaledown(run_dir: str, min_replicas: int) -> None:
    run = load_run(run_dir)
    out(f"Run {run_dir}: replicas must reach {min_replicas} within {SCALEDOWN_MAX_S:g}s of their queue emptying")
    worst, ok = 0.0, True
    for stage, queue in (("ocr", "ocr_queue"), ("render", "render_queue")):
        for b in range(run.bursts):
            lo = b * run.interval
            hi = (b + 1) * run.interval if b + 1 < run.bursts else float("inf")
            busy = [row["t"] for row in run.series if lo <= row["t"] < hi and row[queue] > 0]
            if not busy:
                continue
            emptied = busy[-1]
            down = next((row["t"] for row in run.series
                         if row["t"] > emptied and row[f"{stage}_replicas"] <= min_replicas), None)
            if down is None:
                out(f"  {stage} burst {b}: never fell to {min_replicas} after the queue emptied at t={emptied:.0f}s")
                ok = False
                continue
            delay = down - emptied
            worst = max(worst, delay)
            ok &= delay <= SCALEDOWN_MAX_S
            out(f"  {stage} burst {b}: queue empty at t={emptied:.0f}s, at {min_replicas} replicas {delay:.0f}s later")
    verdict("Scale-down behaves as configured", ok,
            f"slowest {worst:.0f}s (need <= {SCALEDOWN_MAX_S:g}s)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["unit", "run", "scaledown"])
    p.add_argument("--run-dir", help="a run directory written by the collector")
    p.add_argument("--min-replicas", type=int, help="the KEDA run's minReplicaCount (scaledown)")
    p.add_argument("--out", help="also write the output to this file")
    args = p.parse_args()

    if args.mode == "unit":
        samples = json.loads(DOCS.read_text())
        check_ocr_model(samples)
        asyncio.run(check_llm_model(samples))
        asyncio.run(check_concurrency())
    elif args.mode == "run":
        run_checks(args.run_dir)
    else:
        if not args.run_dir or args.min_replicas is None:
            p.error("scaledown needs --run-dir and --min-replicas")
        check_scaledown(args.run_dir, args.min_replicas)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(_lines) + "\n")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
