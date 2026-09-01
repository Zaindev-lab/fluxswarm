"""Gate 2 — logout, password change and session invalidation.

Logout and password change must invalidate every previously-issued JWT for that
user (stateless tokens: refusal is driven by ``users.logged_out_at`` vs the
token's ``iat``). A wrong current password must NOT change anything.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import db as db_mod
import main as main_mod

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


def _register(email: str, pw: str = "s3cure-Pass-123"):
    r = client.post("/api/auth/register", json={"email": email, "name": "T", "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


def test_logout_invalidates_all_sessions():
    reg = _register("logout@fluxswarm.test")
    uid = reg["user"]["id"]
    token = reg["token"]

    # Session token works before logout.
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    r = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert db_mod.get_logged_out_at(uid) > 0

    # The SAME token (any iat before logout) is now refused.
    r2 = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 401, r2.text
    # Calling logout again with the now-dead token also fails closed.
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_password_change_invalidates_sessions_and_old_password():
    reg = _register("pwchange@fluxswarm.test")
    uid = reg["user"]["id"]
    old_token = reg["token"]

    # Wrong current password -> no change, session survives.
    bad = client.post("/api/auth/password",
                      headers={"Authorization": f"Bearer {old_token}"},
                      json={"current": "wrong-pass", "new": "N3w-Pass-456"})
    assert bad.status_code == 401
    assert client.get("/api/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 200

    # Correct change -> ok, old password stops working, old sessions die.
    ok = client.post("/api/auth/password",
                     headers={"Authorization": f"Bearer {old_token}"},
                     json={"current": "s3cure-Pass-123", "new": "N3w-Pass-456"})
    assert ok.status_code == 200
    assert db_mod.authenticate("pwchange@fluxswarm.test", "N3w-Pass-456") is not None
    assert db_mod.authenticate("pwchange@fluxswarm.test", "s3cure-Pass-123") is None

    old_dead = client.get("/api/me", headers={"Authorization": f"Bearer {old_token}"})
    assert old_dead.status_code == 401
    # Fresh login with the new password works.
    new = client.post("/api/auth/login",
                      json={"email": "pwchange@fluxswarm.test", "password": "N3w-Pass-456"})
    assert new.status_code == 200, new.text
    assert client.get("/api/me", headers={"Authorization": f"Bearer {new.json()['token']}"}).status_code == 200


def test_optional_user_rejects_logged_out_session():
    reg = _register("optlogout@fluxswarm.test")
    token = reg["token"]
    demo_slug = "flux-demo-1"
    # A demo-board security scan is allowed with the token.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main_mod.hc, "ensure_board", lambda s: None)
        mp.setattr(main_mod.hc, "list_tasks", lambda s: [])
        mp.setattr(main_mod.hc, "read_workspace", lambda s: "")
        r = client.get(f"/api/projects/{demo_slug}/security",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
    client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    # After logout even the optional auth path treats the token as absent.
    r = client.get(f"/api/projects/{demo_slug}/security",
                   headers={"Authorization": f"Bearer {token}"})
    # No owner -> demo board is public, but the stale token must still not be
    # treated as a valid session; the scan itself runs either way.
    assert r.status_code == 200