"""Provider selection guard (Phase C).

Sits between the existing demo/provider health abstraction (provider.py,
provider_pool.py) and the launch routes. Adds what the master brief requires
on top of the current first-healthy-wins pool WITHOUT changing the public
contract used by main.py and its tests:

  * error classification (429/401/403/400/404/408/5xx/unknown) with a retry
    policy hint per class;
  * per-provider circuit breakers (HEALTHY/DEGRADED/COOLDOWN/EXHAUSTED/
    AUTH_ERROR/DISABLED);
  * round-robin selection across healthy pool entries (replaces always-first);
  * a truthful pool capacity report (available/degraded/limited/exhausted/
    disabled); and
  * a fail-closed budget gate (BUDGET_EXHAUSTED), driven entirely by
    operator configuration — we never invent a provider fact here.

Provider endpoints / model names stay owned by provider.py and the operator's
env (FLUXSWARM_MODEL_*, FLUXSWARM_DEFAULT_MODEL). This module only classifies
measured outcomes and decides *which already-supported entry* may run.

Config reference (all optional, safe defaults):
  FLUXSWARM_POOL_ROTATION          "1"   round-robin instead of always-first
  FLUXSWARM_POOL_COOLDOWN_S        "300" default cooldown after a rate-limited /
                                         provider-unavailable probe result
  FLUXSWARM_POOL_BREAKER_FAILS       3   consecutive probe failures -> EXHAUSTED
  FLUXSWARM_POOL_BREAKER_OPEN_S    "600" how long an EXHAUSTED breaker stays open
  FLUXSWARM_POOL_AUTH_BACKOFF_S  "1800" how long an AUTH_ERROR breaker stays closed
  FLUXSWARM_BUDGET_MAX_RUNTIME_S  "600"  wall-clock ceiling per launch (matches the
                                         existing dispatch timeout default)
  FLUXSWARM_BUDGET_MAX_TASKS       "512"  ceiling on spawned agent tasks per launch
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from provider import ProviderStatus, check_provider_health


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
class ErrorClass(str, Enum):
    """Coarse taxonomy of a provider-side failure, plus the retry policy hint.

    The codes are exactly the ones the brief lists: 429, 401, 403, 400, 404,
    408, 500, 502, 503, 504. Anything else is `unknown` — we never guess a
    friendlier class for an unclassified status.
    """
    RATE_LIMITED = "rate_limited"            # 429 — honor Retry-After / cooldown
    AUTH_ERROR = "auth_error"                # 401/403 — no repeated blind retries
    BAD_REQUEST = "bad_request"              # 400 — no blind retry
    MODEL_UNAVAILABLE = "model_unavailable"  # 404 model unknown
    TIMEOUT = "timeout"                      # 408 / probe timeout
    PROVIDER_ERROR = "provider_error"        # 500/502/503/504 — bounded retries
    UNKNOWN = "unknown"

    @property
    def retries(self) -> int:
        """Bounded retry budget per class (never infinite)."""
        return {
            "rate_limited": 1,
            "auth_error": 0,
            "bad_request": 0,
            "model_unavailable": 0,
            "timeout": 1,
            "provider_error": 2,
            "unknown": 0,
        }[self.value]

    @property
    def cooldownable(self) -> bool:
        """Classes that warrant a provider cooldown before the next probe."""
        return self in (ErrorClass.RATE_LIMITED, ErrorClass.TIMEOUT,
                        ErrorClass.PROVIDER_ERROR)


def classify_provider_error(status_code: int | None, provider: str = "",
                            body: str = "") -> ErrorClass:
    """Map a raw provider status/body onto the ErrorClass taxonomy.

    ``status_code`` may be None (transport-level failure) which is treated as
    UNKNOWN (0 retries) rather than guessed. 404 is only classified as
    MODEL_UNAVAILABLE; a plain 400 with no body evidence of a model problem
    stays BAD_REQUEST (no blind retry). Unknown statuses => UNKNOWN (0 retries).
    """
    if status_code is None:
        return ErrorClass.UNKNOWN
    if status_code in (408,):
        return ErrorClass.TIMEOUT
    if status_code == 429:
        return ErrorClass.RATE_LIMITED
    if status_code in (401, 403):
        return ErrorClass.AUTH_ERROR
    if status_code == 404:
        return ErrorClass.MODEL_UNAVAILABLE
    if status_code == 400:
        return ErrorClass.BAD_REQUEST
    if status_code in (500, 502, 503, 504):
        return ErrorClass.PROVIDER_ERROR
    return ErrorClass.UNKNOWN


def _error_from_health(health) -> ErrorClass:
    """Map a ProviderHealth onto the classify taxonomy without inventing facts.

    ProviderStatus carries the truth about the probe outcome; the detail text
    is only used to refine a PROVIDER_UNAVAILABLE into rate-limited vs
    provider-error when an HTTP code is visible.
    """
    st = getattr(health, "status", None)
    direct = {
        ProviderStatus.AUTH_ERROR: ErrorClass.AUTH_ERROR,
        ProviderStatus.TIMEOUT: ErrorClass.TIMEOUT,
        ProviderStatus.MODEL_UNAVAILABLE: ErrorClass.MODEL_UNAVAILABLE,
        ProviderStatus.CONFIG_ERROR: ErrorClass.UNKNOWN,
    }
    if st in direct:
        return direct[st]
    if st == ProviderStatus.SUCCESS:
        return ErrorClass.UNKNOWN  # callers should never classify a success
    code = _status_code_for(health)
    if code in (429,):
        return ErrorClass.RATE_LIMITED
    if code in (401, 403):
        return ErrorClass.AUTH_ERROR
    if code in (400,):
        return ErrorClass.BAD_REQUEST
    if code in (404,):
        return ErrorClass.MODEL_UNAVAILABLE
    if code in (408,):
        return ErrorClass.TIMEOUT
    if code in (500, 502, 503, 504):
        return ErrorClass.PROVIDER_ERROR
    # provider_reachable but endpoint signalled an unclassified problem: treat
    # as transient provider error (bounded retries) — never infinite.
    return ErrorClass.PROVIDER_ERROR


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
class CircuitState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"          # reachable but recently errored
    COOLDOWN = "cooldown"          # currently cooling down; not pickable
    EXHAUSTED = "exhausted"        # breaker open (N consecutive failures)
    AUTH_ERROR = "auth_error"      # credential rejected; no repeated probing
    DISABLED = "disabled"          # operator/entry disabled


def _cooldown_s_default() -> int:
    return max(0, int(os.environ.get("FLUXSWARM_POOL_COOLDOWN_S", "300")))


def _breaker_fails() -> int:
    return max(1, int(os.environ.get("FLUXSWARM_POOL_BREAKER_FAILS", "3")))


def _breaker_open_s() -> int:
    return max(0, int(os.environ.get("FLUXSWARM_POOL_BREAKER_OPEN_S", "600")))


def _auth_backoff_s() -> int:
    return max(0, int(os.environ.get("FLUXSWARM_POOL_AUTH_BACKOFF_S", "1800")))


@dataclass
class ProviderCircuit:
    """Per-provider breaker state (thread-safe). Not persisted by design: after
    a process restart the pool re-probes once before deciding, so a stale open
    breaker never permanently orphans a provider."""

    provider: str
    state: CircuitState = CircuitState.HEALTHY
    consecutive_fails: int = 0
    opens_at: float = 0.0            # when the breaker opened
    cooldown_until: float = 0.0      # earliest time a cooldown ends
    retry_after_s: float = 0.0       # last seen Retry-After (best-effort)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def pickable(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            if self.state in (CircuitState.DISABLED, CircuitState.AUTH_ERROR):
                if self.state == CircuitState.AUTH_ERROR and now >= self.opens_at + _auth_backoff_s():
                    # Backoff lapsed: allow one re-probe so a rotated key or a
                    # resolved credential can recover the provider.
                    self.state = CircuitState.COOLDOWN
                    self.cooldown_until = now
                    return True
                return False
            if self.state == CircuitState.EXHAUSTED:
                if now >= self.opens_at + _breaker_open_s():
                    # Cooldown lapsed: allow a re-probe (which restores state
                    # on success); mark COOLDOWN so a failed re-probe falls
                    # back to the next entry rather than blocking selection.
                    self.state = CircuitState.COOLDOWN
                    self.cooldown_until = now
                    return True
                return False
            if self.state == CircuitState.COOLDOWN:
                return now >= self.cooldown_until
            return True

    def record_success(self) -> None:
        with self._lock:
            self.state = CircuitState.HEALTHY
            self.consecutive_fails = 0
            self.cooldown_until = 0.0
            self.retry_after_s = 0.0

    def record_failure(self, error: ErrorClass, now: float | None = None,
                       retry_after_s: float = 0.0) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if retry_after_s and retry_after_s > 0:
                self.retry_after_s = retry_after_s
            if error == ErrorClass.AUTH_ERROR:
                self.state = CircuitState.AUTH_ERROR
                self.opens_at = now
                self.consecutive_fails += 1
                return
            if error in (ErrorClass.RATE_LIMITED, ErrorClass.TIMEOUT,
                         ErrorClass.PROVIDER_ERROR):
                self.consecutive_fails += 1
                cooldown = (retry_after_s if retry_after_s > 0
                            else _cooldown_s_default())
                self.cooldown_until = now + cooldown
                if self.consecutive_fails >= _breaker_fails():
                    self.state = CircuitState.EXHAUSTED
                    self.opens_at = now
                else:
                    self.state = CircuitState.COOLDOWN
                return
            # BAD_REQUEST / MODEL_UNAVAILABLE / UNKNOWN: not a transient
            # provider hiccup. A known-bad model string is worth disabling
            # (don't keep re-probing it); other client errors just mark the
            # provider DEGRADED without counting as flapping.
            if error == ErrorClass.MODEL_UNAVAILABLE:
                self.state = CircuitState.DISABLED
            else:
                self.state = CircuitState.DEGRADED

    def disable(self) -> None:
        with self._lock:
            self.state = CircuitState.DISABLED

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock:
            return {
                "provider": self.provider,
                "state": self.state.value,
                "consecutive_fails": self.consecutive_fails,
                "cooldown_remaining_s": round(
                    max(0.0, self.cooldown_until - now), 1),
                "retry_after_s": round(self.retry_after_s, 1),
            }


# ---------------------------------------------------------------------------
# Registry + rotation
# ---------------------------------------------------------------------------
_breakers: dict[str, ProviderCircuit] = {}
_breakers_lock = threading.Lock()
_rotation: dict[str, int] = {}   # probe_key -> next index


def breaker_for(provider: str) -> ProviderCircuit:
    """Return the shared breaker for *provider* (creates on first use)."""
    with _breakers_lock:
        c = _breakers.get(provider)
        if c is None:
            c = ProviderCircuit(provider=provider)
            _breakers[provider] = c
        return c


def reset_state() -> None:
    """Clear all breaker/rotation state (test/ops helper, not for prod calls)."""
    with _breakers_lock:
        _breakers.clear()
        _rotation.clear()


def rotation_enabled() -> bool:
    return os.environ.get("FLUXSWARM_POOL_ROTATION", "1").strip() in ("1", "true", "yes")


def _next_index(provider: str, count: int) -> int:
    with _breakers_lock:
        i = _rotation.get(provider, 0)
        _rotation[provider] = (i + 1) % max(1, count)
        return i


class ProviderPoolBlocked(Exception):
    """Raised when no pool entry is currently pickable (exhausted / cooling /
    auth-blocked / disabled). Carries the breaker snapshot for diagnostics."""

    def __init__(self, provider: str, snapshot: dict):
        super().__init__(f"provider pool blocked: {provider} "
                         f"({snapshot.get('state', '?')})")
        self.provider = provider
        self.snapshot = snapshot


def pick_rotation(entries: list[dict], enabled: bool | None = None) -> dict | None:
    """Round-robin pick the first pickable entry among *entries*.

    Entry dicts keep their DEMO_PROVIDERS shape ({provider, model, ...}); the
    caller (provider_pool) applies the key-presence gate before handing them
    in. ``probe_key`` (optional) is the provider.py endpoint key — each entry's
    circuit and the rotation cursor group on that key, so "google" and "gemini"
    models share one circuit yet each entry is skipped independently when its
    own breaker is cooling down.

    Exactly one probe happens per pickable candidate until an entry succeeds or
    the list is exhausted (ProviderPoolBlocked). A bad model pin disables the
    entry (breaker DISABLED) so we do not retry a known-bad string.

    Candidates whose circuit is mid-cooldown / auth-blocked are skipped without
    a probe (no pointless AUTH_ERROR round-trips while the key is absent); the
    whole selection only raises ProviderPoolBlocked when nothing was pickable.
    """
    enabled = rotation_enabled() if enabled is None else enabled
    if not entries:
        raise ProviderPoolBlocked("none", {"state": "exhausted"})
    group_key = str(entries[0].get("probe_key") or entries[0].get("provider", ""))

    order = list(range(len(entries)))
    if enabled and len(entries) > 1:
        start = _next_index(group_key, len(entries))
        order = [(start + i) % len(entries) for i in range(len(entries))]

    blocked: dict | None = None
    for idx in order:
        entry = entries[idx]
        entry_key = str(entry.get("probe_key") or entry.get("provider", ""))
        breaker = breaker_for(entry_key)
        if not breaker.pickable():
            blocked = blocked or breaker.snapshot()
            continue
        health = check_provider_health(entry_key, model=entry.get("model"))
        if health.status == ProviderStatus.SUCCESS:
            breaker.record_success()
            out = dict(entry)
            out["reason"] = "healthy"
            out["probe_key"] = entry_key
            return out
        error = _error_from_health(health)
        breaker.record_failure(error, retry_after_s=_retry_after_for(health))
        blocked = breaker.snapshot()
        if error == ErrorClass.MODEL_UNAVAILABLE:
            break  # known-bad model string: don't keep re-probing siblings
    if blocked is None:
        blocked = {"state": "exhausted", "provider": group_key}
    raise ProviderPoolBlocked(blocked.get("provider", group_key), blocked)


def _status_code_for(health) -> int | None:
    """Best-effort HTTP status from a ProviderHealth detail string."""
    detail = getattr(health, "detail", "") or ""
    for tok in ("429", "401", "403", "400", "404", "408", "500", "502", "503", "504"):
        if tok in detail:
            return int(tok)
    return None


def _retry_after_for(health) -> float:
    detail = getattr(health, "detail", "") or ""
    for line in str(detail).splitlines():
        low = line.lower()
        if low.startswith("retry-after") and ":" in low:
            cand = low.split(":", 1)[-1].strip()
            if cand.replace(".", "", 1).isdigit():
                try:
                    return float(cand)
                except ValueError:
                    pass
    return 0.0


# ---------------------------------------------------------------------------
# Capacity states
# ---------------------------------------------------------------------------
class PoolCapacityState(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    LIMITED = "limited"
    EXHAUSTED = "exhausted"
    DISABLED = "disabled"


def capacity_report(entries: list[dict], pool_enabled: bool = True,
                    now: float | None = None) -> dict:
    """Truthful pool capacity derived from the current breaker state.

    DISABLED  -> pool turned off by the operator.
    EXHAUSTED -> zero entries have a usable credential (nothing to probe).
    LIMITED   -> exactly one viable entry is healthy right now.
    DEGRADED  -> entries exist with credentials but every candidate is
                 currently cooling / auth-blocked / disabled.
    AVAILABLE -> at least one entry is HEALTHY and pickable right now.
    """
    now = time.time() if now is None else now
    if not pool_enabled:
        return {"state": PoolCapacityState.DISABLED.value, "providers": []}
    if not entries:
        return {"state": PoolCapacityState.EXHAUSTED.value, "providers": []}
    reports = []
    keyed_viable = 0
    healthy = 0
    seen: set[str] = set()
    for entry in entries:
        cap_name = str(entry.get("probe_key") or entry.get("provider", ""))
        if cap_name in seen:
            continue
        seen.add(cap_name)
        breaker = breaker_for(cap_name)
        reports.append(breaker.snapshot(now))
        needs_key = entry.get("requires_key", False)
        key_env = entry.get("key_env")
        if needs_key and key_env and not os.environ.get(key_env, "").strip():
            continue
        keyed_viable += 1
        if breaker.state == CircuitState.HEALTHY:
            healthy += 1
    if keyed_viable == 0:
        state = PoolCapacityState.EXHAUSTED
    elif healthy == 0:
        state = PoolCapacityState.DEGRADED
    elif healthy == 1 and len(seen) > 1:
        state = PoolCapacityState.LIMITED
    else:
        state = PoolCapacityState.AVAILABLE
    return {"state": state.value, "providers": reports}


# ---------------------------------------------------------------------------
# Budget guard (fail-closed)
# ---------------------------------------------------------------------------
class BudgetDecision:
    __slots__ = ("ok", "reason", "ceilings")

    def __init__(self, ok: bool, reason: str, ceilings: dict):
        self.ok = ok
        self.reason = reason
        self.ceilings = ceilings


def _cfg_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def budget_ceilings() -> dict:
    """Effective budget ceilings (env-overridable; defaults documented above)."""
    return {
        "max_runtime_s": _cfg_int("FLUXSWARM_BUDGET_MAX_RUNTIME_S", 600),
        "max_tasks": _cfg_int("FLUXSWARM_BUDGET_MAX_TASKS", 512),
        "pool_cooldown_s": _cooldown_s_default(),
        "breaker_fails": _breaker_fails(),
        "breaker_open_s": _breaker_open_s(),
    }


def budget_gate(runtime_s: int = 0, tasks: int = 0) -> BudgetDecision:
    """Fail-closed gate BEFORE a launch: refuse when measured usage would
    exceed the configured ceilings, or when the config itself is unusable
    (never silently allow unbounded spend on a bad config value).

    ``runtime_s`` / ``tasks`` are app-level measured quantities (wall-clock
    dispatch window, spawned agent-task count) — NOT dollar amounts, and never
    claimed to be.
    """
    ceilings = budget_ceilings()
    if runtime_s < 0 or tasks < 0:
        return BudgetDecision(False, "BUDGET_EXHAUSTED", ceilings)
    if ceilings["max_runtime_s"] <= 0 or ceilings["max_tasks"] <= 0:
        # A zero/negative ceiling is a misconfiguration; fail closed rather
        # than interpret it as "unlimited".
        return BudgetDecision(False, "BUDGET_EXHAUSTED", ceilings)
    if runtime_s and runtime_s > ceilings["max_runtime_s"]:
        return BudgetDecision(False, "BUDGET_EXHAUSTED", ceilings)
    if tasks and tasks > ceilings["max_tasks"]:
        return BudgetDecision(False, "BUDGET_EXHAUSTED", ceilings)
    return BudgetDecision(True, "ok", ceilings)


def paid_fallback_enabled() -> bool:
    """Operator opt-in for the paid last-resort runtime (default OFF).

    The demo surface normally never spends money: when the free pool is
    exhausted it fails with ``demo_provider_unavailable``. Only an explicit
    FLUXSWARM_PAID_FALLBACK_ENABLED=1 (with an operator-configured default
    runtime) permits a paid provider as the last resort — always behind the
    budget gate, and never on by default.
    """
    return os.environ.get("FLUXSWARM_PAID_FALLBACK_ENABLED", "0").strip().lower() in ("1", "true", "yes")