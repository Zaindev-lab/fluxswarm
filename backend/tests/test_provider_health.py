"""Provider health-check tests — 12 configurations.

Covers: provider success/timeout, HTTP failure, model unavailable,
invalid credentials, missing config, production mode, BYOK, credit refund,
recovery, and no silent fallback (Phase 3: no free tier).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from provider import ProviderHealth, ProviderStatus, check_provider_health
from hermes_client import _default_runtime, _resolve_runtime, ProviderConfigError


# ---------------------------------------------------------------------------
# 1. provider success
# ---------------------------------------------------------------------------
class TestProviderSuccess:
    @patch("provider.httpx.Client")
    def test_success(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "{}"
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        h = check_provider_health("openai", model="gpt-4", credential="sk-test")
        assert h.status == ProviderStatus.SUCCESS
        assert h.provider == "openai"
        assert h.model == "gpt-4"
        assert h.latency_ms >= 0

    @patch("provider.httpx.Client")
    def test_success_sends_bearer_auth(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        check_provider_health("openai", model="gpt-4", credential="sk-test")

        called_url, called_kwargs = mock_client.get.call_args
        assert "api.openai.com" in called_url[0]
        assert called_kwargs["headers"]["Authorization"] == "Bearer sk-test"

    @patch("provider.httpx.Client")
    def test_openrouter_success_sends_bearer_auth(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        h = check_provider_health("openrouter", model="z-ai/glm-5.2:free", credential="sk-or-test")
        assert h.status == ProviderStatus.SUCCESS
        assert h.provider == "openrouter"
        assert h.model == "z-ai/glm-5.2:free"

        called_url, called_kwargs = mock_client.get.call_args
        assert "openrouter.ai" in called_url[0]
        assert called_kwargs["headers"]["Authorization"] == "Bearer sk-or-test"

    @patch("provider.httpx.Client")
    def test_free_provider_is_unknown_provider(self, MockClient):
        """Phase 3 removed the free tier: 'opencode-free' is not a provider."""
        h = check_provider_health("opencode-free", model="big-pickle", credential="free")
        assert h.status == ProviderStatus.CONFIG_ERROR
        assert "unknown provider" in h.detail.lower()


# ---------------------------------------------------------------------------
# 2. provider timeout
# ---------------------------------------------------------------------------
class TestProviderTimeout:
    @patch("provider.httpx.Client")
    def test_timeout(self, MockClient):
        import httpx
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.TimeoutException("timed out")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        h = check_provider_health("openai", model="gpt-4", credential="sk-test", timeout_s=5.0)
        assert h.status == ProviderStatus.TIMEOUT
        assert "timeout" in h.detail.lower()


# ---------------------------------------------------------------------------
# 3. provider HTTP failure (502, 503, 429)
# ---------------------------------------------------------------------------
class TestProviderHTTPFailure:
    @pytest.mark.parametrize("code", [502, 503, 429])
    @patch("provider.httpx.Client")
    def test_http_failure(self, MockClient, code):
        mock_resp = MagicMock()
        mock_resp.status_code = code
        mock_resp.text = "server error"
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        h = check_provider_health("openai", model="gpt-4", credential="sk-test")
        assert h.status == ProviderStatus.PROVIDER_UNAVAILABLE
        assert str(code) in h.detail


# ---------------------------------------------------------------------------
# 4. model unavailable
# ---------------------------------------------------------------------------
class TestModelUnavailable:
    @patch("provider.httpx.Client")
    def test_model_not_found(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = '{"error": {"message": "model not found"}}'
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        h = check_provider_health("openai", model="gpt-nonexistent", credential="sk-test")
        assert h.status == ProviderStatus.MODEL_UNAVAILABLE
        assert "model not found" in h.detail.lower()


# ---------------------------------------------------------------------------
# 5. invalid credentials (401, 403)
# ---------------------------------------------------------------------------
class TestInvalidCredentials:
    @pytest.mark.parametrize("code", [401, 403])
    @patch("provider.httpx.Client")
    def test_auth_error(self, MockClient, code):
        mock_resp = MagicMock()
        mock_resp.status_code = code
        mock_resp.text = "unauthorized"
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        h = check_provider_health("anthropic", model="claude-3", credential="bad-key")
        assert h.status == ProviderStatus.AUTH_ERROR
        assert str(code) in h.detail


# ---------------------------------------------------------------------------
# 6. provider configuration missing (unknown provider)
# ---------------------------------------------------------------------------
class TestProviderConfigMissing:
    def test_unknown_provider(self):
        h = check_provider_health("nonexistent-provider", model="some-model")
        assert h.status == ProviderStatus.CONFIG_ERROR
        assert "unknown provider" in h.detail.lower()

    def test_no_model(self):
        h = check_provider_health("openai", model="")
        assert h.status == ProviderStatus.CONFIG_ERROR
        assert "no model" in h.detail.lower()


# ---------------------------------------------------------------------------
# 7. production mode without a configured runtime (no silent fallback)
# ---------------------------------------------------------------------------
class TestProductionModeNoRuntime:
    @patch.dict(os.environ, {"FLUXSWARM_DEFAULT_PROVIDER": "", "FLUXSWARM_DEMO_MODE": ""}, clear=False)
    def test_unconfigured_production_raises(self):
        with pytest.raises(ProviderConfigError):
            _default_runtime()

    @patch.dict(os.environ, {"FLUXSWARM_DEMO_MODE": "1", "FLUXSWARM_DEFAULT_PROVIDER": ""}, clear=False)
    def test_demo_mode_without_operator_provider_raises(self):
        """Phase 3: demo mode has NO free fallback — the operator must configure
        a real runtime (FLUXSWARM_DEFAULT_PROVIDER) for the demo to work."""
        with pytest.raises(ProviderConfigError):
            _default_runtime()

    @patch.dict(os.environ, {
        "FLUXSWARM_DEMO_MODE": "1",
        "FLUXSWARM_DEFAULT_PROVIDER": "openai",
        "FLUXSWARM_DEFAULT_MODEL": "gpt-4",
    }, clear=False)
    def test_demo_mode_uses_operator_runtime(self):
        model, provider = _default_runtime()
        assert provider == "openai"
        assert model == "gpt-4"


# ---------------------------------------------------------------------------
# 8. production mode with explicit provider
# ---------------------------------------------------------------------------
class TestProductionModeExplicitProvider:
    @patch.dict(os.environ, {
        "FLUXSWARM_DEFAULT_PROVIDER": "anthropic",
        "FLUXSWARM_DEFAULT_MODEL": "claude-3-sonnet",
    }, clear=False)
    def test_explicit_provider_resolves(self):
        model, provider = _default_runtime()
        assert provider == "anthropic"
        assert model == "claude-3-sonnet"

    @patch.dict(os.environ, {
        "FLUXSWARM_DEFAULT_PROVIDER": "openai",
        "FLUXSWARM_MODEL_OPENAI": "gpt-4-turbo",
    }, clear=False)
    def test_per_provider_model_override(self):
        model, provider = _default_runtime()
        assert provider == "openai"
        assert model == "gpt-4-turbo"


# ---------------------------------------------------------------------------
# 9. BYOK resolution
# ---------------------------------------------------------------------------
class TestBYOKResolution:
    def test_free_key_is_ignored_for_operator_default(self):
        with patch.dict(os.environ, {
            "FLUXSWARM_DEFAULT_PROVIDER": "openai",
            "FLUXSWARM_DEFAULT_MODEL": "gpt-4",
        }, clear=False):
            # "opencode-free" is not a provider in Phase 3 — ignored entirely.
            model, provider = _resolve_runtime({"opencode-free": "free", "openai": "sk-test"})
            assert provider == "openai"
            assert model is None  # BYOK key wins over the operator default

    def test_unknown_free_only_uses_operator_default(self):
        with patch.dict(os.environ, {
            "FLUXSWARM_DEFAULT_PROVIDER": "gemini",
            "FLUXSWARM_MODEL_GEMINI": "gemini-2.0-flash",
        }, clear=False):
            model, provider = _resolve_runtime({"opencode-free": "free"})
            assert provider == "gemini"
            assert model == "gemini-2.0-flash"

    def test_paid_byok_resolves(self):
        keys = {"openai": "sk-test"}
        model, provider = _resolve_runtime(keys)
        assert provider == "openai"
        assert model is None  # model resolved at pin time


# ---------------------------------------------------------------------------
# 10. provider failure + credit refund
# ---------------------------------------------------------------------------
class TestProviderFailureCreditRefund:
    @patch("provider.httpx.Client")
    def test_connection_error(self, MockClient):
        import httpx
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ConnectError("connection refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        h = check_provider_health("openai", model="gpt-4", credential="sk-test")
        assert h.status == ProviderStatus.PROVIDER_UNAVAILABLE
        assert "connection failed" in h.detail.lower()


# ---------------------------------------------------------------------------
# 11. provider recovery
# ---------------------------------------------------------------------------
class TestProviderRecovery:
    @patch("provider.httpx.Client")
    def test_retry_after_failure(self, MockClient):
        """Simulate: first call fails, second succeeds."""
        import httpx
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200

        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 503
        mock_resp_fail.text = "service unavailable"

        mock_client = MagicMock()
        mock_client.get.side_effect = [mock_resp_fail, mock_resp_ok]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        h1 = check_provider_health("openai", model="gpt-4", credential="sk-test")
        assert h1.status == ProviderStatus.PROVIDER_UNAVAILABLE

        h2 = check_provider_health("openai", model="gpt-4", credential="sk-test")
        assert h2.status == ProviderStatus.SUCCESS


# ---------------------------------------------------------------------------
# 12. no silent fallback
# ---------------------------------------------------------------------------
class TestNoSilentFallback:
    @patch.dict(os.environ, {"FLUXSWARM_DEFAULT_PROVIDER": "", "FLUXSWARM_DEMO_MODE": ""}, clear=False)
    def test_launch_fails_fast(self):
        """Dispatch must fail fast in unconfigured production, never silently use free."""
        import hermes_client as hc
        with pytest.raises((ProviderConfigError, RuntimeError)):
            hc._resolve_launch_runtime(None)

    @patch.dict(os.environ, {"FLUXSWARM_DEFAULT_PROVIDER": "", "FLUXSWARM_DEMO_MODE": ""}, clear=False)
    def test_preflight_provider_returns_config_error(self):
        """preflight_provider must return CONFIG_ERROR in unconfigured production."""
        import hermes_client as hc
        health = hc.preflight_provider(None)
        assert health.status == ProviderStatus.CONFIG_ERROR
