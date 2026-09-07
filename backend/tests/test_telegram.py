"""Telegram account-linking coverage.

The link flow pairs a Telegram chat to a platform account through a one-time
code: GET /api/telegram/link issues it, the bot process redeems it via
db.consume_telegram_link_code (a chat id + code -> binding), and the account can
unlink over the API. Also verifies the linked credit (spend/output) helpers.
"""
from __future__ import annotations

import time

import db
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _make_user() -> tuple[int, str]:
    ts = int(time.time() * 1000)
    email = f"tgapi{ts}@fluxswarm.test"
    r = client.post("/api/auth/register", json={
        "email": email, "name": "TG User", "password": "s3cure-Pass-123",
        "tos_accept": True,
    })
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    uid = db.get_user_by_email(email)["id"]
    return uid, token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_link_code_reuse_and_unlinked_status():
    uid, tok = _make_user()
    r = client.get("/api/telegram/link", headers=_headers(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    code = body["code"]
    assert code and len(code) in (6, 8)
    assert body["ttl_seconds"] == db.TELEGRAM_LINK_TTL
    # Same active code is re-served (throttle, one per user).
    r2 = client.get("/api/telegram/link", headers=_headers(tok))
    assert r2.status_code == 200
    assert r2.json()["code"] == code
    # Not linked yet.
    st = client.get("/api/telegram/status", headers=_headers(tok)).json()
    assert st["linked"] is False and st["telegram_chat_id"] is None


def test_redeem_binds_chat_and_marks_status():
    uid, tok = _make_user()
    code = client.get("/api/telegram/link", headers=_headers(tok)).json()["code"]
    # Bot side: redeem with a plain chat id.
    user = db.consume_telegram_link_code(code, telegram_chat_id=777777)
    assert user is not None and user["id"] == uid
    bound = db.get_user_by_telegram_chat(777777)
    assert bound is not None and bound["id"] == uid
    st = client.get("/api/telegram/status", headers=_headers(tok)).json()
    assert st["linked"] is True and st["telegram_chat_id"] == 777777


def test_redeem_rejects_used_and_bogus_codes():
    _, tok = _make_user()
    code = client.get("/api/telegram/link", headers=_headers(tok)).json()["code"]
    assert db.consume_telegram_link_code(code, 11111) is not None
    assert db.consume_telegram_link_code(code, 11111) is None  # consumed
    assert db.consume_telegram_link_code("ZZZZZZ", 11111) is None
    assert db.consume_telegram_link_code("", 11111) is None


def test_unlink_over_api_clears_binding():
    _, tok = _make_user()
    code = client.get("/api/telegram/link", headers=_headers(tok)).json()["code"]
    db.consume_telegram_link_code(code, 222222)
    r = client.delete("/api/telegram/link", headers=_headers(tok))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert db.get_user_by_telegram_chat(222222) is None
    st = client.get("/api/telegram/status", headers=_headers(tok)).json()
    assert st["linked"] is False


def test_link_appears_in_ccpa_payload_and_survives_deletion():
    _, tok = _make_user()
    code = client.get("/api/telegram/link", headers=_headers(tok)).json()["code"]
    db.consume_telegram_link_code(code, 333333)
    payload = client.get("/api/account/export", headers=_headers(tok)).json()
    assert any(t["telegram_chat_id"] == 333333 for t in payload["telegram_links"])
    # Unlink via account deletion must remove the binding too.
    uid = db.get_user_by_telegram_chat(333333)["id"]
    db.delete_user(uid)
    assert db.get_user_by_telegram_chat(333333) is None


def test_index_serves_telegram_wiring():
    r = client.get("/")
    assert r.status_code == 200
    assert '"/api/telegram/link"' in r.text
    assert '"/api/telegram/status"' in r.text
    assert "tg_link" in r.text


def test_credit_spend_and_failed_launch_refund():
    # Round-trip on a freshly registered account (credits = demo allowance).
    email = "credit-roundtrip@fluxswarm.test"
    r = client.post("/api/auth/register", json={
        "email": email, "name": "Credits", "password": "s3cure-Pass-123",
        "tos_accept": True,
    })
    assert r.status_code == 200, r.text
    uid = db.get_user_by_email(email)["id"]
    before = db.get_user_credits(uid)
    assert db.deduct_credit(uid) is True
    assert db.get_user_credits(uid) == before - 1
    assert db.add_credit(uid, 1) is True
    assert db.get_user_credits(uid) == before
    db.delete_user(uid)