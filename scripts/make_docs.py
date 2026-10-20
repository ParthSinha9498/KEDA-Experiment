"""Generate the 10 sample docs: 5-15KB of filler text, mean exactly 10KB, fixed seed.

    python scripts/make_docs.py

size_kb is what drives OCR cost and LLM tokens; the text pads each doc to
roughly that size so queue payloads are realistic.
"""

import json
import random
from pathlib import Path

SIZES_KB = [5, 6, 7, 8, 9, 11, 12, 13, 14, 15]
SEED = 42
OUT = Path(__file__).resolve().parent.parent / "data" / "sample_docs.json"

VOCAB = (
    "invoice total amount due payment terms net vendor customer address order "
    "quantity unit price tax subtotal shipping date reference account number "
    "description item service delivery balance remit signature approved"
).split()
VENDORS = ["Acme Corp", "Globex", "Initech", "Umbrella", "Hooli"]
DOC_TYPES = ["invoice", "receipt", "purchase_order", "contract"]


def make_doc(i: int, size_kb: int, rng: random.Random) -> dict:
    words, length = [], 0
    while length < size_kb * 1024:
        w = rng.choice(VOCAB)
        words.append(w)
        length += len(w) + 1
    return {
        "sample_id": f"sample_{i:02d}",
        "size_kb": size_kb,
        "text": " ".join(words).ljust(size_kb * 1024)[: size_kb * 1024],
        "metadata": {
            "vendor": rng.choice(VENDORS),
            "doc_type": rng.choice(DOC_TYPES),
            "amount": round(rng.uniform(50, 5000), 2),
            "date": f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}",
        },
    }


def main() -> None:
    assert sum(SIZES_KB) / len(SIZES_KB) == 10, "sizes must average exactly 10KB"
    rng = random.Random(SEED)
    docs = [make_doc(i, kb, rng) for i, kb in enumerate(SIZES_KB)]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(docs, indent=1))
    print(f"wrote {len(docs)} docs to {OUT} (mean {sum(SIZES_KB) / len(SIZES_KB):g}KB)")


if __name__ == "__main__":
    main()
