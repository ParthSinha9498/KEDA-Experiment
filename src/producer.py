"""Submit bursts of documents to ocr_queue.

    python -m src.producer --bursts 3 --burst-size 1000 --interval 300 --reset

Docs are copies of the 10 samples, cycled in order, each with a unique doc_id
and the time it was submitted.
"""

import argparse
import json
import time

from src.common import (
    LIMITS_PREFIX,
    LLM_QUEUE,
    OCR_QUEUE,
    RENDER_QUEUE,
    RESULTS,
    RUN_META,
    connect,
    log,
    now,
)

PUSH_CHUNK = 200


def reset(r) -> None:
    r.delete(OCR_QUEUE, LLM_QUEUE, RENDER_QUEUE, RESULTS, RUN_META)
    for key in r.scan_iter(f"{LIMITS_PREFIX}:*"):
        r.delete(key)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--docs", default="data/sample_docs.json")
    p.add_argument("--bursts", type=int, default=3)
    p.add_argument("--burst-size", type=int, default=1000)
    p.add_argument("--interval", type=float, default=300, help="seconds between burst starts")
    p.add_argument("--reset", action="store_true", help="clear queues, results and limiter state first")
    args = p.parse_args()

    with open(args.docs) as f:
        samples = json.load(f)
    r = connect()
    if args.reset:
        reset(r)

    t0 = now()
    r.hset(RUN_META, mapping={
        "start": t0,
        "bursts": args.bursts,
        "burst_size": args.burst_size,
        "interval": args.interval,
        "total": args.bursts * args.burst_size,
    })

    for b in range(args.bursts):
        delay = t0 + b * args.interval - now()
        if delay > 0:
            time.sleep(delay)
        pipe = r.pipeline()
        for i in range(args.burst_size):
            doc = dict(samples[i % len(samples)])
            doc.update({"doc_id": f"b{b}-{i:05d}", "burst": b, "submitted_at": now()})
            pipe.lpush(OCR_QUEUE, json.dumps(doc))
            if (i + 1) % PUSH_CHUNK == 0:
                pipe.execute()
        pipe.execute()
        log("producer", f"burst {b + 1}/{args.bursts}: {args.burst_size} docs submitted")


if __name__ == "__main__":
    main()
