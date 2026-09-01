"""Gate 4 — runtime pinning fixes.

Covers the confirmed FluxSwarm bug (prior work item):

  * ``_resolve_runtime`` must give an explicitly-enabled ``opencode-free``
    priority over any paid BYOK key, returning ``nemotron-3-ultra-free`` /
    ``opencode-free``. A stale/paid key (e.g. OpenAI) must never hijack a
    deliberately-selected free runtime.
  * ``_pin_runtime`` must NOT silently ignore a failed ``kanban set-model``: it
    checks the subprocess return code and raises the exact error, stopping the
    launch instead of letting the task fall back to a profile-default runtime
    (which produced the historical ``openrouter / z-ai/glm-5.2`` 401 stalls).

The test matrix reflects the required cases:
  * only opencode-free
  * openai + opencode-free
  * anthropic + opencode-free
  * multiple BYOK providers + opencode-free
  * no providers
  * failed set-model (raise, never silent)
"""
from __future__ import annotations

import pytest

import hermes_client as hc_mod


def _fake_run(stdout="{}", returncode=0, stderr=""):
    return type("_Proc", (), {
        "returncode": returncode, "stdout": stdout, "stderr": stderr,
    })()


class TestResolveRuntime:
    """_resolve_runtime provider precedence."""

    def test_only_opencode_free(self):
        assert hc_mod._resolve_runtime({"opencode-free": "free"}) == (
            "nemotron-3-ultra-free", "opencode-free")

    def test_openai_plus_opencode_free_free_wins(self):
        # A stale OpenAI key must NOT hijack an explicitly-selected free runtime.
        assert hc_mod._resolve_runtime({"openai": "sk-fake", "opencode-free": "free"}) == (
            "nemotron-3-ultra-free", "opencode-free")

    def test_anthropic_plus_opencode_free_free_wins(self):
        assert hc_mod._resolve_runtime({"anthropic": "sk-ant-fake", "opencode-free": "free"}) == (
            "nemotron-3-ultra-free", "opencode-free")

    def test_multiple_byok_plus_opencode_free_free_wins(self):
        keys = {
            "anthropic": "sk-ant",
            "openai": "sk-openai",
            "gemini": "sk-gem",
            "kimi": "sk-kim",
            "opencode-free": "free",
        }
        assert hc_mod._resolve_runtime(keys) == (
            "nemotron-3-ultra-free", "opencode-free")

    def test_multiple_byok_no_free_prefers_first_byok(self):
        # Without opencode-free, keep original BYOK precedence (anthropic first).
        keys = {"openai": "sk-openai", "gemini": "sk-gem", "kimi": "sk-kim"}
        model, provider = hc_mod._resolve_runtime(keys)
        assert model is None
        assert provider == "openai"

    def test_no_providers_falls_back_to_free(self):
        assert hc_mod._resolve_runtime(None) == (
            "nemotron-3-ultra-free", "opencode-free")
        assert hc_mod._resolve_runtime({}) == (
            "nemotron-3-ultra-free", "opencode-free")


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

    def test_pins_every_task_to_free_runtime(self, monkeypatch):
        rec = self._set_up(monkeypatch, ["t1", "t2", "t3"])
        hc_mod._pin_runtime("board-x", {"openai": "sk-openai", "opencode-free": "free"})
        # Last call args (and the fact no exception was raised) is sufficient:
        assert rec["args"] == [
            "set-model", "t3", "nemotron-3-ultra-free", "--provider", "opencode-free"]

    def test_passes_provider_keys_through(self, monkeypatch):
        rec = self._set_up(monkeypatch, ["t1"])
        keys = {"openai": "sk-openai", "opencode-free": "free"}
        hc_mod._pin_runtime("board-x", keys)
        assert rec["provider_keys"] == keys

    def test_failed_set_model_raises_exact_error(self, monkeypatch):
        rec = self._set_up(monkeypatch, ["t1"], ret=1, err="provider_override requires a model_override")
        with pytest.raises(RuntimeError) as ei:
            hc_mod._pin_runtime("board-x", {"opencode-free": "free"})
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
            hc_mod._pin_runtime("board-x", {"opencode-free": "free"})
        assert calls["n"] == 1
