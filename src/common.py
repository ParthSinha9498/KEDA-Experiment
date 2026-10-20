"""Config and helpers shared by every stage."""

import os
import socket
import time
from datetime import datetime

import redis
import redis.asyncio as aioredis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Pipeline queues (Redis lists). Producers LPUSH, workers BRPOP, so each queue is FIFO.
OCR_QUEUE = "ocr_queue"
LLM_QUEUE = "llm_queue"
RENDER_QUEUE = "render_queue"
RESULTS = "results"
RUN_META = "run:meta"
LLM_CONFIG = "llm:config"  # the LLM worker's settings, recorded with each run
LIMITS_PREFIX = "llm_limits"

# Hostname is unique per pod; the pid keeps local processes on one machine apart.
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def connect() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def connect_async() -> aioredis.Redis:
    return aioredis.Redis.from_url(REDIS_URL, decode_responses=True)


def log(stage: str, msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='milliseconds')} [{stage}] {msg}", flush=True)


def now() -> float:
    # Wall-clock seconds. Every timestamp in a run comes from one machine, so they compare directly.
    return time.time()
