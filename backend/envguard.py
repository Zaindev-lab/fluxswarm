"""Fail-fast environment guards for FluxSwarm production startup.

Evaluated BEFORE the app modules that load secrets (auth/vault) so a production
deployment with missing required secrets refuses to start with one clear error
instead of silently degrading (e.g. an ephemeral Fernet key that would make all
stored BYOK keys undecryptable after restart).
"""
from __future__ import annotations

import os

_DEMO_FLAGS = ("1", "true", "yes")


def is_demo_mode() -> bool:
    """True when the operator explicitly runs the Demo/dev mode.

    Demo mode is the ONLY place ephemeral/generated secrets are acceptable.
    """
    return os.environ.get("FLUXSWARM_DEMO_MODE", "").strip().lower() in _DEMO_FLAGS


def assert_production_secrets() -> None:
    """Raise at startup when required secrets are missing outside demo mode.

    No ephemeral secrets in production: both FLUXSWARM_FERNET_KEY (BYOK key
    encryption at rest) and FLUXSWARM_JWT_SECRET (token signing) must be set by
    the operator. An env var that is present but empty counts as missing.
    """
    if is_demo_mode():
        return
    missing = [
        name
        for name in ("FLUXSWARM_FERNET_KEY", "FLUXSWARM_JWT_SECRET")
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise RuntimeError(
            "production startup refused: missing required secrets: "
            + ", ".join(missing)
            + ". Set them in the environment (FLUXSWARM_DEMO_MODE=1 is only for "
            "development/demo; production never uses ephemeral secrets)."
        )