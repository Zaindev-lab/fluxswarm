"""Paddle gateway tests: signature verification (V1 + V2), webhook receipt
normalization, provider resolution (fail-safe stub), and db idempotency."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

import db
import payments

SECRET = "paddle_test_webhook_secret_0123456789"


def _v1_sig(body: bytes, secret: str = SECRET) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode("ascii")


def _v2_sig(body: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    ts = int(ts if ts is not None else time.time())
    hexed = hmac.new(
        secret.encode(), f"paddle-{ts};{body.decode('utf-8')}".encode(), hashlib.sha256
    ).hexdigest()
    return f"ts={ts};h1={hexed}"


# ---------- signature verification ----------
def test_v1_signature_valid():
    body = b'{"event_id":"evt_1"}'
    assert payments.verify_paddle_signature(body, _v1_sig(body), SECRET) is True


def test_v1_signature_wrong_key():
    body = b'{"event_id":"evt_1"}'
    assert payments.verify_paddle_signature(body, _v1_sig(body, "other"), SECRET) is False


def test_v1_signature_tampered_body():
    sig = _v1_sig(b'{"event_id":"evt_1"}')
    assert payments.verify_paddle_signature(b'{"event_id":"evt_2"}', sig, SECRET) is False


def test_v1_empty_signature_rejected():
    assert payments.verify_paddle_signature(b"{}", "", SECRET) is False


def test_v2_signature_valid_current():
    body = b'{"event_id":"evt_2"}'
    assert payments.verify_paddle_signature(body, _v2_sig(body), SECRET) is True


def test_v2_signature_replay_window():
    body = b'{"event_id":"evt_2"}'
    old_sig = _v2_sig(body, ts=int(time.time()) - 3600)
    assert payments.verify_paddle_signature(body, old_sig, SECRET, now=time.time()) is False


def test_v2_signature_wrong_key():
    body = b'{"event_id":"evt_2"}'
    assert payments.verify_paddle_signature(body, _v2_sig(body, "other"), SECRET) is False


# ---------- webhook receipt normalization ----------
def _pay_payload(event_type="transaction.completed", status="completed", uid="7", plan="pro"):
    return {
        "event_id": "evt_pay_1",
        "event_type": event_type,
        "data": {
            "id": "txn_pay_1",
            "status": status,
            "custom_data": {"user_id": uid, "plan": plan},
            "total": {"amount": 9900, "currency": "USD"},
        },
        "metadata": {},
    }


def _receipt(payload: dict) -> dict:
    raw = json.dumps(payload).encode("utf-8")
    gw = payments.PaddleGateway.__new__(payments.PaddleGateway)
    gw._api_key = "pk_test_x"
    gw._secret = SECRET
    return gw.handle_webhook(raw, signature=_v1_sig(raw))


def test_handle_webhook_payment_succeeded():
    rec = _receipt(_pay_payload())
    assert rec["ok"] is True
    assert rec["gateway"] == "paddle"
    assert rec["event"] == "payment.succeeded"
    assert rec["event_id"] == "evt_pay_1"
    assert rec["transaction_id"] == "txn_pay_1"
    assert rec["user_id"] == 7
    assert rec["plan"] == "pro"
    assert rec["amount_cents"] == 9900
    assert rec["currency"] == "usd"


def test_handle_webhook_refund():
    rec = _receipt(_pay_payload(event_type="transaction.refunded", status="refunded"))
    assert rec["ok"] is True
    assert rec["event"] == "payment.refunded"


def _adjustment_payload(adj_type="refund", status="completed", txn="txn_pay_1", event_id="evt_adj_1"):
    return {
        "event_id": event_id,
        "event_type": "adjustment.created",
        "data": {
            "id": "adj_1",
            "type": adj_type,
            "status": status,
            "transaction_id": txn,
            "amount": {"amount": 9900, "currency_code": "USD"},
        },
        "metadata": {},
    }


def test_handle_webhook_adjustment_refund():
    """Paddle v1 refunds arrive as adjustment.created (type=refund). The receipt
    must map to payment.refunded and carry the ORIGINAL transaction id."""
    rec = _receipt(_adjustment_payload())
    assert rec["ok"] is True
    assert rec["event"] == "payment.refunded"
    assert rec["transaction_id"] == "txn_pay_1"


def test_handle_webhook_adjustment_completed_status_is_not_success():
    """An adjustment with status='completed' must NOT be read as a successful
    payment (the status branch is only reached for transaction events)."""
    rec = _receipt(_adjustment_payload(adj_type="refund", status="completed"))
    assert rec["event"] == "payment.refunded"


def test_handle_webhook_adjustment_credit_is_not_refund():
    """A non-refund adjustment (manual credit) must not downgrade anyone."""
    rec = _receipt(_adjustment_payload(adj_type="credit"))
    assert rec["ok"] is True
    assert rec["event"] is None


def test_handle_webhook_bad_signature():
    raw = json.dumps(_pay_payload()).encode("utf-8")
    gw = payments.PaddleGateway.__new__(payments.PaddleGateway)
    gw._api_key = "pk_test_x"
    gw._secret = SECRET
    rec = gw.handle_webhook(raw, signature="forged")
    assert rec["ok"] is False
    assert rec["reason"] == "bad_signature"


def test_handle_webhook_not_configured(monkeypatch):
    monkeypatch.delenv("FLUXSWARM_PADDLE_MOCK", raising=False)
    gw = payments.PaddleGateway.__new__(payments.PaddleGateway)
    gw._api_key = ""
    gw._secret = ""
    rec = gw.handle_webhook(b"{}", signature="x")
    assert rec["ok"] is False
    assert rec["reason"] == "paddle_not_configured"


# ---------- gateway resolution (fail-safe) ----------
def test_gateway_defaults_to_stub(monkeypatch):
    monkeypatch.delenv("FLUXSWARM_PAYMENT_PROVIDER", raising=False)
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    monkeypatch.delenv("PADDLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("FLUXSWARM_PADDLE_MOCK", raising=False)
    gw = payments.get_gateway()
    assert gw.name == "stub"
    assert getattr(gw, "configured", False) is False


def test_gateway_paddle_without_keys_returns_stub_and_402_safe(monkeypatch):
    monkeypatch.setenv("FLUXSWARM_PAYMENT_PROVIDER", "paddle")
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    monkeypatch.delenv("PADDLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("FLUXSWARM_PADDLE_MOCK", raising=False)
    gw = payments.get_gateway()
    assert gw.name == "stub"  # credentials missing -> dev gate stays closed


def test_gateway_paddle_with_keys_operative(monkeypatch):
    monkeypatch.setenv("FLUXSWARM_PAYMENT_PROVIDER", "paddle")
    monkeypatch.setenv("PADDLE_API_KEY", "pk_test_x")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("FLUXSWARM_PADDLE_MOCK", raising=False)
    gw = payments.get_gateway()
    assert gw.name == "paddle"
    assert gw.configured is True


# ---------- db: webhook idempotency + plan lifecycle ----------
def test_payment_event_idempotency():
    assert db.record_payment_event("evt_idem_1", "paddle", "payment.succeeded", 1, {"plan": "pro"}) is True
    assert db.record_payment_event("evt_idem_1", "paddle", "payment.succeeded", 1, {"plan": "pro"}) is False


def test_payment_user_by_txn():
    u = db.create_user("txnmap@fluxswarm.test", "Txn Map", "s3cure-Pass-123")
    db.record_payment_event("evt_txnmap_1", "paddle", "payment.succeeded", u["id"],
                            {"plan": "pro", "txn": "txn_abc_1"})
    assert db.payment_user_by_txn("txn_abc_1") == u["id"]
    assert db.payment_user_by_txn("txn_nope") is None
    assert db.payment_user_by_txn("") is None


def test_upgrade_then_downgrade_subscription():
    u = db.create_user("paddle_test@fluxswarm.test", "Paddle Tester", "s3cure-Pass-123")
    db.upgrade_plan(u["id"], "pro")
    assert db.get_user_by_id(u["id"])["plan"] == "pro"
    db.downgrade_subscription(u["id"])
    assert db.get_user_by_id(u["id"])["plan"] == "demo"


# ---------- db: CCPA access + erasure ----------
def test_audit_trail_strips_sensitive_keys():
    """Audit records never persist tokens/keys/secrets even if passed as meta
    keys — the sensitive set is dropped at write time (G-2 closure)."""
    import audit

    audit.audit("payments.webhook", uid=1, email="audit@fluxswarm.test", outcome="ok",
                token="jwt-abc", secret="super-secret", api_key="sk-ant-x",
                plan="pro", txn="txn_9", amount_cents=9900)
    trail = audit.AUDIT_FILE.read_text(encoding="utf-8")
    for leaked in ("jwt-abc", "super-secret", "sk-ant-x"):
        assert leaked not in trail
    assert "audit@fluxswarm.test" in trail
    assert "payments.webhook" in trail
    assert "txn_9" in trail


def test_account_export_and_delete():
    u = db.create_user("ccpa_test@fluxswarm.test", "CCPA Tester", "s3cure-Pass-123")
    db.upgrade_plan(u["id"], "starter")
    payload = db.account_payload(u["id"])
    assert payload["user"]["email"] == "ccpa_test@fluxswarm.test"
    assert db.delete_user(u["id"]) is True
    assert db.delete_user(u["id"]) is False
    assert db.get_user_by_id(u["id"]) is None