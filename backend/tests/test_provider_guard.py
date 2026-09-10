"""Provider guard (Phase C) — hermetic unit tests.

Covers the taxonomy, circuit-breaker state machine, round-robin selection,
capacity report and the fail-closed budget gate. No network is touched: the
probe function is monkeypatched with synthetic ProviderHealth objects, and all
breaker/rotation state is reset between tests.
"""
from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

import provider_guard as guard
from provider import ProviderHealth, ProviderStatus


@pytest.fixture(autouse=True)
def _fresh_guard(monkeypatch):
    guard.reset_state()
    yield
    guard.reset_state()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def _h(status: ProviderStatus, provider: str = "gemini", detail: str = "",
       model: str | None = None) -> ProviderHealth:
    return ProviderHealth(status=status, provider=provider, model=model,
                          detail=detail)


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------
class TestClassify:
    @pytest.mark.parametrize("code,expected", [
        (429, guard.ErrorClass.RATE_LIMITED),
        (401, guard.ErrorClass.AUTH_ERROR),
        (403, guard.ErrorClass.AUTH_ERROR),
        (400, guard.ErrorClass.BAD_REQUEST),
        (404, guard.ErrorClass.MODEL_UNAVAILABLE),
        (408, guard.ErrorClass.TIMEOUT),
        (500, guard.ErrorClass.PROVIDER_ERROR),
        (502, guard.ErrorClass.PROVIDER_ERROR),
        (503, guard.ErrorClass.PROVIDER_ERROR),
        (504, guard.ErrorClass.PROVIDER_ERROR),
        (418, guard.ErrorClass.UNKNOWN),
        (None, guard.ErrorClass.UNKNOWN),
    ])
    def test_codes(self, code, expected):
        assert guard.classify_provider_error(code) == expected

    def test_retry_budgets_bounded(self):
        assert guard.ErrorClass.RATE_LIMITED.retries == 1
        assert guard.ErrorClass.AUTH_ERROR.retries == 0      # never blind-retry auth
        assert guard.ErrorClass.BAD_REQUEST.retries == 0
        assert guard.ErrorClass.MODEL_UNAVAILABLE.retries == 0
        assert guard.ErrorClass.TIMEOUT.retries == 1
        assert guard.ErrorClass.PROVIDER_ERROR.retries == 2  # bounded, not endless
        assert guard.ErrorClass.UNKNOWN.retries == 0

    def test_cooldownable_only_transient(self):
        assert guard.ErrorClass.RATE_LIMITED.cooldownable
        assert guard.ErrorClass.TIMEOUT.cooldownable
        assert guard.ErrorClass.PROVIDER_ERROR.cooldownable
        assert not guard.ErrorClass.AUTH_ERROR.cooldownable
        assert not guard.ErrorClass.BAD_REQUEST.cooldownable


