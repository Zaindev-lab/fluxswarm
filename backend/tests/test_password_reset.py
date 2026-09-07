"""Gate 2 — password reset flow (single-use, expiring, hashed at rest).

The production delivery channel is a mailer (absent here). Tests exercise the
full flow through FLUXSWARM_RESET_SELF_SERVICE=1 (dev/test echo flag, default
off). Enforcement points: no account enumeration, anti-spam rate limit,
single-use token, expiry, generic response when the echo flag is off.
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
    r = client.post("/api/auth/register", json={"email": email, "name": "T", "password": pw, "tos_accept": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_reset_self_service_full_flow(monkeypatch):
    reg = _register("resetok@fluxswarm.test")
    old_token = reg["token"]
    monkeypatch.setenv("FLUXSWARM_RESET_SELF_SERVICE", "1")

    req = client.post("/api/auth/reset-request", json={"email": "resetok@fluxswarm.test"})
    assert req.status_code == 200, req.text
    body = req.json()
    assert body["ok"] is True
    token = body["reset_token"]
    assert len(token) >= 32

    done = client.post("/api/auth/reset", json={"token": token, "new_password": "R3set-Pass-99"})
    assert done.status_code == 200 and done.json()["ok"] is True
    # New password works; old sessions are dead.
    assert db_mod.authenticate("resetok@fluxswarm.test", "R3set-Pass-99") is not None
    assert client.get("/api/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert db_mod.authenticate("resetok@fluxswarm.test", "s3cure-Pass-123") is None

    login = client.post("/api/auth/login",
                        json={"email": "resetok@fluxswarm.test", "password": "R3set-Pass-99"})
    assert login.status_code == 200, login.text


def test_reset_token_is_single_use(monkeypatch):
    _register("resetsingle@fluxswarm.test")
    monkeypatch.setenv("FLUXSWARM_RESET_SELF_SERVICE", "1")
    token = client.post("/api/auth/reset-request",
                        json={"email": "resetsingle@fluxswarm.test"}).json()["reset_token"]

    first = client.post("/api/auth/reset", json={"token": token, "new_password": "An0ther-Pass"})
    assert first.status_code == 200
    second = client.post("/api/auth/reset", json={"token": token, "new_password": "Again-777777"})
    assert second.status_code == 400  # already consumed


def test_reset_wrong_or_expired_token_rejected(monkeypatch):
    _register("resetbad@fluxswarm.test")
    monkeypatch.setenv("FLUXSWARM_RESET_SELF_SERVICE", "1")
    client.post("/api/auth/reset-request", json={"email": "resetbad@fluxswarm.test"})

    r = client.post("/api/auth/reset", json={"token": "not-a-real-token", "new_password": "P4ssword12"})
    assert r.status_code == 400

    # Expired token: minted with a negative TTL -> dead on arrival.
    # (create_password_reset purges expired rows on every call, so the stale
    # token can never be redeemed.)
    uid = db_mod.get_user_by_email("resetbad@fluxswarm.test")["id"]
    raw = db_mod.create_password_reset(uid, ttl=-10)
    r = client.post("/api/auth/reset", json={"token": raw, "new_password": "P4ssword13"})
    assert r.status_code == 400


def test_reset_request_no_account_enumeration(monkeypatch):
    monkeypatch.setenv("FLUXSWARM_RESET_SELF_SERVICE", "1")
    _register("enum@fluxswarm.test")

    # Existing vs missing email: identical shape, same generic body.
    real = client.post("/api/auth/reset-request", json={"email": "enum@fluxswarm.test"})
    missing = client.post("/api/auth/reset-request", json={"email": "nobody@fluxswarm.test"})
    assert real.status_code == 200 and missing.status_code == 200
    assert "reset_token" in real.json()          # dev echo flag: delivered path
    assert "reset_token" not in missing.json()   # no account -> nothing minted


def test_reset_request_no_echo_in_production(monkeypatch):
    """Without FLUXSWARM_RESET_SELF_SERVICE the client NEVER receives a token —
    even for a real account — it only may see the token via the eventual mailer.
    Response must stay generic for both cases."""
    monkeypatch.delenv("FLUXSWARM_RESET_SELF_SERVICE", raising=False)
    _register("prodreset@fluxswarm.test")
    r = client.post("/api/auth/reset-request", json={"email": "prodreset@fluxswarm.test"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "reset_token" not in r.json()


def test_reset_weak_password_rejected(monkeypatch):
    _register("resetweak@fluxswarm.test")
    monkeypatch.setenv("FLUXSWARM_RESET_SELF_SERVICE", "1")
    token = client.post("/api/auth/reset-request",
                        json={"email": "resetweak@fluxswarm.test"}).json()["reset_token"]
    r = client.post("/api/auth/reset", json={"token": token, "new_password": "short"})
    assert r.status_code == 400