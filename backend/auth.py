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


def _load_secret() -> str:
    """Never keep a hardcoded default.

    Priority: FLUXSWARM_JWT_SECRET env var -> persisted secret file ->
    generate-and-persist. Existing sessions signed with any previous value
    become invalid (which is exactly what we want after a leak).
    """
    secret = os.environ.get("FLUXSWARM_JWT_SECRET")
    if secret:
        return secret
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
