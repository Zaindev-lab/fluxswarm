"""Gate 4 — runtime pinning fixes (Phase 3: no free tier).

Covers the confirmed FluxSwarm bug (prior work item):
  * ``_resolve_runtime`` must resolve a deliberate runtime: BYOK provider in
    precedence order, else the operator-configured default — never a silent
    free fallback. Suffixed/unknown provider keys (e.g. "opencode-free") must
    be ignored: they are not real providers in Phase 3.
  * ``_pin_runtime`` must NOT silently ignore a failed ``kanban set-model``: it
    checks the subprocess return code and raises the exact error, stopping the
    launch instead of letting the task fall back to a profile-default runtime
    (which produced the historical ``openrouter / z-ai/glm-5.2`` 401 stalls).

The test matrix reflects the required cases:
  * single BYOK provider
  * multiple BYOK providers (precedence order)
  * unknown/free keys ignored -> operator default
  * no providers -> operator default
  * no providers + unconfigured -> ProviderConfigError (fail fast)
  * failed set-model (raise, never silent)
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import hermes_client as hc_mod


def _fake_run(stdout="{}", returncode=0, stderr=""):
    return type("_Proc", (), {
        "returncode": returncode, "stdout": stdout, "stderr": stderr,
    })()


class TestResolveRuntime:
    """_resolve_runtime provider precedence (Phase 3: no free)."""

    def test_single_byok_resolves_provider(self):
        assert hc_mod._resolve_runtime({"openai": "sk-fake"}) == (None, "openai")

    def test_multiple_byok_prefers_first_in_precedence(self):
        keys = {"openai": "sk-openai", "gemini": "sk-gem", "kimi": "sk-kim"}
        model, provider = hc_mod._resolve_runtime(keys)
        assert model is None
        assert provider == "openai"

    def test_anthropic_first_when_listed_last(self):
        # Precedence is (anthropic, openai, gemini, kimi) — order of the dict
        # must not matter.
        keys = {"kimi": "sk-kim", "openai": "sk-open", "anthropic": "sk-ant"}
        model, provider = hc_mod._resolve_runtime(keys)
        assert provider == "anthropic"

    def test_free_provider_key_is_ignored_uses_operator_default(self):
        # "opencode-free" is NOT a real provider in Phase 3 — it must be
        # ignored, never hijack a runtime, and never per-provider-resolve.
        with patch.dict(os.environ, {
            "FLUXSWARM_DEFAULT_PROVIDER": "openai",
            "FLUXSWARM_DEFAULT_MODEL": "gpt-4",
        }, clear=False):
            assert hc_mod._resolve_runtime({"opencode-free": "free"}) == ("gpt-4", "openai")
            assert hc_mod._resolve_runtime({"openai": "sk-fake", "opencode-free": "free"}) == (
                None, "openai")

    def test_no_providers_uses_operator_default(self):
        with patch.dict(os.environ, {
            "FLUXSWARM_DEFAULT_PROVIDER": "gemini",
            "FLUXSWARM_MODEL_GEMINI": "gemini-2.0-flash",
        }, clear=False):
            assert hc_mod._resolve_runtime(None) == ("gemini-2.0-flash", "gemini")
            assert hc_mod._resolve_runtime({}) == ("gemini-2.0-flash", "gemini")

    def test_no_providers_unconfigured_raises(self):
        # A deployment without a deliberate default must fail fast — there is
        # NO free fallback in any mode.
        with patch.dict(os.environ, {
            "FLUXSWARM_DEFAULT_PROVIDER": "", "FLUXSWARM_DEMO_MODE": "1",
        }, clear=False):
            with pytest.raises(hc_mod.ProviderConfigError):
                hc_mod._resolve_runtime(None)
            with pytest.raises(hc_mod.ProviderConfigError):
                hc_mod._resolve_runtime({})


class TestPinRuntime:
    """_pin_runtime pins tasks and fails loudly on set-model errors."""

    def _set_up(self, monkeypatch, tasks, ret=None, err=""):
        """Mock list_tasks + _run, return the recorded args list."""
        recorded = {}

        def fake_list(board):
            return [{"id": t} for t in tasks]

        def fake_run(args, board=None, capture=True, provider_keys=None):
            recorded["args"] = args
            recorded["provider_keys"] = provider_keys
            rc = ret if ret is not None else 0
            return _fake_run(stdout="{}", returncode=rc, stderr=err)

        monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
        monkeypatch.setattr(hc_mod, "_run", fake_run)
        return recorded

    def test_pins_every_task_to_byok_runtime(self, monkeypatch):
        with patch.dict(os.environ, {"FLUXSWARM_MODEL_OPENAI": "gpt-4-turbo"}, clear=False):
            rec = self._set_up(monkeypatch, ["t1", "t2", "t3"])
            hc_mod._pin_runtime("board-x", {"openai": "sk-openai"})
            # Last call args (and the fact no exception was raised) is sufficient:
            assert rec["args"] == [
                "set-model", "t3", "gpt-4-turbo", "--provider", "openai"]

    def test_pins_every_task_to_operator_default(self, monkeypatch):
        with patch.dict(os.environ, {
            "FLUXSWARM_DEFAULT_PROVIDER": "anthropic",
            "FLUXSWARM_DEFAULT_MODEL": "claude-3-sonnet",
        }, clear=False):
            rec = self._set_up(monkeypatch, ["t1", "t2"])
            hc_mod._pin_runtime("board-x", None)
            assert rec["args"] == [
                "set-model", "t2", "claude-3-sonnet", "--provider", "anthropic"]

    def test_passes_provider_keys_through(self, monkeypatch):
        rec = self._set_up(monkeypatch, ["t1"])
        keys = {"openai": "sk-openai"}
        with patch.dict(os.environ, {"FLUXSWARM_MODEL_OPENAI": "gpt-4"}, clear=False):
            hc_mod._pin_runtime("board-x", keys)
        assert rec["provider_keys"] == keys

    def test_failed_set_model_raises_exact_error(self, monkeypatch):
        rec = self._set_up(monkeypatch, ["t1"], ret=1, err="provider_override requires a model_override")
        with pytest.raises(RuntimeError) as ei:
            with patch.dict(os.environ, {"FLUXSWARM_MODEL_OPENAI": "gpt-4"}, clear=False):
                hc_mod._pin_runtime("board-x", {"openai": "sk-openai"})
        assert "set-model" in str(ei.value)
        assert "t1" in str(ei.value)
        assert "provider_override requires a model_override" in str(ei.value)

    def test_failure_stops_launch_no_further_tasks(self, monkeypatch):
        # The first failing set-model must abort before pinning later tasks.
        calls = {"n": 0}

        def fake_list(board):
            return [{"id": f"t{i}"} for i in range(3)]

        def fake_run(args, board=None, capture=True, provider_keys=None):
            calls["n"] += 1
            # Fail on the very first pin.
            return _fake_run(stdout="{}", returncode=1, stderr="boom")

        monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
        monkeypatch.setattr(hc_mod, "_run", fake_run)
        with pytest.raises(RuntimeError):
            with patch.dict(os.environ, {"FLUXSWARM_MODEL_OPENAI": "gpt-4"}, clear=False):
                hc_mod._pin_runtime("board-x", {"openai": "sk-openai"})
        assert calls["n"] == 1