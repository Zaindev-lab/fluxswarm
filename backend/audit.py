"""Append-only JSONL audit trail.

Records security-relevant events (auth, launches, purchases, key changes) for
post-incident review. Never logs tokens, keys, password hashes, or ciphertext.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
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

# Session 3: log rotation — 100MB per file, 10 files, gzip anything older than
# 7 days. All overridable via env for ops/testing.
_AUDIT_MAX_MB = int(os.environ.get("FLUXSWARM_AUDIT_MAX_MB", "100"))
_AUDIT_MAX_FILES = int(os.environ.get("FLUXSWARM_AUDIT_MAX_FILES", "10"))
_AUDIT_GZIP_AFTER_S = int(os.environ.get("FLUXSWARM_AUDIT_GZIP_AFTER_S", str(7 * 86400)))
_ROTATE_CHECK_INTERVAL_S = 60.0
_last_rotate_check = 0.0

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


def rotate_audit_log(max_mb: int = _AUDIT_MAX_MB, max_files: int = _AUDIT_MAX_FILES,
                     gzip_after_s: int = _AUDIT_GZIP_AFTER_S,
                     audit_file: "Path | None" = None) -> dict:
    """Rotate + prune + gzip the audit trail. Idempotent, never raises.

    * when ``audit_file`` exceeds ``max_mb`` it is shifted to ``.1`` (newest)
      .. ``.<max_files>`` (oldest), the overflow file is dropped;
    * any numbered file older than ``gzip_after_s`` is replaced by a ``.gz``
      sibling (the plain file is deleted), reclaiming disk for hot logs.

    Returns ``{"rotated": bool, "pruned": int, "gzipped": int}`` for tests.
    """
    f = Path(audit_file) if audit_file is not None else AUDIT_FILE
    result = {"rotated": False, "pruned": 0, "gzipped": 0}
    try:
        if f.exists() and f.stat().st_size >= max(1, int(max_mb)) * 1024 * 1024:
            with _lock:
                oldest = Path(str(f) + f".{max(2, int(max_files))}")
                if oldest.exists():
                    oldest.unlink()
                    result["pruned"] += 1
                for i in range(max(2, int(max_files)) - 1, 0, -1):
                    src = Path(str(f) + f".{i}")
                    if src.exists():
                        src.rename(Path(str(f) + f".{i + 1}"))
                f.rename(Path(str(f) + ".1"))
                result["rotated"] = True
        now = time.time()
        with _lock:
            for i in range(1, int(max_files) + 1):
                src = Path(str(f) + f".{i}")
                if not src.exists():
                    continue
                if now - src.stat().st_mtime > int(gzip_after_s):
                    gz = Path(str(src) + ".gz")
                    with src.open("rb") as fin, gzip.open(gz, "wb", compresslevel=6) as fout:
                        shutil.copyfileobj(fin, fout)
                    src.unlink()
                    result["gzipped"] += 1
    except Exception:
        pass
    return result


def _maybe_rotate() -> None:
    """Throttled rotation check (once per 60s) so the hot audit path stays cheap."""
    global _last_rotate_check
    now = time.time()
    if now - _last_rotate_check < _ROTATE_CHECK_INTERVAL_S:
        return
    _last_rotate_check = now
    rotate_audit_log()


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
        _maybe_rotate()
    except Exception:
        pass