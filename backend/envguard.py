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

    No ephemeral secrets in production: FLUXSWARM_FERNET_KEY (legacy BYOK blob
    decryption at rest), FLUXSWARM_DATABASE_URL (PostgreSQL endpoint, Phase 1
    overhaul), FLUXSWARM_JWT_SECRET (token signing) and FLUXSWARM_KMS_BACKEND
    (must be a real KMS — the hermetic ``file`` backend is forbidden in
    production) must be set by the operator. An env var that is present but
    empty counts as missing.
    """
    if is_demo_mode():
        return
    missing = [
        name
        for name in (
            "FLUXSWARM_FERNET_KEY",
            "FLUXSWARM_DATABASE_URL",
            "FLUXSWARM_JWT_SECRET",
            "FLUXSWARM_KMS_BACKEND",
        )
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise RuntimeError(
            "production startup refused: missing required secrets: "
            + ", ".join(missing)
            + ". Set them in the environment (FLUXSWARM_DEMO_MODE=1 is only for "
            "development/demo; production never uses ephemeral secrets, and "
            "FLUXSWARM_KMS_BACKEND must name a real KMS backend, never 'file')."
        )
    if os.environ.get("FLUXSWARM_KMS_BACKEND", "").strip().lower() == "file":
        raise RuntimeError(
            "production startup refused: FLUXSWARM_KMS_BACKEND=file is forbidden "
            "in production (hermetic KEK). Use aws_kms, azure_keyvault or "
            "hashicorp_vault."
        )
    # Session 2: production must run on a PAID provider. The demo surface gets its
    # zero-cost runtime from provider_pool (keys still required per entry), so a
    # production deployment without a paid key is refused instead of silently
    # defaulting anywhere.
    if not any(
        os.environ.get(k, "").strip()
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")
    ):
        raise RuntimeError(
            "production startup refused: at least one paid AI provider API key is "
            "required (set ANTHROPIC_API_KEY, OPENAI_API_KEY or GOOGLE_API_KEY). "
            "Demo/free defaults are disabled outside demo mode."
        )