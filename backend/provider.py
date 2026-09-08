"""Provider health-check and configuration abstraction.

Lightweight preflight that distinguishes:
  CONFIG_ERROR   – provider not configured
  AUTH_ERROR     – credentials invalid or missing
  PROVIDER_UNAVAILABLE – provider endpoint unreachable
  TIMEOUT        – provider did not respond in time
  MODEL_UNAVAILABLE – model name not recognised by provider
  SUCCESS        – provider is reachable and healthy

The check must NOT consume credits or start real swarm work.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx


class ProviderStatus(str, Enum):
    CONFIG_ERROR = "config_error"
    AUTH_ERROR = "auth_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    MODEL_UNAVAILABLE = "model_unavailable"
    SUCCESS = "success"


@dataclass(frozen=True)
class ProviderHealth:
    status: ProviderStatus
    provider: str
    model: Optional[str] = None
    detail: str = ""
    latency_ms: float = 0.0


# Known provider base URLs (OpenAI-compatible chat endpoints).
# These are lightweight /models or /chat/completions probes — NOT full completions.
#
# Phase 3: no anonymous/free-tier-without-a-key provider exists. Every runtime
# requires a real, user-supplied (BYOK) or operator-configured credential — the
# OpenRouter free tier (``:free`` models) still requires an API key and the
# user's provider agreement. A deployment without one fails fast instead of
# silently routing work anywhere.
_PROVIDER_ENDPOINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "kimi": "https://api.moonshot.cn/v1/models",
    # OpenRouter is an OpenAI-compatible aggregator; its free-tier models
    # (suffix ``:free``, e.g. z-ai/glm-5.2:free) are the supported way to
    # trial the platform at zero cost. The /models endpoint honours the same
    # Bearer auth as any OpenAI-compatible host.
    "openrouter": "https://openrouter.ai/api/v1/models",
}

_PROVIDER_ENV_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "kimi": "KIMI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# Phase G: Gemini credentials accept the legacy Google key name as an alias so
# an operator who configured GOOGLE_API_KEY (instead of the canonical
# GEMINI_API_KEY) is not silently treated as unconfigured.
_PROVIDER_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "gemini": ("GOOGLE_API_KEY",),
}

def check_provider_health(
    provider: str,
    model: Optional[str] = None,
    credential: Optional[str] = None,
    timeout_s: float = 10.0,
) -> ProviderHealth:
    """Check if a provider is reachable and configured correctly.

    This is a lightweight probe — it does NOT run a full completion or
    consume credits. It hits the /models endpoint
    (which is typically free and rate-limit-friendly).

    Returns a ProviderHealth with the diagnostic status.
    """
    t0 = time.monotonic()

    # --- CONFIG_ERROR: provider not recognised ---
    if provider not in _PROVIDER_ENDPOINTS and provider not in _PROVIDER_ENDPOINTS.values():
        return ProviderHealth(
            status=ProviderStatus.CONFIG_ERROR,
            provider=provider,
            model=model,
            detail=f"unknown provider: {provider!r}",
        )

    # --- CONFIG_ERROR: model not specified ---
    if not model:
        return ProviderHealth(
            status=ProviderStatus.CONFIG_ERROR,
            provider=provider,
            detail="no model specified",
        )

    # --- AUTH_ERROR: credential missing or invalid ---
    # Phase 3: every provider needs a real credential — there is no free tier.
    env_key = _PROVIDER_ENV_KEYS.get(provider)
    cred = credential
    if not cred and env_key:
        cred = os.environ.get(env_key, "")
        if not cred:
            for alias in _PROVIDER_ENV_ALIASES.get(provider, ()):
                cred = os.environ.get(alias, "")
                if cred:
                    break
    if not cred:
        return ProviderHealth(
            status=ProviderStatus.AUTH_ERROR,
            provider=provider,
            model=model,
            detail=f"no credential for {provider} (set {env_key or 'BYOK key'})",
        )

    # --- Build the probe request ---
    endpoint = _PROVIDER_ENDPOINTS.get(provider, "")
    if not endpoint:
        return ProviderHealth(
            status=ProviderStatus.CONFIG_ERROR,
            provider=provider,
            model=model,
            detail=f"no endpoint configured for {provider!r}",
        )

    headers: dict[str, str] = {}
    if provider == "anthropic":
        headers = {
            "x-api-key": cred,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    elif provider in ("openai", "kimi", "openrouter"):
        headers = {"Authorization": f"Bearer {cred}"}
    elif provider == "gemini":
        # Gemini uses query param auth; the /models endpoint is publicly listable
        # with an API key.
        endpoint = f"{endpoint}?key={cred}"

    # --- Probe ---
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(endpoint, headers=headers)
    except httpx.TimeoutException:
        latency = (time.monotonic() - t0) * 1000
        return ProviderHealth(
            status=ProviderStatus.TIMEOUT,
            provider=provider,
            model=model,
            detail=f"timeout after {timeout_s:.0f}s",
            latency_ms=latency,
        )
    except httpx.ConnectError as exc:
        latency = (time.monotonic() - t0) * 1000
        return ProviderHealth(
            status=ProviderStatus.PROVIDER_UNAVAILABLE,
            provider=provider,
            model=model,
            detail=f"connection failed: {exc}",
            latency_ms=latency,
        )
    except Exception as exc:
        latency = (time.monotonic() - t0) * 1000
        return ProviderHealth(
            status=ProviderStatus.PROVIDER_UNAVAILABLE,
            provider=provider,
            model=model,
            detail=f"request failed: {exc}",
            latency_ms=latency,
        )

    latency = (time.monotonic() - t0) * 1000

    # --- Interpret response ---
    if resp.status_code in (401, 403):
        return ProviderHealth(
            status=ProviderStatus.AUTH_ERROR,
            provider=provider,
            model=model,
            detail=f"HTTP {resp.status_code}: authentication failed",
            latency_ms=latency,
        )

    if resp.status_code in (502, 503, 504):
        return ProviderHealth(
            status=ProviderStatus.PROVIDER_UNAVAILABLE,
            provider=provider,
            model=model,
            detail=f"HTTP {resp.status_code}: provider unavailable",
            latency_ms=latency,
        )

    if resp.status_code == 429:
        return ProviderHealth(
            status=ProviderStatus.PROVIDER_UNAVAILABLE,
            provider=provider,
            model=model,
            detail="HTTP 429: rate limited",
            latency_ms=latency,
        )

    if resp.status_code >= 400:
        # Other client errors — could be model not found, bad request, etc.
        body = resp.text[:200] if resp.text else ""
        if "model" in body.lower() and ("not found" in body.lower() or "unknown" in body.lower()):
            return ProviderHealth(
                status=ProviderStatus.MODEL_UNAVAILABLE,
                provider=provider,
                model=model,
                detail=f"HTTP {resp.status_code}: model not found",
                latency_ms=latency,
            )
        return ProviderHealth(
            status=ProviderStatus.PROVIDER_UNAVAILABLE,
            provider=provider,
            model=model,
            detail=f"HTTP {resp.status_code}: {body[:100]}",
            latency_ms=latency,
        )

    # --- SUCCESS: endpoint reachable, auth accepted ---
    return ProviderHealth(
        status=ProviderStatus.SUCCESS,
        provider=provider,
        model=model,
        detail=f"HTTP {resp.status_code}",
        latency_ms=latency,
    )
