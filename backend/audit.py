"""Append-only JSONL audit trail.

Records security-relevant events (auth, launches, purchases, key changes) for
post-incident review. Never logs tokens, keys, password hashes, or ciphertext.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
AUDIT_FILE = BASE / "data" / "audit.jsonl"

_lock = threading.Lock()

# Cap a single audit record so one enormous meta blob (e.g. a multi-KB error
# string) cannot render the whole audit.jsonl unparseable for readers that
# load it line-by-line. Truncation keeps the record VALID JSON (we trim values,
# never slice the serialized string).
_MAX_RECORD_BYTES = 4000
_MAX_VALUE_CHARS = 1024

_SENSITIVE_KEYS = {
    "token", "key", "secret", "password", "pw_hash", "cipher", "raw",
    "api_key", "access_token", "refresh_token", "client_secret", "client_key",
    "webhook_secret",
}

# Core keys always retained in the (rare) minimal-fallback record.
_CORE_KEYS = ("ts", "event", "outcome", "uid", "email", "ip")


def _truncate_values(rec: dict) -> dict:
    """Trim any over-long string value so the record serializes compactly."""
    out = {}
    for k, v in rec.items():
        if isinstance(v, str) and len(v) > _MAX_VALUE_CHARS:
            v = v[:_MAX_VALUE_CHARS] + "…"
        out[k] = v
    return out


def _minimal_record(rec: dict) -> dict:
    """Last resort: keep only core identity keys + a truncation marker."""
    return {k: rec[k] for k in _CORE_KEYS if k in rec} | {"truncated": True}


def audit(event: str, *, uid=None, email=None, ip=None, outcome="ok", **meta) -> None:
    """Append one audit record. Failures are swallowed: auditing never breaks a request."""
    try:
        rec: dict = {"ts": round(time.time(), 3), "event": event, "outcome": outcome}
        if uid is not None:
            rec["uid"] = uid
        if email is not None:
            rec["email"] = email.lower()
        if ip is not None:
            rec["ip"] = ip
        for k, v in meta.items():
            if k.lower() in _SENSITIVE_KEYS:
                continue
            rec[k] = v
        rec = _truncate_values(rec)
        line = json.dumps(rec, ensure_ascii=False)
        # Guard file health: a single monster line should not poison the trail.
        # Trim values until it fits; if still too big, emit a minimal valid record.
        if len(line.encode("utf-8")) > _MAX_RECORD_BYTES:
            line = json.dumps(_minimal_record(rec), ensure_ascii=False)
        with _lock:
            with AUDIT_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass