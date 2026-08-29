"""End-to-end billing flow test over the real HTTP API in local sandbox mode.

Simulates a complete Paddle lifecycle with NO real account: subscribe returns a
sandbox checkout URL, the dev-complete endpoint mints a *correctly signed*
Paddle webhook and runs it through the same handler production uses, then a
replayed webhook must NOT double-grant, and a refund must downgrade the plan.

The sandbox env is applied via a MODULE-scoped autouse fixture so no other test
module is polluted, and the backend reads these vars live (no import-time
constants), so this file needs no module-level os.environ mutation.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

import db
from main import app

client = TestClient(app)

_SANDBOX = {
    "FLUXSWARM_PAYMENTS": "1",
    "FLUXSWARM_PAYMENT_PROVIDER": "paddle",
    "FLUXSWARM_PADDLE_MOCK": "1",
    "PADDLE_WEBHOOK_SECRET": "sandbox-webhook-secret",
}


@pytest.fixture(scope="module", autouse=True)
def _sandbox_env():
    saved = {k: os.environ.get(k) for k in _SANDBOX}
    for k, v in _SANDBOX.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    os.environ.pop("PADDLE_API_KEY", None)
    os.environ.pop("PADDLE_API_BASE", None)
    yield
    # restore
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@contextlib.contextmanager
def _env_override(changes: dict[str, str]) -> Iterator[None]:
    """Temporarily set env vars for a single request, restoring afterwards."""
    saved = {k: os.environ.get(k) for k in changes}
    for k, v in changes.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _secret() -> str:
    return os.environ.get("PADDLE_WEBHOOK_SECRET", "mock-secret")


def _login() -> str:
    r = client.post("/api/auth/login", json={"email": "demo@fluxswarm.ai", "password": "demo1234"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _v1(payload: dict) -> tuple[bytes, str]:
    body = json.dumps(payload).encode("utf-8")
    sig = base64.b64encode(hmac.new(_secret().encode(), body, hashlib.sha256).digest()).decode()
    return body, sig


def _webhook_payload(event_id: str, user_id: int, plan: str, status="completed", event="transaction.completed"):
    return {
        "event_id": event_id,
        "event_type": event,
        "data": {
            "id": f"txn_{event_id}",
            "status": status,
            "custom_data": {"user_id": str(user_id), "plan": plan},
            "total": {"amount": db.PLANS[plan].get("price", 29) * 100, "currency": "USD"},
        },
        "metadata": {},
    }


def test_paid_subscribe_opens_sandbox_checkout():
    tok = _login()
    r = client.post("/api/subscribe/pro", headers=_headers(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "/mock-checkout/1/pro" in body["checkout_url"]
    assert body["plan"] == "pro"
    # No upgrade yet — credits only granted on a verified payment.
    assert db.get_user_by_id(1)["plan"] == "demo"


def test_dev_complete_grants_and_shows_checkout_page():
    r = client.get("/mock-checkout/1/pro")
    assert r.status_code == 200
    assert "دفعة تجريبية" in r.text

    r = client.get("/api/payments/dev-complete/1/pro")
    assert r.status_code == 200, r.text
    assert r.json()["paid"] is True
    assert db.get_user_by_id(1)["plan"] == "pro"
    assert db.get_user_by_id(1)["credits"] == 120


def test_webhook_email_and_price_fallback():
    """A transaction without custom_data still maps to the right user via their
    email, and to the right plan via the paid price id."""
    uid = db.create_user("fallback@fluxswarm.test", "Fallback", "s3cure-Pass-123")["id"]
    payload = {
        "event_id": "evt_fallback_1",
        "event_type": "transaction.completed",
        "data": {
            "id": f"txn_{uid}",
            "status": "completed",
            "customer": {"email": "fallback@fluxswarm.test"},
            "items": [{"price": {"id": "pri_fallback_pro"}, "quantity": 1}],
            "total": {"amount": "9900", "currency": "USD"},
        },
        "metadata": {},
    }
    with _env_override({"PADDLE_PRICE_PRO": "pri_fallback_pro"}):
        body, sig = _v1(payload)
        r = client.post("/api/payments/webhook", headers={"Paddle-Signature": sig}, content=body)
        assert r.status_code == 200, r.text
    assert db.get_user_by_id(uid)["plan"] == "pro"


def test_checkout_page_loads_paddlejs():
    with _env_override({"PADDLE_CLIENT_TOKEN": "test_token_abc", "PADDLE_API_BASE": "https://sandbox-api.paddle.com"}):
        r = client.get("/checkout")
        assert r.status_code == 200
        assert "cdn.paddle.com/paddle/v2/paddle.js" in r.text
        assert "Environment.set" in r.text
        assert "Checkout.open" in r.text
        assert "test_token_abc" in r.text
        # live env flips to no .Environment.set
        with _env_override({"PADDLE_API_BASE": "https://api.paddle.com"}):
            r2 = client.get("/checkout")
            assert "Environment.set" not in r2.text
        # missing token shows the config warning instead of loading the SDK
        with _env_override({"PADDLE_CLIENT_TOKEN": ""}):
            r3 = client.get("/checkout")
            assert "PADDLE_CLIENT_TOKEN" in r3.text


def test_webhook_replay_is_idempotent():
    uid = db.create_user("replay@fluxswarm.test", "Replay", "s3cure-Pass-123")["id"]
    body, sig = _v1(_webhook_payload("evt_replay_1", uid, "starter"))
    kw = {"headers": {"Paddle-Signature": sig}, "content": body}
    first = client.post("/api/payments/webhook", **kw)
    assert first.status_code == 200, first.text
    assert first.json()["deduplicated"] is False
    assert db.get_user_by_id(uid)["credits"] == 25

    second = client.post("/api/payments/webhook", **kw)
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    assert db.get_user_by_id(uid)["credits"] == 25  # not double-granted


def test_webhook_rejects_bad_signature():
    body, _ = _v1(_webhook_payload("evt_bad_1", 1, "starter"))
    r = client.post("/api/payments/webhook", headers={"Paddle-Signature": "forged"}, content=body)
    assert r.status_code == 400
    assert db.get_user_by_id(1)["plan"] == "pro"  # untouched


def test_refund_downgrades_plan_keeps_credits():
    assert db.get_user_by_id(1)["plan"] == "pro"
    body, sig = _v1(_webhook_payload("evt_refund_1", 1, "pro", status="refunded",
                                     event="transaction.refunded"))
    r = client.post("/api/payments/webhook", headers={"Paddle-Signature": sig}, content=body)
    assert r.status_code == 200, r.text
    assert db.get_user_by_id(1)["plan"] == "demo"
    assert db.get_user_by_id(1)["credits"] == 120  # never clawed back


def test_mock_disabled_with_live_credentials(monkeypatch):
    monkeypatch.setenv("PADDLE_API_KEY", "live_key")
    monkeypatch.setenv("PADDLE_API_BASE", "https://api.paddle.com")  # live → mock forbidden
    r = client.get("/api/payments/dev-complete/1/pro")
    assert r.status_code == 404  # no free-credits path once live creds are present


def test_adjustment_refund_maps_transaction_to_user():
    """Paddle v1 refund arrives as adjustment.created (type=refund) with only a
    transaction_id — the webhook must map it back to the paying user and
    downgrade their plan without clawing back credits."""
    uid = db.create_user("adjmap@fluxswarm.test", "Adj Map", "s3cure-Pass-123")["id"]
    pay = _webhook_payload(f"evt_map_pay_{uid}", uid, "pro")
    pay["data"]["id"] = "txn_map_pay_123"
    body, sig = _v1(pay)
    r = client.post("/api/payments/webhook", headers={"Paddle-Signature": sig}, content=body)
    assert r.status_code == 200, r.text
    assert db.get_user_by_id(uid)["plan"] == "pro"

    adj = {
        "event_id": f"evt_map_adj_{uid}",
        "event_type": "adjustment.created",
        "data": {
            "id": f"adj_{uid}",
            "type": "refund",
            "status": "completed",
            "transaction_id": "txn_map_pay_123",
            "amount": {"amount": "9900", "currency_code": "USD"},
        },
        "metadata": {},
    }
    body, sig = _v1(adj)
    r = client.post("/api/payments/webhook", headers={"Paddle-Signature": sig}, content=body)
    assert r.status_code == 200, r.text
    assert db.get_user_by_id(uid)["plan"] == "demo"
    assert db.get_user_by_id(uid)["credits"] == 120  # never clawed back


def test_account_export_delete_via_api():
    """CCPA access + erasure exercised over HTTP with a real session (G-1 closure)."""
    email = "ccpa_api@fluxswarm.test"
    pwd = "s3cure-Pass-123"
    u = db.create_user(email, "CCPA API", pwd)
    db.upgrade_plan(u["id"], "starter")
    tok = client.post("/api/auth/login", json={"email": email, "password": pwd}).json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}

    r1 = client.get("/api/account/export", headers=hdr)
    assert r1.status_code == 200
    data = r1.json()
    assert data["user"]["email"] == email
    assert data["user"]["plan"] == "starter"

    r2 = client.delete("/api/account", headers=hdr)
    assert r2.status_code == 200 and r2.json()["ok"] is True
    assert db.get_user_by_id(u["id"]) is None

    # A deleted user's token no longer maps to anyone -> rejected (idempotent over HTTP).
    r3 = client.delete("/api/account", headers=hdr)
    assert r3.status_code == 401