class TestClassifyFromHealth:
    def test_status_direct(self):
        assert guard._error_from_health(_h(ProviderStatus.AUTH_ERROR)) == \
            guard.ErrorClass.AUTH_ERROR
        assert guard._error_from_health(_h(ProviderStatus.TIMEOUT)) == \
            guard.ErrorClass.TIMEOUT
        assert guard._error_from_health(_h(ProviderStatus.MODEL_UNAVAILABLE)) == \
            guard.ErrorClass.MODEL_UNAVAILABLE
        assert guard._error_from_health(_h(ProviderStatus.CONFIG_ERROR)) == \
            guard.ErrorClass.UNKNOWN

    def test_provider_unavailable_refined_by_status(self):
        assert guard._error_from_health(_h(ProviderStatus.PROVIDER_UNAVAILABLE,
                                            detail="HTTP 429: rate limited")) == \
            guard.ErrorClass.RATE_LIMITED
        assert guard._error_from_health(_h(ProviderStatus.PROVIDER_UNAVAILABLE,
                                            detail="HTTP 503: provider unavailable")) == \
            guard.ErrorClass.PROVIDER_ERROR
        # no code visible -> transient provider error (bounded retries)
        assert guard._error_from_health(_h(ProviderStatus.PROVIDER_UNAVAILABLE,
                                            detail="connection failed: boom")) == \
            guard.ErrorClass.PROVIDER_ERROR


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
class TestCircuit:
    def test_success_resets(self):
        c = guard.ProviderCircuit("gemini")
        c.record_failure(guard.ErrorClass.PROVIDER_ERROR)
        assert c.state == guard.CircuitState.COOLDOWN
        c.record_success()
        assert c.state == guard.CircuitState.HEALTHY
        assert c.consecutive_fails == 0

    def test_rate_limited_cooldown(self):
        c = guard.ProviderCircuit("gemini")
        t0 = time.time()
        c.record_failure(guard.ErrorClass.RATE_LIMITED, now=t0, retry_after_s=0)
        assert c.state == guard.CircuitState.COOLDOWN
        assert not c.pickable(t0)
        assert c.pickable(t0 + 301)

    def test_retry_after_respected(self):
        c = guard.ProviderCircuit("gemini")
        t0 = time.time()
        c.record_failure(guard.ErrorClass.RATE_LIMITED, now=t0, retry_after_s=10)
        assert not c.pickable(t0 + 5)
        assert c.pickable(t0 + 11)

    def test_consecutive_failures_open_breaker(self):
        c = guard.ProviderCircuit("gemini")
        t0 = time.time()
        for i in range(3):
            c.record_failure(guard.ErrorClass.PROVIDER_ERROR, now=t0 + i)
        assert c.state == guard.CircuitState.EXHAUSTED
        assert not c.pickable(t0 + 10)
        # once the open window lapses a re-probe is allowed
        assert c.pickable(t0 + 610)

    def test_auth_error_closes_without_repeated_probing(self):
        c = guard.ProviderCircuit("gemini")
        t0 = time.time()
        c.record_failure(guard.ErrorClass.AUTH_ERROR, now=t0)
        assert c.state == guard.CircuitState.AUTH_ERROR
        assert not c.pickable(t0 + 60)
        # backoff lapses -> allows exactly one re-probe
        assert c.pickable(t0 + 1801)

    def test_bad_request_does_not_count_as_flap(self):
        c = guard.ProviderCircuit("gemini")
        c.record_failure(guard.ErrorClass.BAD_REQUEST)
        assert c.state == guard.CircuitState.DEGRADED
        assert c.consecutive_fails == 0

    def test_model_unavailable_disables(self):
        c = guard.ProviderCircuit("gemini")
        c.record_failure(guard.ErrorClass.MODEL_UNAVAILABLE)
        assert c.state == guard.CircuitState.DISABLED
        assert not c.pickable(time.time() + 9999)

    def test_disable_is_terminal(self):
        c = guard.ProviderCircuit("gemini")
        t0 = time.time()
        c.disable()
        assert not c.pickable(t0 + 99999)


# ---------------------------------------------------------------------------
# Round-robin selection
# ---------------------------------------------------------------------------
_ENTRIES = [
    {"provider": "google", "model": "gemini-3.5-flash-lite", "requires_key": False,
     "probe_key": "gemini"},
    {"provider": "openrouter", "model": "google/gemini-flash-1.5:free",
     "requires_key": True, "key_env": "OPENROUTER_API_KEY", "probe_key": "openrouter"},
]


