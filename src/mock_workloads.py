"""Mock workloads for the three stages.

Every parameter here is an input to the experiment. The design checks
(scripts/checks.py) verify that measured behavior matches these models.
"""

import asyncio
import time

# OCR: CPU-bound, cost proportional to document size.
OCR_CPU_S_PER_KB = 0.05

# LLM: metered in tokens, latency dominated by output generation.
SYSTEM_PROMPT_TOKENS = 500  # paid once per call, however many docs it carries
INPUT_TOKENS_PER_KB = 250
OUTPUT_TOKENS_PER_DOC = 150
LLM_BASE_LATENCY_S = 0.5
LLM_PREFILL_TOKENS_PER_S = 50_000
LLM_DECODE_TOKENS_PER_S = 100

# Render: I/O-bound, fixed wait per document.
RENDER_S = 0.5


def ocr_cost_s(doc: dict) -> float:
    return doc["size_kb"] * OCR_CPU_S_PER_KB


def mock_ocr(doc: dict) -> dict:
    """Burn CPU until process time advances by the doc's cost.

    Calibrated by CPU time, not an iteration count, so the cost is the same on
    any machine. Under CPU contention the wall time grows, which is the point.
    """
    target = ocr_cost_s(doc)
    start = time.process_time()
    x = 0
    while time.process_time() - start < target:
        for i in range(10_000):
            x += i * i
    return {"ocr_cpu_s": time.process_time() - start}


def doc_input_tokens(doc: dict) -> int:
    return doc["size_kb"] * INPUT_TOKENS_PER_KB


def call_input_tokens(docs: list[dict]) -> int:
    return SYSTEM_PROMPT_TOKENS + sum(doc_input_tokens(d) for d in docs)


def call_output_tokens(docs: list[dict]) -> int:
    return OUTPUT_TOKENS_PER_DOC * len(docs)


def llm_latency_s(input_tokens: int, output_tokens: int) -> float:
    return (
        LLM_BASE_LATENCY_S
        + input_tokens / LLM_PREFILL_TOKENS_PER_S
        + output_tokens / LLM_DECODE_TOKENS_PER_S
    )


async def mock_llm_call(docs: list[dict]) -> list[dict]:
    """One API call extracting entities from every doc in the batch."""
    await asyncio.sleep(llm_latency_s(call_input_tokens(docs), call_output_tokens(docs)))
    return [
        {"entities": [f"{k}:{v}" for k, v in sorted(d["metadata"].items())]}
        for d in docs
    ]


def mock_render(doc: dict) -> dict:
    time.sleep(RENDER_S)
    return {"rendered": True}
