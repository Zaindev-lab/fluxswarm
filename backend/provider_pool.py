"""Demo provider pool (FLUXSWARM_DEMO_MODE=1 only).

Session 2 (Compliance & Demo Protection): production no longer defaults to a
single zero-cost OpenRouter free model. The demo surface instead tries a small
pool of free-tier providers, in order, and picks the first that passes a
lightweight health probe. When the whole pool is unavailable the caller falls
back to the operator-configured runtime (env) or fails fast — providers are
NEVER probed with real completions here.

Every entry that `requires_key` reads its key from the named env var; without a
key the entry is skipped before any network probe (no pointless AUTH_ERROR
round-trips). `provider` matches an endpoint known to provider.py; the prompt
spelled the first entry "google" (Gemini's provider evolution), which is mapped
to the provider key "gemini" at probe time — CanonicalNames stay in the pool.
"""
from __future__ import annotations

import os
from typing import Optional

from provider import ProviderStatus, check_provider_health

# Provider -> probe key in provider.py's endpoint table (name drift handled here).
_PROBE_KEY = {"google": "gemini"}

DEMO_PROVIDERS = [
    {"provider": "google", "model": "gemini-1.5-flash", "requires_key": False},
    {"provider": "openrouter", "model": "google/gemini-flash-1.5:free",
     "requires_key": True, "key_env": "OPENROUTER_API_KEY"},
    {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct:free",
     "requires_key": True, "key_env": "OPENROUTER_API_KEY"},
]


class ProviderUnavailableError(RuntimeError):
    """Raised when the demo provider pool is exhausted (all entries unhealthy)."""


def _probe_provider(provider: str, model: str) -> bool:
    """Health-probe one pool entry without consuming credits."""
    probe_key = _PROBE_KEY.get(provider, provider)
    health = check_provider_health(probe_key, model)
    return health.status == ProviderStatus.SUCCESS


def _entry_keyed(entry: dict) -> bool:
    """A requires_key entry only counts when its env key is present."""
    key_env = entry.get("key_env")
    if not entry.get("requires_key") or not key_env:
        return True
    return bool(os.environ.get(key_env, "").strip())


def get_demo_provider() -> dict:
    """Return the first available demo provider (dict literal from DEMO_PROVIDERS).

    Synchronous core: provider.py's health probe is sync and the demo launch
    endpoint is sync; `get_demo_provider_async` wraps this for async callers.
    """
    for entry in DEMO_PROVIDERS:
        if not _entry_keyed(entry):
            continue
        if _probe_provider(entry["provider"], entry["model"]):
            return entry
    raise ProviderUnavailableError("All demo providers exhausted")


async def get_demo_provider_async() -> dict:
    """Async shim over get_demo_provider (the prompt's ``await rat`` API)."""
    return get_demo_provider()


def resolve_provider_key(provider: str) -> str:
    """Map a pool provider name to the provider.py runtime key (""google"" -> ``gemini``)."""
    return _PROBE_KEY.get(provider, provider)


def pick_demo_provider() -> Optional[dict]:
    """Safe wrapper: never raises — None means "fall back to operator runtime"."""
    try:
        return get_demo_provider()
    except ProviderUnavailableError:
        return None
    except Exception:
        return None