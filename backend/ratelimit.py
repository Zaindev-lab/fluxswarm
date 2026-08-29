"""Rate limiting for FluxSwarm.

Two interchangeable backends:
  * Redis (multi-worker safe) when REDIS_URL / FLUXSWARM_REDIS_URL is set and
    reachable — used in production behind a proxy / multiple workers.
  * In-process (single worker) identical fallback when Redis is absent.

Windows note: fixed-window counters via INCR+EXPIRE; a window that crosses the
boundary is the standard trade-off (a burst can briefly exceed by one window).

Nothing here ever stores tokens, keys, or password hashes.
"""
from __future__ import annotations

import os
import sys
import threading
import time

try:
    import redis as _redis_lib
except ImportError:  # pragma: no cover - venv without the optional dep
    _redis_lib = None

REDIS_URL = os.environ.get("REDIS_URL") or os.environ.get("FLUXSWARM_REDIS_URL")

_LOGIN_WINDOW = 900       # seconds
_LOGIN_MAX_FAILURES = 5   # failed attempts per ip+email within the window
_IP_WINDOW = 60           # seconds
_IP_MAX_REQUESTS = 20     # auth requests per IP within the window
_REG_WINDOW = 3600        # seconds
_REG_MAX_PER_IP = 10      # registrations per IP per hour
_PURCHASE_WINDOW = 3600   # seconds
_PURCHASE_MAX = 5         # template purchases per user per hour (burst guard)


class _Limiter:  # in-process memory backend
    backend = "memory"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._login_fail: dict[tuple[str, str], list[float]] = {}
        self._ip_hits: dict[str, list[float]] = {}
        self._reg_hits: dict[str, list[float]] = {}
        self._purchase_hits: dict[int, list[float]] = {}

    @staticmethod
    def _prune(items: list[float], cutoff: float) -> list[float]:
        # Drop timestamps older than `cutoff` (sliding-window expiry). The bug
        # here used `items[i] < cutoff`: timestamps are EPOCH SECONDS, but cutoff
        # was computed as `time.time() - WINDOW`, i.e. the threshold itself is an
        # epoch. That compares fresh timestamps against the wrong quantity and
        # never expired the list -> counters grew unbounded. Keyed expiry below
        # is the real fix; this helper is kept only for the in-process backend.
        return [t for t in items if t >= cutoff]

    def login_allowed(self, ip: str, email: str) -> bool:
        key = (ip, email)
        with self._lock:
            arr = self._login_fail.get(key, [])
            self._login_fail[key] = self._prune(arr, time.time() - _LOGIN_WINDOW)
            return len(self._login_fail[key]) < _LOGIN_MAX_FAILURES

    def record_login_failure(self, ip: str, email: str) -> None:
        with self._lock:
            self._login_fail.setdefault((ip, email), []).append(time.time())

    def clear_login_failures(self, ip: str, email: str) -> None:
        with self._lock:
            self._login_fail.pop((ip, email), None)

    def ip_allowed(self, ip: str) -> bool:
        with self._lock:
            arr = self._ip_hits.get(ip, [])
            self._ip_hits[ip] = self._prune(arr, time.time() - _IP_WINDOW)
            return len(self._ip_hits[ip]) < _IP_MAX_REQUESTS

    def hit_ip(self, ip: str) -> None:
        with self._lock:
            self._ip_hits.setdefault(ip, []).append(time.time())

    def register_allowed(self, ip: str) -> bool:
        with self._lock:
            arr = self._reg_hits.get(ip, [])
            self._reg_hits[ip] = self._prune(arr, time.time() - _REG_WINDOW)
            return len(self._reg_hits[ip]) < _REG_MAX_PER_IP

    def record_registration(self, ip: str) -> None:
        with self._lock:
            self._reg_hits.setdefault(ip, []).append(time.time())

    def purchase_allowed(self, user_id: int) -> bool:
        with self._lock:
            arr = self._purchase_hits.get(user_id, [])
            self._purchase_hits[user_id] = self._prune(arr, time.time() - _PURCHASE_WINDOW)
            return len(self._purchase_hits[user_id]) < _PURCHASE_MAX

    def record_purchase(self, user_id: int) -> None:
        with self._lock:
            self._purchase_hits.setdefault(user_id, []).append(time.time())


class _RedisLimiter:  # multi-worker backend (fixed-window counters)
    backend = "redis"

    def __init__(self, url: str = REDIS_URL) -> None:
        self._r = _redis_lib.Redis.from_url(
            url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True,
        )
        self._r.ping()  # raises ConnectionError if unreachable -> fallback happens

    def _incr(self, key: str, window: int) -> None:
        # A transient Redis error must NOT silently disable rate limiting, so we
        # let the exception propagate to the caller (which degrades to the
        # in-process backend) instead of swallowing it into a no-op counter.
        p = self._r.pipeline()
        p.incr(key)
        p.expire(key, window)
        p.execute()

    def login_allowed(self, ip: str, email: str) -> bool:
        try:
            v = self._r.get(f"fs:lf:{ip}:{email}")
        except Exception:
            # Connection lost -> fail closed (block) rather than trust "allowed".
            return False
        return int(v or 0) < _LOGIN_MAX_FAILURES

    def record_login_failure(self, ip: str, email: str) -> None:
        self._incr(f"fs:lf:{ip}:{email}", _LOGIN_WINDOW)

    def clear_login_failures(self, ip: str, email: str) -> None:
        try:
            self._r.delete(f"fs:lf:{ip}:{email}")
        except Exception:
            pass

    def ip_allowed(self, ip: str) -> bool:
        try:
            v = self._r.get(f"fs:ip:{ip}")
        except Exception:
            return False
        return int(v or 0) < _IP_MAX_REQUESTS

    def hit_ip(self, ip: str) -> None:
        self._incr(f"fs:ip:{ip}", _IP_WINDOW)

    def register_allowed(self, ip: str) -> bool:
        try:
            v = self._r.get(f"fs:reg:{ip}")
        except Exception:
            return False
        return int(v or 0) < _REG_MAX_PER_IP

    def record_registration(self, ip: str) -> None:
        self._incr(f"fs:reg:{ip}", _REG_WINDOW)

    def purchase_allowed(self, user_id: int) -> bool:
        try:
            v = self._r.get(f"fs:buy:{user_id}")
        except Exception:
            return False
        return int(v or 0) < _PURCHASE_MAX

    def record_purchase(self, user_id: int) -> None:
        self._incr(f"fs:buy:{user_id}", _PURCHASE_WINDOW)


_MEMORY = None


def _build() -> "_Limiter | _RedisLimiter":
    global _MEMORY
    if REDIS_URL and _redis_lib is not None:
        try:
            return _RedisLimiter()
        except Exception as exc:  # network error / wrong URL -> degrade gracefully
            print(f"[ratelimit] Redis unreachable ({exc}); using in-process backend.",
                  file=sys.stderr)
    if _MEMORY is None:
        _MEMORY = _Limiter()
    return _MEMORY


limiter = _build()