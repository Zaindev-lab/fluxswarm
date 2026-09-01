"""Production Preparation regression tests.

Locks in the operator-configurable runtime contract:
  * production MUST NOT silently default to opencode-free — an unconfigured
    production launch fails fast (ProviderConfigError) instead;
  * opencode-free / nemotron-3-ultra-free runs ONLY as an explicit Demo opt-in
    (FLUXSWARM_DEMO_MODE=1) or an explicit user BYOK free selection;
  * BYOK providers stay supported (provider precedence, operator model overrides);
  * runtime pinning (`kanban set-model`) stays intact and loud;
and the fail-fast secrets contract:
  * FLUXSWARM_FERNET_KEY / FLUXSWARM_JWT_SECRET missing outside demo mode
    -> startup refusal. No ephemeral secrets in production.

The session runs in demo mode (see backend/tests/conftest.py); each test here
toggles env vars via monkeypatch to exercise the production paths.
"""
from __future__ import annotations

import os
import subprocess

import pytest

import auth
import envguard
import hermes_client as hc
import vault


def _clear_runtime_env(monkeypatch) -> None:
    monkeypatch.delenv("FLUXSWARM_DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv("FLUXSWARM_DEFAULT_MODEL", raising=False)
    for prov in ("anthropic", "openai", "gemini", "kimi"):
        monkeypatch.delenv("FLUXSWARM_MODEL_" + prov.upper(), raising=False)


class TestOperatorDefaultProvider:
    def test_default_provider_with_model(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "anthropic")
        monkeypatch.setenv("FLUXSWARM_DEFAULT_MODEL", "claude-prod-x")
        assert hc._resolve_runtime(None) == ("claude-prod-x", "anthropic")

    def test_default_provider_launch_resolution(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "openai")
        monkeypatch.setenv("FLUXSWARM_DEFAULT_MODEL", "gpt-prod-1")
        assert hc._resolve_launch_runtime(None) == ("gpt-prod-1", "openai")

    def test_default_provider_without_model_fails_fast(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "anthropic")
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime(None)

    def test_per_provider_model_override(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "gemini")
        monkeypatch.setenv("FLUXSWARM_MODEL_GEMINI", "gemini-prod-9")
        assert hc._resolve_runtime(None) == ("gemini-prod-9", "gemini")


class TestDemoFreeProvider:
    def test_demo_mode_defaults_to_free(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        _clear_runtime_env(monkeypatch)
        assert hc._resolve_runtime(None) == (hc.FREE_MODEL, hc.FREE_PROVIDER)
        assert hc._resolve_runtime({}) == (hc.FREE_MODEL, hc.FREE_PROVIDER)

    def test_unconfigured_production_never_defaults_to_free(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        _clear_runtime_env(monkeypatch)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime(None)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime({})

    def test_explicit_free_selection_still_wins_over_default(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "anthropic")
        monkeypatch.setenv("FLUXSWARM_DEFAULT_MODEL", "claude-prod-x")
        assert hc._resolve_runtime({"openai": "sk-fake", "opencode-free": "free"}) == (
            hc.FREE_MODEL, hc.FREE_PROVIDER)


class TestByok:
    def test_byok_resolves_provider_without_model(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        model, prov = hc._resolve_runtime({"openai": "sk-fake"})
        assert model is None
        assert prov == "openai"

    def test_byok_precedence_openai_over_kimi(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        model, prov = hc._resolve_runtime({"kimi": "sk-k", "openai": "sk-o"})
        assert prov == "openai"

    def test_byok_launch_without_operator_model_fails_fast(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_launch_runtime({"openai": "sk-fake"})

    def test_byok_launch_with_operator_model(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_MODEL_OPENAI", "gpt-byok-1")
        assert hc._resolve_launch_runtime({"openai": "sk-fake"}) == ("gpt-byok-1", "openai")

    def test_byok_uses_shared_default_model(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_DEFAULT_MODEL", "shared-model-1")
        assert hc._resolve_launch_runtime({"kimi": "sk-kim"}) == ("shared-model-1", "kimi")


class TestNoSilentFallback:
    def test_launch_swarm_fails_fast_before_any_run(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        _clear_runtime_env(monkeypatch)
        called = {"n": 0}

        def boom_run(*args, **kwargs):
            called["n"] += 1
            raise AssertionError("must not reach _run")

        monkeypatch.setattr(hc, "_run", boom_run)
        with pytest.raises(hc.ProviderConfigError):
            hc.launch_swarm("u1-x", "goal")
        assert called["n"] == 0

    def test_run_in_unconfigured_production_never_claims_free(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        _clear_runtime_env(monkeypatch)
        captured = {}

        def fake_subprocess_run(cmd, **kw):
            captured["env"] = dict(kw.get("env", {}))
            return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
        hc._run(["boards", "ls"], capture=False)
        assert "HERMES_DEFAULT_PROVIDER" not in captured["env"]
        assert "HERMES_DEFAULT_MODEL" not in captured["env"]

    def test_run_in_demo_mode_declares_free(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        _clear_runtime_env(monkeypatch)
        captured = {}

        def fake_subprocess_run(cmd, **kw):
            captured["env"] = dict(kw.get("env", {}))
            return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
        hc._run(["boards", "ls"], capture=False)
        assert captured["env"].get("HERMES_DEFAULT_PROVIDER") == hc.FREE_PROVIDER


class TestRuntimePinningIntact:
    def _set_up(self, monkeypatch, tasks):
        recorded = {"args": None, "keys": None}

        def fake_list(board):
            return [{"id": t} for t in tasks]

        def fake_run(args, board=None, capture=True, provider_keys=None):
            recorded["args"] = args
            recorded["keys"] = provider_keys
            return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

        monkeypatch.setattr(hc, "list_tasks", fake_list)
        monkeypatch.setattr(hc, "_run", fake_run)
        return recorded

    def test_pins_operator_default_provider(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "openai")
        monkeypatch.setenv("FLUXSWARM_DEFAULT_MODEL", "gpt-prod-1")
        rec = self._set_up(monkeypatch, ["t1", "t2"])
        hc._pin_runtime("b", None)
        assert rec["args"] == ["set-model", "t2", "gpt-prod-1", "--provider", "openai"]

    def test_pins_byok_to_operator_model(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_MODEL_ANTHROPIC", "claude-byok-2")
        rec = self._set_up(monkeypatch, ["t7"])
        hc._pin_runtime("b", {"anthropic": "sk-ant-x"})
        assert rec["args"] == ["set-model", "t7", "claude-byok-2", "--provider", "anthropic"]

    def test_pins_free_when_explicitly_selected(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        rec = self._set_up(monkeypatch, ["t9"])
        hc._pin_runtime("b", {"opencode-free": "free"})
        assert rec["args"] == ["set-model", "t9", hc.FREE_MODEL, "--provider", hc.FREE_PROVIDER]

    def test_failed_set_model_still_raises_loudly(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")

        def fake_run(args, board=None, capture=True, provider_keys=None):
            return type("R", (), {"returncode": 1, "stdout": "{}", "stderr": "boom"})()

        monkeypatch.setattr(hc, "list_tasks", lambda b: [{"id": "t1"}])
        monkeypatch.setattr(hc, "_run", fake_run)
        with pytest.raises(RuntimeError, match="set-model"):
            hc._pin_runtime("b", {"opencode-free": "free"})


class TestDemoLaunchFreeOptIn:
    def test_demo_launch_explicitly_pins_free(self, monkeypatch):
        import main as main_mod
        seen = {}

        def fake_launch(board, goal, provider_keys=None):
            seen["keys"] = provider_keys
            return type("S", (), {
                "root_id": "r1", "worker_ids": [], "verifier_id": "v", "synthesizer_id": "s",
            })()

        def fake_fire(slug, plan, provider_keys=None, pid=None):
            seen["fire_k"] = provider_keys

        monkeypatch.setattr(main_mod, "_client_ip", lambda request: "127.0.0.1")
        monkeypatch.setattr(main_mod.hc, "ensure_board", lambda slug: True)
        monkeypatch.setattr(main_mod.hc, "launch_swarm", fake_launch)
        monkeypatch.setattr(main_mod, "_fire_dispatch", fake_fire)

        resp = main_mod.api_demo_launch(None)
        assert resp["demo"] is True
        assert seen["keys"] == {hc.PROVIDER_OPENCODE_FREE: "free"}
        assert seen["fire_k"] == {hc.PROVIDER_OPENCODE_FREE: "free"}


class TestVaultSecretFailFast:
    def test_production_without_fernet_raises(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_FERNET_KEY", raising=False)
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        with pytest.raises(RuntimeError) as ei:
            vault._load_key()
        assert "FLUXSWARM_FERNET_KEY" in str(ei.value)

    def test_env_fernet_used(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_FERNET_KEY", "prod-fernet-key")
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        assert vault._load_key() == b"prod-fernet-key"

    def test_demo_mode_allows_generated_key(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        monkeypatch.delenv("FLUXSWARM_FERNET_KEY", raising=False)
        assert len(vault._load_key()) > 10


class TestAuthSecretFailFast:
    def test_production_without_jwt_raises(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_JWT_SECRET", raising=False)
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        with pytest.raises(RuntimeError) as ei:
            auth._load_secret()
        assert "FLUXSWARM_JWT_SECRET" in str(ei.value)

    def test_demo_mode_jwt_roundtrip(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        monkeypatch.delenv("FLUXSWARM_JWT_SECRET", raising=False)
        token = auth.make_token({"id": 1, "email": "t@x.test", "plan": "demo"})
        assert auth.decode_token(token)["uid"] == 1


class TestEnvGuardStartup:
    def test_production_missing_fernet_raises(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        monkeypatch.delenv("FLUXSWARM_FERNET_KEY", raising=False)
        monkeypatch.setenv("FLUXSWARM_JWT_SECRET", "x")
        with pytest.raises(RuntimeError) as ei:
            envguard.assert_production_secrets()
        assert "FLUXSWARM_FERNET_KEY" in str(ei.value)
        assert "FLUXSWARM_JWT_SECRET" not in str(ei.value)

    def test_production_missing_jwt_raises(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        monkeypatch.setenv("FLUXSWARM_FERNET_KEY", "y")
        monkeypatch.delenv("FLUXSWARM_JWT_SECRET", raising=False)
        with pytest.raises(RuntimeError) as ei:
            envguard.assert_production_secrets()
        assert "FLUXSWARM_JWT_SECRET" in str(ei.value)

    def test_production_both_secrets_present_ok(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        monkeypatch.setenv("FLUXSWARM_FERNET_KEY", "a")
        monkeypatch.setenv("FLUXSWARM_JWT_SECRET", "b")
        assert envguard.assert_production_secrets() is None

    def test_demo_mode_never_required(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        monkeypatch.delenv("FLUXSWARM_FERNET_KEY", raising=False)
        monkeypatch.delenv("FLUXSWARM_JWT_SECRET", raising=False)
        assert envguard.assert_production_secrets() is None