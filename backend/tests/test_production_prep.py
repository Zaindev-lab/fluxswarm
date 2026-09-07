"""Production Preparation regression tests.

Locks in the operator-configurable runtime contract (Phase 3: no free tier):
  * production MUST NOT silently default to any model — an unconfigured
    launch fails fast (ProviderConfigError) instead;
  * there is NO free provider ("opencode-free"/"big-pickle" were removed);
    demo mode also requires a configured operator runtime;
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
    for prov in ("anthropic", "openai", "gemini", "kimi", "openrouter"):
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


class TestNoFreeTier:
    def test_demo_mode_without_configured_runtime_raises(self, monkeypatch):
        # Phase 3 removed the anonymous free model entirely: even Demo mode must
        # have a real operator-configured runtime (or BYOK). No silent fallback.
        # The one zero-cost exception is an explicit OpenRouter free model — that
        # still requires a key; see TestOpenRouterFreeTier.
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        _clear_runtime_env(monkeypatch)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime(None)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime({})
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime({"opencode-free": "free"})

    def test_unconfigured_production_never_defaults_to_free(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        _clear_runtime_env(monkeypatch)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime(None)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime({})

    def test_free_key_never_wins_over_byok(self, monkeypatch):
        # A stale/unknown "opencode-free" key is ignored — BYOK precedence holds.
        _clear_runtime_env(monkeypatch)
        assert hc._resolve_runtime({"openai": "sk-fake", "opencode-free": "free"}) == (
            None, "openai")

    def test_free_key_only_uses_operator_default(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "anthropic")
        monkeypatch.setenv("FLUXSWARM_DEFAULT_MODEL", "claude-prod-x")
        assert hc._resolve_runtime({"opencode-free": "free"}) == ("claude-prod-x", "anthropic")


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


class TestOpenRouterFreeTier:
    """OpenRouter free tier = the supported zero-cost runtime.

    It is deliberately explicit: it only kicks in once openrouter has been
    chosen (operator default or a real BYOK key), never silently elsewhere.
    """

    def test_operator_default_openrouter_pins_free_model_without_model_env(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "openrouter")
        assert hc._resolve_runtime(None) == (hc.OPENROUTER_DEFAULT_MODEL, "openrouter")
        assert hc._resolve_launch_runtime(None) == (hc.OPENROUTER_DEFAULT_MODEL, "openrouter")

    def test_operator_default_openrouter_respects_model_override(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "openrouter")
        monkeypatch.setenv("FLUXSWARM_MODEL_OPENROUTER", "deepseek/deepseek-r1:free")
        assert hc._resolve_runtime(None) == ("deepseek/deepseek-r1:free", "openrouter")

    def test_byok_openrouter_resolves_without_operator_model(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        model, prov = hc._resolve_runtime({"openrouter": "sk-or-free"})
        assert model is None
        assert prov == "openrouter"

    def test_byok_openrouter_launch_pins_free_model(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        assert hc._resolve_launch_runtime({"openrouter": "sk-or-free"}) == (
            hc.OPENROUTER_DEFAULT_MODEL, "openrouter")

    def test_openrouter_loses_to_paid_byok_precedence(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        assert hc._resolve_runtime({"openrouter": "sk-or", "openai": "sk-o"}) == (None, "openai")
        assert hc._resolve_runtime({"openrouter": "sk-or", "opencode-free": "free"}) == (
            None, "openrouter")

    def test_free_model_default_never_leaks_to_other_providers(self, monkeypatch):
        # No provider chosen at all -> still fails fast, never defaults to the
        # openrouter free model.
        _clear_runtime_env(monkeypatch)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime(None)
        with pytest.raises(hc.ProviderConfigError):
            hc._resolve_runtime({})


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

    def test_run_in_demo_mode_only_declares_operator_runtime(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "openai")
        monkeypatch.setenv("FLUXSWARM_DEFAULT_MODEL", "gpt-demo-1")
        captured = {}

        def fake_subprocess_run(cmd, **kw):
            captured["env"] = dict(kw.get("env", {}))
            return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
        hc._run(["boards", "ls"], capture=False)
        assert captured["env"].get("HERMES_DEFAULT_PROVIDER") == "openai"
        assert captured["env"].get("HERMES_DEFAULT_MODEL") == "gpt-demo-1"

    def test_run_in_demo_mode_without_runtime_declares_nothing(self, monkeypatch):
        # No free declaration in Phase 3: unconfigured demo _run passes no
        # provider env (launch paths fail fast earlier, in _resolve_launch_runtime).
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        _clear_runtime_env(monkeypatch)
        captured = {}

        def fake_subprocess_run(cmd, **kw):
            captured["env"] = dict(kw.get("env", {}))
            return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
        hc._run(["boards", "ls"], capture=False)
        assert "HERMES_DEFAULT_PROVIDER" not in captured["env"]
        assert "HERMES_DEFAULT_MODEL" not in captured["env"]


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

    def test_pins_byok_ignoring_free_key(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("FLUXSWARM_MODEL_ANTHROPIC", "claude-byok-2")
        rec = self._set_up(monkeypatch, ["t7"])
        hc._pin_runtime("b", {"anthropic": "sk-ant-x", "opencode-free": "free"})
        assert rec["args"] == ["set-model", "t7", "claude-byok-2", "--provider", "anthropic"]

    def test_pins_operator_default_from_env(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEFAULT_PROVIDER", "anthropic")
        monkeypatch.setenv("FLUXSWARM_DEFAULT_MODEL", "claude-prod-1")
        rec = self._set_up(monkeypatch, ["t9"])
        hc._pin_runtime("b", None)
        assert rec["args"] == ["set-model", "t9", "claude-prod-1", "--provider", "anthropic"]

    def test_failed_set_model_still_raises_loudly(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_MODEL_OPENAI", "gpt-pin-1")

        def fake_run(args, board=None, capture=True, provider_keys=None):
            return type("R", (), {"returncode": 1, "stdout": "{}", "stderr": "boom"})()

        monkeypatch.setattr(hc, "list_tasks", lambda b: [{"id": "t1"}])
        monkeypatch.setattr(hc, "_run", fake_run)
        with pytest.raises(RuntimeError, match="set-model"):
            hc._pin_runtime("b", {"openai": "sk-openai"})


class TestDemoLaunchRuntime:
    def test_demo_launch_uses_requestscoped_runtime_pin(self, monkeypatch):
        """P0.5: a healthy pool pick is threaded as EXPLICIT provider/model kwargs
        (request-scoped pin) — provider_keys stays None (keys never minted) and
        the process-global os.environ is never mutated."""
        import main as main_mod
        seen = {}

        def fake_pick():
            return {"provider": "google", "model": "gemini-1.5-flash",
                    "requires_key": False}

        def fake_launch(board, goal, provider_keys=None, provider=None, model=None):
            seen["keys"] = provider_keys
            seen["provider"] = provider
            seen["model"] = model
            return type("S", (), {
                "root_id": "r1", "worker_ids": [], "verifier_id": "v", "synthesizer_id": "s",
            })()

        def fake_fire(slug, plan, provider_keys=None, pid=None):
            seen["fire_k"] = provider_keys

        monkeypatch.setattr(main_mod.provider_pool, "pick_demo_provider", fake_pick)
        monkeypatch.setattr(main_mod, "_client_ip", lambda request: "127.0.0.1")
        monkeypatch.setattr(main_mod.hc, "ensure_board", lambda slug: True)
        monkeypatch.setattr(main_mod.hc, "launch_swarm", fake_launch)
        monkeypatch.setattr(main_mod, "_fire_dispatch", fake_fire)

        env_before = {
            "FLUXSWARM_DEFAULT_PROVIDER": os.environ.get("FLUXSWARM_DEFAULT_PROVIDER"),
            "FLUXSWARM_DEFAULT_MODEL": os.environ.get("FLUXSWARM_DEFAULT_MODEL"),
        }
        resp = main_mod.api_demo_launch(None)
        assert resp["demo"] is True
        assert seen["keys"] is None
        assert seen["provider"] == "gemini"          # resolve_provider_key("google")
        assert seen["model"] == "gemini-1.5-flash"
        assert seen["fire_k"] is None
        # env-flip regression guard: the demo NEVER pins the runtime via the
        # process-global os.environ (concurrent launches would cross-pollute).
        assert os.environ.get("FLUXSWARM_DEFAULT_PROVIDER") == env_before["FLUXSWARM_DEFAULT_PROVIDER"]
        assert os.environ.get("FLUXSWARM_DEFAULT_MODEL") == env_before["FLUXSWARM_DEFAULT_MODEL"]

    def test_demo_launch_pool_unavailable_returns_structured_body(self, monkeypatch):
        """P0.5: exhausted pool (and no operator default) -> a structured body,
        never a raw 500 or a mutated process env."""
        import main as main_mod
        monkeypatch.setattr(main_mod.provider_pool, "pick_demo_provider", lambda: None)
        monkeypatch.setattr(main_mod, "_client_ip", lambda request: "127.0.0.1")
        monkeypatch.setattr(main_mod.hc, "ensure_board", lambda slug: True)

        resp = main_mod.api_demo_launch(None)
        assert resp["demo"] is True
        assert resp["error"] == "demo_provider_unavailable"
        assert "en" in resp["message"]


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
        # Phase 1: FLUXSWARM_DATABASE_URL is also mandatory in production.
        monkeypatch.setenv("FLUXSWARM_DATABASE_URL", "postgresql://u:p@h/db")
        # Hardening: production must name a real KMS backend (never 'file').
        monkeypatch.setenv("FLUXSWARM_KMS_BACKEND", "aws_kms")
        # Session 2: production also requires at least one PAID provider key.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-prod-test")
        assert envguard.assert_production_secrets() is None

    def test_production_missing_kms_backend_raises(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        monkeypatch.setenv("FLUXSWARM_FERNET_KEY", "a")
        monkeypatch.setenv("FLUXSWARM_JWT_SECRET", "b")
        monkeypatch.setenv("FLUXSWARM_DATABASE_URL", "postgresql://u:p@h/db")
        monkeypatch.delenv("FLUXSWARM_KMS_BACKEND", raising=False)
        with pytest.raises(RuntimeError) as ei:
            envguard.assert_production_secrets()
        assert "FLUXSWARM_KMS_BACKEND" in str(ei.value)

    def test_production_file_kms_forbidden(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        monkeypatch.setenv("FLUXSWARM_FERNET_KEY", "a")
        monkeypatch.setenv("FLUXSWARM_JWT_SECRET", "b")
        monkeypatch.setenv("FLUXSWARM_DATABASE_URL", "postgresql://u:p@h/db")
        monkeypatch.setenv("FLUXSWARM_KMS_BACKEND", "file")
        with pytest.raises(RuntimeError) as ei:
            envguard.assert_production_secrets()
        assert "forbidden" in str(ei.value).lower()

    def test_production_missing_paid_provider_raises(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        monkeypatch.setenv("FLUXSWARM_FERNET_KEY", "a")
        monkeypatch.setenv("FLUXSWARM_JWT_SECRET", "b")
        monkeypatch.setenv("FLUXSWARM_DATABASE_URL", "postgresql://u:p@h/db")
        monkeypatch.setenv("FLUXSWARM_KMS_BACKEND", "aws_kms")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(RuntimeError) as ei:
            envguard.assert_production_secrets()
        assert "paid AI provider" in str(ei.value)

    def test_production_paid_provider_only_ok(self, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_DEMO_MODE", raising=False)
        monkeypatch.setenv("FLUXSWARM_FERNET_KEY", "a")
        monkeypatch.setenv("FLUXSWARM_JWT_SECRET", "b")
        monkeypatch.setenv("FLUXSWARM_DATABASE_URL", "postgresql://u:p@h/db")
        monkeypatch.setenv("FLUXSWARM_KMS_BACKEND", "aws_kms")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-prod-test")
        assert envguard.assert_production_secrets() is None

    def test_demo_mode_never_required(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_DEMO_MODE", "1")
        monkeypatch.delenv("FLUXSWARM_FERNET_KEY", raising=False)
        monkeypatch.delenv("FLUXSWARM_JWT_SECRET", raising=False)
        assert envguard.assert_production_secrets() is None