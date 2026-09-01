"""JWT helpers for FluxSwarm auth."""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

import jwt

BASE = Path(__file__).resolve().parent
_SECRET_FILE = BASE / "data" / ".jwt_secret"

ALGO = "HS256"
EXP_SECONDS = 60 * 60 * 24 * 7  # 7 days

_DEMO_FLAGS = ("1", "true", "yes")


def _is_demo_mode() -> bool:
    return os.environ.get("FLUXSWARM_DEMO_MODE", "").strip().lower() in _DEMO_FLAGS


def _load_secret() -> str:
    """Never keep a hidden default outside demo mode.

    Demo/dev priority: FLUXSWARM_JWT_SECRET env var -> persisted secret file ->
    generate-and-persist -> (OSError) ephemeral. Production fail-fast: if
    FLUXSWARM_JWT_SECRET is missing we refuse to start, because a generated or
    ephemeral signing secret would invalidate every session after a restart.
    Existing sessions signed with any previous value become invalid (which is
    exactly what we want after a leak).
    """
    secret = os.environ.get("FLUXSWARM_JWT_SECRET")
    if secret:
        return secret
    if not _is_demo_mode():
        raise RuntimeError(
            "FLUXSWARM_JWT_SECRET is required outside demo mode. Refusing to "
            "start with a generated or ephemeral signing secret (no ephemeral "
            "secrets in production). Set FLUXSWARM_JWT_SECRET, or set "
            "FLUXSWARM_DEMO_MODE=1 for development/demo only."
        )
    try:
        _SECRET_FILE.parent.mkdir(exist_ok=True)
        if _SECRET_FILE.exists():
            return _SECRET_FILE.read_text().strip()
        secret = secrets.token_urlsafe(48)
        _SECRET_FILE.write_text(secret)
        return secret
    except OSError:
        # Last resort: ephemeral secret (sessions do not survive restart).
        return secrets.token_urlsafe(48)


SECRET = _load_secret()


def make_token(user: dict) -> str:
    payload = {
        "uid": user["id"],
        "email": user["email"],
        "plan": user["plan"],
        # Float epoch: the logged_out_at gate compares iat against a float
        # timestamp, so a token minted AFTER logout (even in the same second)
        # must carry a strictly-later iat, while a pre-logout token keeps an
        # earlier one. Integer seconds cannot distinguish those two cases.
        "iat": time.time(),
        "exp": int(time.time()) + EXP_SECONDS,
    }
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def decode_token(token: str) -> dict | None:
    """Verify a JWT and reject any token whose header algorithm is not the
    configured HS256. This is the security-critical check: a token claiming
    ``alg: none`` or any asymmetric algorithm must be refused, never decoded
    with a public key. jwt.decode scopes algorithms explicitly, so the library
    already rejects unknown algorithms; we keep the call here for clarity and
    to centralise decoding.
    """
    try:
        return jwt.decode(token, SECRET, algorithms=[ALGO])
    except Exception:
        return None
