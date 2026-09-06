"""Shared pytest setup for FluxSwarm backend tests.

CRITICAL: db.py / vault.py / audit.py open their storage files at IMPORT time
and db.py seeds the production database on first import. To keep tests from ever
touching the live C:\\Users\\DELL\\fluxswarm data, we (a) point db.py at a temp
DB via FLUXSWARM_DB BEFORE importing it, and (b) repaint vault/audit globals to
temp paths, then import all modules so every test uses the isolated copies.

We also set FLUXSWARM_ALLOW_MULTI=1 so importing ``main`` (which calls
serverlock.acquire()) does not delete or fight the production lock file held by
the running backend.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("FLUXSWARM_ALLOW_MULTI", "1")
# The test session runs in Demo/dev mode: ephemeral/generated secrets are
# allowed. Production-mode behaviors (missing-secret failure,
# unconfigured-runtime failure — Phase 3 removed the free fallback) are covered
# by dedicated tests that toggle these env vars via monkeypatch.
os.environ.setdefault("FLUXSWARM_DEMO_MODE", "1")
# Phase 3: pin the hermetic file-KMS backend to a fixed test passphrase so the
# suite never reads/writes the operator's real ~/.fluxswarm/kms_file.key.
os.environ.setdefault("FLUXSWARM_KMS_BACKEND", "file")
os.environ.setdefault("FLUXSWARM_KMS_FILE_KEY", "fluxswarm-test-kms-passphrase-not-secret")
# Shared-secret bearer for the /api/admin/* surface (deny-by-default when unset).
os.environ.setdefault("FLUXSWARM_ADMIN_TOKEN", "test-admin-token-not-secret")

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Isolated temp store for the whole test session. db.py honours FLUXSWARM_DB at
# import time, so set it BEFORE importing the module.
_TMP = Path(tempfile.mkdtemp(prefix="fluxswarm_test_"))
(_TMP / "data").mkdir(exist_ok=True)
os.environ["FLUXSWARM_DB"] = str(_TMP / "data" / "users.db")

import db as db_mod  # noqa: E402
import vault as vault_mod  # noqa: E402
import audit as audit_mod  # noqa: E402

vault_mod._STORE = _TMP / "data" / "byok.json"
audit_mod.AUDIT_FILE = _TMP / "data" / "audit.jsonl"

db_mod.init_db()
db_mod.seed_demo()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Every test shares one TestClient IP; the in-memory auth rate-limit
    counters (10 registrations / 20 auth hits per IP) otherwise bleed across
    tests and trip 429s depending on ordering."""
    rl = sys.modules.get("ratelimit")
    lim = getattr(rl, "limiter", None) if rl else None
    reset = getattr(lim, "reset", None)
    if reset:
        reset()
    yield


def pytest_unconfigure(config):  # pragma: no cover
    import shutil

    shutil.rmtree(_TMP, ignore_errors=True)
