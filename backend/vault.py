"""
FluxSwarm BYOK vault.

Stores user-supplied provider API keys encrypted at rest using KMS envelope
encryption (see ``kms_client``). Each key blob is sealed under a fresh per-blob
DEK (AES-256-GCM); the DEK is wrapped by a KEK held by the configured KMS
(``FLUXSWARM_KMS_BACKEND`` = file | aws_kms | azure_keyvault | hashicorp_vault).
Attacking the store alone therefore yields nothing without the KMS.

Keys are decrypted only when launching that user's squad and injected into the
Hermes subprocess (or sandbox container) environment. FluxSwarm itself never
pays for tokens in BYOK mode.

Legacy blobs (written by previous versions with Fernet) are still readable: the
legacy Fernet payload is detected and decrypted with ``FLUXSWARM_FERNET_KEY``
(or the OS-user key file), so already-stored BYOK keys keep working.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet
import kms_client

_log = logging.getLogger("vault")

BASE = Path(__file__).resolve().parent
_LEGACY_KEY_FILE = BASE / "data" / ".fernet_key"
_KEY_HOST = Path.home() / ".fluxswarm"
_KEY_FILE = _KEY_HOST / "fernet.key"

_DEMO_FLAGS = ("1", "true", "yes")


def _is_demo_mode() -> bool:
    return os.environ.get("FLUXSWARM_DEMO_MODE", "").strip().lower() in _DEMO_FLAGS


def _load_key() -> bytes:
    raw = os.environ.get("FLUXSWARM_FERNET_KEY", "").strip()
    if raw:
        return raw.encode()
    if not _is_demo_mode():
        # Production fail-fast: a missing Fernet key must not silently fall back
        # to a generated/ephemeral key — that would make every stored BYOK key
        # undecryptable after a restart. Demo/dev keeps the file-based fallback.
        raise RuntimeError(
            "FLUXSWARM_FERNET_KEY is required outside demo mode. Refusing to "
            "start with a generated or ephemeral Fernet key (no ephemeral "
            "secrets in production). Set FLUXSWARM_FERNET_KEY, or set "
            "FLUXSWARM_DEMO_MODE=1 for development/demo only."
        )
    try:
        _KEY_HOST.mkdir(parents=True, exist_ok=True)
        if _KEY_FILE.exists():
            return _KEY_FILE.read_text().strip().encode()
        if _LEGACY_KEY_FILE.exists():
            # One-time migration: preserve the value (so existing blobs decrypt),
            # relocate it out of the data dir, then delete the plaintext copy.
            secret = _LEGACY_KEY_FILE.read_text().strip()
            _KEY_FILE.write_text(secret)
            try:
                _LEGACY_KEY_FILE.unlink()
            except OSError:
                pass
            return secret.encode()
        secret = Fernet.generate_key().decode()
        _KEY_FILE.write_text(secret)
        return secret.encode()
    except Exception:
        # Ephemeral fallback: encrypts with a throwaway key (does not persist).
        return Fernet.generate_key()

# The KMS backend that seals new/current BYOK blobs.
_KMS = kms_client.get_client()

# Legacy Fernet (pre-KMS) key for decrypting old blobs only.
_FERNET = Fernet(_load_key())


def _is_envelope(blob: str) -> bool:
    return bool(blob) and blob.lstrip().startswith("{") and '"wrapped_dek"' in blob


# Per-user key storage: user_id -> {provider: encrypted_token}
_STORE = Path(os.environ.get("FLUXSWARM_BYOK_STORE", str(BASE / "data" / "byok.json")))


def _load() -> dict:
    if _STORE.exists():
        import json
        return json.loads(_STORE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict):
    import json
    _STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_user_key(user_id: int, provider: str, token: str):
    """Encrypt and store a user's provider token (provider: anthropic|openai|gemini|kimi|openrouter)."""
    data = _load()
    uid = str(user_id)
    env_blob = _KMS.encrypt(token.encode("utf-8"))
    data.setdefault(uid, {})[provider] = env_blob
    _save(data)


def get_user_key(user_id: int, provider: str) -> str | None:
    data = _load()
    enc = data.get(str(user_id), {}).get(provider)
    if not enc:
        return None
    # Current envelope format (KMS). Fall back to legacy Fernet for blobs
    # written before the Phase 3 KMS overhaul.
    try:
        if _is_envelope(enc):
            return _KMS.decrypt(enc).decode("utf-8")
        return _FERNET.decrypt(enc.encode()).decode("utf-8")
    except Exception:
        # Never silently ship an empty/corrupt key upstream (F6/FIX-4): log the
        # condition loudly so ops can detect key-rotation or store corruption.
        _log.warning("vault.decrypt failed for user %s provider %s", user_id, provider)
        return None


def user_has_key(user_id: int) -> bool:
    data = _load()
    return bool(data.get(str(user_id)))


def delete_user_key(user_id: int) -> None:
    """Permanently remove a user's encrypted provider keys (CCPA right to erasure)."""
    data = _load()
    if data.pop(str(user_id), None):
        _save(data)


def mask_key(token: str) -> str:
    if not token or len(token) < 8:
        return "****"
    return token[:4] + "…" + token[-4:]