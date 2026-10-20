"""Rate limits for the LLM API, shared through Redis.

Three simulated limits: requests per minute, tokens per minute and calls in
flight. They live in Redis rather than in each pod, so they hold across every
replica: a per-pod limiter would multiply with the replica count.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import redis.asyncio as aioredis

from src.common import LIMITS_PREFIX, now

# Token bucket. Starts full (one minute's allowance), refills continuously at
# limit/60 per second. Takes `want` if available and returns 0, otherwise takes
# nothing and returns how long until `want` would be available. Returned as a
# string because Lua numbers are truncated to integers on the way out.
_BUCKET_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1e6
local cap = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local want = tonumber(ARGV[3])
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens'))
local ts = tonumber(redis.call('HGET', KEYS[1], 'ts'))
if tokens == nil then
  tokens = cap
  ts = now
end
tokens = math.min(cap, tokens + (now - ts) * rate)
local wait = 0
if tokens >= want then
  tokens = tokens - want
else
  wait = (want - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
return tostring(wait)
"""

# Concurrency as a set of leases. Expired leases (a crashed worker) are purged,
# so a dead pod can't hold a slot forever.
_LEASE_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1e6
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - tonumber(ARGV[3]))
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[1]) then
  redis.call('ZADD', KEYS[1], now, ARGV[2])
  return 1
end
return 0
"""

_LEASE_POLL_S = 0.05


@dataclass
class Lease:
    id: str
    waits: dict = field(default_factory=dict)  # seconds waited per limit


class SharedLimiter:
    def __init__(
        self,
        r: aioredis.Redis,
        rpm: float,
        tpm: float,
        concurrency: int,
        prefix: str = LIMITS_PREFIX,
        lease_ttl_s: float = 300,
    ):
        self.rpm, self.tpm, self.concurrency = rpm, tpm, concurrency
        self.lease_ttl_s = lease_ttl_s
        self._rpm_key = f"{prefix}:rpm"
        self._tpm_key = f"{prefix}:tpm"
        self._lease_key = f"{prefix}:leases"
        self._bucket = r.register_script(_BUCKET_LUA)
        self._lease = r.register_script(_LEASE_LUA)
        self._r = r

    async def _take(self, key: str, per_minute: float, amount: float) -> float:
        """Block until `amount` is taken from the bucket; return seconds waited."""
        if amount > per_minute:
            raise ValueError(f"{amount} exceeds the per-minute limit of {per_minute} on {key}")
        start = now()
        while True:
            wait = float(await self._bucket(keys=[key], args=[per_minute, per_minute / 60, amount]))
            if wait == 0:
                return now() - start
            await asyncio.sleep(wait)

    async def acquire(self, tokens: int) -> Lease:
        # Concurrency first, then the request and the tokens. Like a real client,
        # a call holds its slot while it waits for the rate limits.
        lease = Lease(id=uuid.uuid4().hex)
        start = now()
        while not await self._lease(keys=[self._lease_key], args=[self.concurrency, lease.id, self.lease_ttl_s]):
            await asyncio.sleep(_LEASE_POLL_S)
        lease.waits["concurrency"] = now() - start
        lease.waits["rpm"] = await self._take(self._rpm_key, self.rpm, 1)
        # Output tokens are charged up front. The mock knows them exactly; a real
        # client would reserve an estimate (e.g. max_tokens) and settle later.
        lease.waits["tpm"] = await self._take(self._tpm_key, self.tpm, tokens)
        return lease

    async def release(self, lease: Lease) -> None:
        await self._r.zrem(self._lease_key, lease.id)

    @asynccontextmanager
    async def slot(self, tokens: int):
        lease = await self.acquire(tokens)
        try:
            yield lease
        finally:
            await self.release(lease)

    async def reset(self) -> None:
        await self._r.delete(self._rpm_key, self._tpm_key, self._lease_key)