class TestPickRotation:
    def test_healthy_returns_first_once(self, monkeypatch):
        monkeypatch.setattr(guard, "check_provider_health",
                            lambda provider, model=None: _h(ProviderStatus.SUCCESS))
        pick = guard.pick_rotation(_ENTRIES)
        assert pick["provider"] == "google"
        assert pick["probe_key"] == "gemini"
        assert pick["reason"] == "healthy"

    def test_rotates_across_calls(self, monkeypatch):
        monkeypatch.setattr(guard, "check_provider_health",
                            lambda provider, model=None: _h(ProviderStatus.SUCCESS))
        seen = []
        for _ in range(4):
            seen.append(guard.pick_rotation(_ENTRIES)["provider"])
        assert seen == ["google", "openrouter", "google", "openrouter"]

    def test_first_unhealthy_skips_to_next(self, monkeypatch):
        def fake(provider, model=None):
            if provider == "gemini":
                return _h(ProviderStatus.PROVIDER_UNAVAILABLE, detail="HTTP 503: down")
            return _h(ProviderStatus.SUCCESS)
        monkeypatch.setattr(guard, "check_provider_health", fake)
        pick = guard.pick_rotation(_ENTRIES)
        assert pick["provider"] == "openrouter"
        # gemini now mid-cooldown
        assert guard.breaker_for("gemini").state == guard.CircuitState.COOLDOWN

    def test_whole_key_in_cooldown_does_not_probe(self, monkeypatch):
        calls = {"n": 0}
        def fake(provider, model=None):
            calls["n"] += 1
            return _h(ProviderStatus.PROVIDER_UNAVAILABLE, detail="HTTP 429: busy")
        monkeypatch.setattr(guard, "check_provider_health", fake)
        with pytest.raises(guard.ProviderPoolBlocked):
            guard.pick_rotation(_ENTRIES)
        assert calls["n"] == 2  # both candidates probed once, then blocked
        # second call must NOT probe again (cooldown active)
        with pytest.raises(guard.ProviderPoolBlocked):
            guard.pick_rotation(_ENTRIES)
        assert calls["n"] == 2

    def test_empty_entries_blocked(self, monkeypatch):
        monkeypatch.setattr(guard, "check_provider_health",
                            lambda provider, model=None: _h(ProviderStatus.SUCCESS))
        with pytest.raises(guard.ProviderPoolBlocked):
            guard.pick_rotation([])

    def test_model_unavailable_disables_and_stops_probing_siblings(self, monkeypatch):
        def fake(provider, model=None):
            if provider == "gemini":
                return _h(ProviderStatus.MODEL_UNAVAILABLE)
            return _h(ProviderStatus.SUCCESS)
        monkeypatch.setattr(guard, "check_provider_health", fake)
        with pytest.raises(guard.ProviderPoolBlocked):
            guard.pick_rotation(_ENTRIES)
        assert guard.breaker_for("gemini").state == guard.CircuitState.DISABLED

    def test_rotation_disabled_always_first(self, monkeypatch):
        monkeypatch.setattr(guard, "check_provider_health",
                            lambda provider, model=None: _h(ProviderStatus.SUCCESS))
        with patch.dict(os.environ, {"FLUXSWARM_POOL_ROTATION": "0"}, clear=False):
            seen = [guard.pick_rotation(_ENTRIES)["provider"] for _ in range(3)]
        assert seen == ["google", "google", "google"]


