"""Gate 2 — account erasure: Hermes board workspaces + reset/code rows.

CCPA/CPRA delete must erase HERMES kanban board directories owned by the user
(without path traversal and without touching other users' boards or the shared
demo boards), and must purge telegram_codes + password_resets rows.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db as db_mod
import main as main_mod
import hermes_client as hc_mod

client = TestClient(main_mod.app)


class _PermissiveLimiter:
    def ip_allowed(self, ip): return True
    def hit_ip(self, ip): return None
    def register_allowed(self, ip): return True
    def record_registration(self, ip): return None
    def login_allowed(self, ip, email): return True
    def record_login_failure(self, ip, email): return None
    def clear_login_failures(self, ip, email): return None
    def purchase_allowed(self, uid): return True
    def record_purchase(self, uid): return None


@pytest.fixture(autouse=True)
def _permissive_limiter(monkeypatch):
    monkeypatch.setattr(main_mod, "limiter", _PermissiveLimiter())


def _register(email: str):
    r = client.post("/api/auth/register", json={"email": email, "name": "T", "password": "s3cure-Pass-123", "tos_accept": True})
    assert r.status_code == 200, r.text
    return r.json()


# Capture the ORIGINAL hc.delete_boards at import time: `main` imports
# hermes_client by module, so a later monkeypatch of main_mod.hc.delete_boards
# would otherwise overwrite the very function _real_delete needs to call.
_DEL_ORIG = hc_mod.delete_boards


def _boards_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="fluxswarm_boards_"))


# ---------- hc.delete_boards (unit: containment + safe slugs) ----------
def test_delete_boards_removes_only_owned_safe_boards():
    root = _boards_root()
    (root / "u1-proj").mkdir(parents=True)
    (root / "u1-other").mkdir(parents=True)
    (root / "u2-proj").mkdir(parents=True)
    victim = Path(tempfile.mkdtemp(prefix="victim_"))
    (victim / "file.txt").write_text("data")

    removed = hc_mod.delete_boards(
        ["u1-proj", "u1-other", "../../{0}".format(victim.name), "../evil", "has space"],
        boards_root=root,
    )
    assert removed == 2  # only the two u1-* boards are gone
    assert not (root / "u1-proj").exists()
    assert not (root / "u1-other").exists()
    assert (root / "u2-proj").exists()       # other user's board untouched
    assert victim.exists()                    # traversal slug was rejected

    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(victim, ignore_errors=True)


def test_delete_boards_ignores_unsafe_slugs():
    root = _boards_root()
    (root / "u9-ok").mkdir(parents=True)
    removed = hc_mod.delete_boards(["u9-ok", "..", ".\\u9-ok", "u9/../u1"], boards_root=root)
    assert removed == 1
    assert not (root / "u9-ok").exists()
    shutil.rmtree(root, ignore_errors=True)


# ---------- db.delete_user: purge one-time rows ----------
def test_delete_user_purges_reset_and_telegram_codes():
    u = db_mod.create_user("delrows@fluxswarm.test", "DR", "s3cure-Pass-123")
    db_mod.create_password_reset(u["id"])
    db_mod.new_telegram_link_code(u["id"])

    c = db_mod._conn()
    assert c.execute("SELECT COUNT(*) FROM password_resets WHERE user_id=?", (u["id"],)).fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM telegram_codes WHERE user_id=?", (u["id"],)).fetchone()[0] == 1
    c.close()

    assert db_mod.delete_user(u["id"]) is True
    c = db_mod._conn()
    assert c.execute("SELECT COUNT(*) FROM password_resets WHERE user_id=?", (u["id"],)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM telegram_codes WHERE user_id=?", (u["id"],)).fetchone()[0] == 0
    assert db_mod.get_user_by_id(u["id"]) is None
    c.close()


# ---------- API: full account delete removes boards + user ----------
def test_api_account_delete_erases_owned_boards(monkeypatch):
    reg_a = _register("delboard-a@fluxswarm.test")
    reg_b = _register("delboard-b@fluxswarm.test")
    uid_a = reg_a["user"]["id"]
    uid_b = reg_b["user"]["id"]
    root = _boards_root()
    (root / f"u{uid_a}-proj").mkdir(parents=True)          # A owns this
    (root / f"u{uid_b}-proj").mkdir(parents=True)          # B owns this (must survive)
    (root / "flux-demo-123").mkdir(parents=True)           # shared demo (must survive)

    monkeypatch.setattr(main_mod.hc, "delete_boards",
                        lambda slugs, boards_root=None: _DEL_ORIG(slugs, boards_root=root))

    # Give A a project row so the endpoint knows their slugs.
    db_mod.add_project(uid_a, f"u{uid_a}-proj", "P", "goal")
    db_mod.add_project(uid_b, f"u{uid_b}-proj", "P", "goal")

    r = client.delete("/api/account", headers={"Authorization": f"Bearer {reg_a['token']}"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["boards_deleted"] == 1

    assert not (root / f"u{uid_a}-proj").exists()          # A erased
    assert (root / f"u{uid_b}-proj").exists()               # B intact
    assert (root / "flux-demo-123").exists()                # demo intact
    assert db_mod.get_user_by_id(uid_a) is None

    # A's now-deleted token is rejected.
    assert client.get("/api/me", headers={"Authorization": f"Bearer {reg_a['token']}"}).status_code == 401
    shutil.rmtree(root, ignore_errors=True)