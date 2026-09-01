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

os.environ.setdefault("FLUXSWARM_ALLOW_MULTI", "1")
# The test session runs in Demo/dev mode: ephemeral/generated secrets are
# allowed and the Free hosted runtime is the (explicit) default. Production-mode
# behaviors (missing-secret failure, unconfigured-runtime failure) are covered
# by dedicated tests that toggle these env vars via monkeypatch.
os.environ.setdefault("FLUXSWARM_DEMO_MODE", "1")

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


def pytest_unconfigure(config):  # pragma: no cover
    import shutil

    shutil.rmtree(_TMP, ignore_errors=True)