# ---------------------------------------------------------------------------
# Capacity report
# ---------------------------------------------------------------------------
class TestCapacity:
    def test_disabled_pool(self):
        assert guard.capacity_report(_ENTRIES, pool_enabled=False)["state"] == "disabled"

    def test_no_entries_exhausted(self):
        assert guard.capacity_report([], pool_enabled=True)["state"] == "exhausted"

    def test_no_keys_exhausted(self):
        # every provider requires a key and none are set
        entries = [
            {"provider": "openrouter", "requires_key": True,
             "key_env": "OPENROUTER_API_KEY", "probe_key": "openrouter"},
            {"provider": "gemini", "requires_key": True,
             "key_env": "GEMINI_API_KEY", "probe_key": "gemini"},
        ]
        report = guard.capacity_report(entries, pool_enabled=True)
        assert report["state"] == "exhausted"

    def test_one_healthy_available(self, monkeypatch):
        monkeypatch.setattr(guard, "check_provider_health",
                            lambda provider, model=None: _h(ProviderStatus.SUCCESS))
        entry = {"provider": "gemini", "model": "m",
                 "requires_key": False, "probe_key": "gemini"}
        guard.pick_rotation([entry])
        report = guard.capacity_report([entry], pool_enabled=True)
        assert report["state"] == "available"

    def test_two_providers_one_healthy_limited(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-1")

        def fake(provider, model=None):
            if provider == "gemini":
                return _h(ProviderStatus.SUCCESS)
            return _h(ProviderStatus.PROVIDER_UNAVAILABLE, detail="HTTP 503")
        monkeypatch.setattr(guard, "check_provider_health", fake)
        # make gemini healthy and openrouter cooling, without touching the network
        guard.pick_rotation(_ENTRIES)
        guard.breaker_for("openrouter").record_failure(guard.ErrorClass.PROVIDER_ERROR)
        report = guard.capacity_report(_ENTRIES, pool_enabled=True)
        assert report["state"] == "limited"

    def test_all_cooling_degraded(self, monkeypatch):
        monkeypatch.setattr(guard, "check_provider_health",
                            lambda provider, model=None: _h(
                                ProviderStatus.PROVIDER_UNAVAILABLE, detail="HTTP 429: busy"))
        with pytest.raises(guard.ProviderPoolBlocked):
            guard.pick_rotation(_ENTRIES)
        assert guard.capacity_report(_ENTRIES, pool_enabled=True)["state"] == "degraded"


# ---------------------------------------------------------------------------
# Budget gate (fail-closed)
# ---------------------------------------------------------------------------
class TestBudget:
    def test_default_ok(self):
        d = guard.budget_gate()
        assert d.ok and d.reason == "ok"
        assert d.ceilings["max_runtime_s"] == 600
        assert d.ceilings["max_tasks"] > 0

    def test_runtime_over(self):
        d = guard.budget_gate(runtime_s=601)
        assert not d.ok and d.reason == "BUDGET_EXHAUSTED"

    def test_tasks_over(self):
        d = guard.budget_gate(tasks=513)
        assert not d.ok and d.reason == "BUDGET_EXHAUSTED"

    def test_negative_usage_fails_closed(self):
        assert not guard.budget_gate(runtime_s=-1).ok

    def test_zero_ceiling_fails_closed(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_BUDGET_MAX_RUNTIME_S", "0")
        d = guard.budget_gate()
        assert not d.ok and d.reason == "BUDGET_EXHAUSTED"

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_BUDGET_MAX_RUNTIME_S", "30")
        assert guard.budget_gate(runtime_s=30).ok
        assert not guard.budget_gate(runtime_s=31).ok

    def test_bad_config_defaults_not_crash(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_BUDGET_MAX_RUNTIME_S", "abc")
        d = guard.budget_gate()
        assert d.ok  # degrades to documented default (600), never crashes


# ---------------------------------------------------------------------------
# Paid fallback (operator opt-in, default OFF)
# ---------------------------------------------------------------------------
class TestPaidFallback:
    def test_default_off(self):
        # Never spend money without an explicit operator opt-in.
        assert not guard.paid_fallback_enabled()

    def test_official_on(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_PAID_FALLBACK_ENABLED", "1")
        assert guard.paid_fallback_enabled()

    def test_loud_variants(self, monkeypatch):
        for v in ("true", "yes", "TRUE", "Yes"):
            monkeypatch.setenv("FLUXSWARM_PAID_FALLBACK_ENABLED", v)
            assert guard.paid_fallback_enabled()

    def test_anyother_off(self, monkeypatch):
        for v in ("0", "false", "no", "", "  "):
            monkeypatch.setenv("FLUXSWARM_PAID_FALLBACK_ENABLED", v)
            assert not guard.paid_fallback_enabled()