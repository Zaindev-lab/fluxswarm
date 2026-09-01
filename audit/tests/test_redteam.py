"""FluxSwarm Red-Team harness (Phase 5 audit).

Author: independent security audit (opencode)
Runs against the REAL FastAPI app (TestClient / ASGI in-process) with:
  - an isolated sqlite DB (FLUXSWARM_DB -> temp),
  - repainted vault store + audit file (never touches real data/byok.json/audit.jsonl),
  - hermes_client subprocess calls MONKEYPATCHED to no-ops (no real swarms are
    launched, so no tokens/boards are consumed).

Evidence file: audit/tests/TEST-LEDGER.md.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("FLUXSWARM_ALLOW_MULTI", "1")
os.environ.pop("FLUXSWARM_PAYMENTS", None)

_TMP = Path(tempfile.mkdtemp(prefix="fs_redteam_"))
os.environ["FLUXSWARM_DB"] = str(_TMP / "users.db")

BACKEND = Path(__file__).resolve().parent.parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import pytest

import audit as audit_mod
import db as db_mod
import hermes_client as hc
import ratelimit as rl_mod
import vault as vault_mod
from fastapi.testclient import TestClient

vault_mod._STORE = _TMP / "byok.json"
audit_mod.AUDIT_FILE = _TMP / "audit.jsonl"

import main as app_mod  # noqa: E402  (after env/repaint)


@pytest.fixture(autouse=True)
def _hermes_mock(monkeypatch):
    """Never spawn real Hermes subprocesses; the API surface is what's tested."""
    monkeypatch.setattr(hc, "ensure_board", lambda slug: True)
    monkeypatch.setattr(hc, "launch_swarm", lambda slug, goal, **kw: type(
        "R", (), {"root_id": "rt", "worker_ids": ["w1"], "verifier_id": "v",
                   "synthesizer_id": "s"}))
    monkeypatch.setattr(hc, "launch_from_template", lambda slug, goal, agents, **kw: type(
        "R", (), {"root_id": "rt", "worker_ids": ["w1"], "verifier_id": "v",
                  "synthesizer_id": "s"}))
    monkeypatch.setattr(hc, "dispatch", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(hc, "list_tasks", lambda slug: [])
    # TestClient shares a single source IP across the whole session; reset the
    # in-process counter buckets per test or the 20/min global IP cap poisons
    # later tests. (Redis backend: not used locally; buckets live in _MEMORY.)
    _mem = rl_mod._MEMORY
    if _mem is not None:
        _mem._login_fail.clear()
        _mem._ip_hits.clear()
        _mem._reg_hits.clear()
        _mem._purchase_hits.clear()


@pytest.fixture
def client():
    return TestClient(app_mod.app)


def _reg(client, email):
    r = client.post("/api/auth/register", json={
        "email": email, "name": "T", "password": "passw0rd12"})
    assert r.status_code == 200, r.text
    return r.json()


def _tok(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "passw0rd12"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---------------- AUTH ----------------

def test_rt01_auth_roundtrip(client):
    u = _reg(client, "a@x.com")
    assert u["user"]["plan"] == "demo"
    me = client.get("/api/me", headers={"Authorization": "Bearer " + u["token"]})
    assert me.status_code == 200 and me.json()["email"] == "a@x.com"


def test_rt02_no_user_enumeration(client):
    r1 = client.post("/api/auth/login", json={"email": "ghost@x.com", "password": "wrongpass1"})
    r2 = client.post("/api/auth/login", json={"email": "a@x.com", "password": "wrongpass1"})
    assert r1.status_code == 401 and r2.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"]


def test_rt03_alg_none_rejected(client):
    hdr = {"alg": "none", "typ": "JWT"}
    body = {"uid": 1, "email": "demo@fluxswarm.ai", "plan": "scale",
            "exp": int(time.time()) + 3600}
    tok = base64.urlsafe_b64encode(json.dumps(hdr).encode()).rstrip(b"=").decode() + "." + \
        base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode() + ".AAAA"
    r = client.get("/api/me", headers={"Authorization": "Bearer " + tok})
    assert r.status_code == 401, r.status_code


def test_rt04_tampered_token_rejected(client):
    t = _tok(client, "a@x.com")
    tam = t[:-3] + ("ABC" if not t[-3:] == "ABC" else "DEF")
    r = client.get("/api/me", headers={"Authorization": "Bearer " + tam})
    assert r.status_code == 401


def test_rt05_login_bruteforce_locks(client):
    email = "brute@x.com"
    _reg(client, email)
    for _ in range(5):
        client.post("/api/auth/login", json={"email": email, "password": "badpass000"})
    r = client.post("/api/auth/login", json={"email": email, "password": "badpass000"})
    assert r.status_code == 429, r.status_code


def test_rt06_register_ip_cap(client):
    for i in range(20):
        client.post("/api/auth/login", json={"email": f"ip{i}@x.com", "password": "passw0rd12"})
    r = client.post("/api/auth/register", json={
        "email": "ipsurge@x.com", "name": "T", "password": "passw0rd12"})
    # 20 auth hits consume the 20/min IP cap -> the 21st request is blocked
    assert r.status_code == 429, r.status_code


def test_rt07_no_max_password_length(client):
    # FIXED (Phase 16, FIX-3): oversized passwords are now rejected pre-hash.
    big = "P" * 65536
    r = client.post("/api/auth/register", json={
        "email": "big@x.com", "name": "T", "password": big})
    assert r.status_code == 422, r.status_code  # Field(max_length=4096) guard
    r2 = client.post("/api/auth/login", json={"email": "big@x.com", "password": big})
    assert r2.status_code == 422, r2.status_code


# ---------------- TENANT ISOLATION (multi-account) ----------------

def _mk_ab(client):
    import uuid
    ua = uuid.uuid4().hex[:8]
    a = _reg(client, f"alice{ua}@x.com")
    b = _reg(client, f"bob{ua}@x.com")
    tb = b["token"]
    r = client.post("/api/projects", headers={"Authorization": "Bearer " + a["token"]},
                    json={"name": "A", "goal": "build x"})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    return a, b, slug


def test_rt_t1_b_cannot_read_a_tasks(client):
    _, b, slug = _mk_ab(client)
    r = client.get(f"/api/projects/{slug}/tasks",
                   headers={"Authorization": "Bearer " + b["token"]})
    assert r.status_code == 403, r.status_code


def test_rt_t2_b_cannot_dispatch_a_board(client):
    _, b, slug = _mk_ab(client)
    r = client.post(f"/api/projects/{slug}/dispatch",
                    headers={"Authorization": "Bearer " + b["token"]})
    assert r.status_code == 403


def test_rt_t3_b_cannot_scan_a_board(client):
    _, b, slug = _mk_ab(client)
    r = client.get(f"/api/projects/{slug}/security",
                   headers={"Authorization": "Bearer " + b["token"]})
    assert r.status_code == 403


def test_rt_t4_project_list_isolated(client):
    _, b, slug = _mk_ab(client)
    r = client.get("/api/projects", headers={"Authorization": "Bearer " + b["token"]})
    assert r.status_code == 200
    assert all(p["board_slug"] != slug for p in r.json())


def test_rt_t5_owner_access_control(client):
    a, _, slug = _mk_ab(client)
    r = client.get(f"/api/projects/{slug}/tasks",
                   headers={"Authorization": "Bearer " + a["token"]})
    assert r.status_code != 403  # owner must pass the ownership guard


def test_rt_t6_template_marketplace_isolation_and_earn(client):
    a, b, _ = _mk_ab(client)
    pid = client.post("/api/templates",
                      headers={"Authorization": "Bearer " + a["token"]},
                      json={"name": "MyTpl", "agents": ["Planner", "TDD"],
                            "price_credits": 2}).json()["id"]
    mine = client.get("/api/templates/mine",
                      headers={"Authorization": "Bearer " + b["token"]}).json()
    assert all(t["id"] != pid for t in mine)
    # public listing shows it; buying by B works and pays author 50%.
    # NOTE: _mk_ab already created A's project, which cost A 1 credit ->
    # baseline is 3-1=2, and the purchase adds max(1, price//2)=1.
    base_a = db_mod.get_user_by_id(a["user"]["id"])["credits"]
    r = client.post(f"/api/templates/{pid}/buy",
                    headers={"Authorization": "Bearer " + b["token"]},
                    json={"goal": "use it"})
    assert r.status_code == 200, r.text
    na = db_mod.get_user_by_id(a["user"]["id"])["credits"]
    nb = db_mod.get_user_by_id(b["user"]["id"])["credits"]
    assert na == base_a + max(1, 2 // 2), (na, "author should earn 1 (half of 2)")
    assert nb == 3 - 2, (nb, "buyer should spend 2")


def test_rt_cannot_buy_own_template(client):
    a, _, _ = _mk_ab(client)
    pid = client.post("/api/templates",
                      headers={"Authorization": "Bearer " + a["token"]},
                      json={"name": "Own", "agents": ["Planner"], "price_credits": 2}).json()["id"]
    r = client.post(f"/api/templates/{pid}/buy",
                    headers={"Authorization": "Bearer " + a["token"]},
                    json={"goal": ""})
    assert r.status_code == 400


def test_rt_t7_dev_endpoints_mock_gated(client):
    r = client.get("/api/payments/dev-complete/1/pro")
    assert r.status_code == 404  # mock off -> sealed
    r2 = client.get("/mock-checkout/1/pro")
    assert r2.status_code == 404


# ---------------- SHARED DEMO SURFACE (FLX-DEMO-1) ----------------

def test_rt_t8_demo_board_shared_and_unpriced(client):
    b = _reg(client, "demoguy@x.com")
    credits_before = db_mod.get_user_credits(b["user"]["id"])
    # any authenticated user can read any flux-demo-* board (guard is prefix-only)
    r = client.get("/api/projects/flux-demo-9999999999/tasks",
                   headers={"Authorization": "Bearer " + b["token"]})
    assert r.status_code == 200, r.status_code  # NOT 403
    # and can dispatch it without paying credits / no per-endpoint limit
    for _ in range(3):
        r2 = client.post("/api/projects/flux-demo-9999999999/dispatch",
                         headers={"Authorization": "Bearer " + b["token"]})
        assert r2.status_code == 200, r2.status_code
    assert db_mod.get_user_credits(b["user"]["id"]) == credits_before  # no debit


def test_rt_t9_ws_fail_closed(client):
    with client.websocket_connect("/ws/flux-demo-1") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["detail"] == "unauthorized"


# ---------------- PATH / AGENT-ID INJECTION ----------------

def test_rt_p1_traversal_slug_rejected(client):
    a = _reg(client, "p1@x.com")
    for slug in ("u1-../../etc/passwd", "u999999-x; rm -rf /", "..%2f..%2fsecret"):
        r = client.get(f"/api/projects/{slug}/tasks",
                       headers={"Authorization": "Bearer " + a["token"]})
        # 403 = ownership guard; 404 = the slash never routes to {slug} (also OK)
        assert r.status_code in (403, 404), r.status_code


def test_rt_p2_unknown_template_agent_rejected(client):
    a = _reg(client, "p2@x.com")
    r = client.post("/api/templates", headers={"Authorization": "Bearer " + a["token"]},
                    json={"name": "Bad", "agents": ["Planner", "evil-agent"], "price_credits": 3})
    assert r.status_code == 400  # agent allowlisted via AGENT_REGISTRY


# ---------------- XSS hardening ----------------

def test_rt_x1_scriptable_template_blocked(client):
    a = _reg(client, "x1@x.com")
    for evil in ('<script>alert(1)</script>', 'onerror=alert(1)', 'javascript:alert(1)'):
        r = client.post("/api/templates", headers={"Authorization": "Bearer " + a["token"]},
                        json={"name": evil, "agents": ["Planner"], "price_credits": 2})
        assert r.status_code == 400, (evil, r.status_code)


def test_rt_x2_project_name_html_not_reflected_unsafe(client):
    a = _reg(client, "x2@x.com")
    r = client.post("/api/projects", headers={"Authorization": "Bearer " + a["token"]},
                    json={"name": '<img src=x onerror=alert(1)>', "goal": "ok goal"})
    assert r.status_code == 200  # stored as data; frontend escapes with esc()


# ---------------- WEBHOOK SECURITY ----------------

def _paddle_env(monkeypatch):
    monkeypatch.setenv("FLUXSWARM_PAYMENT_PROVIDER", "paddle")
    monkeypatch.setenv("PADDLE_API_BASE", "https://sandbox-api.paddle.com")
    monkeypatch.setenv("PADDLE_API_KEY", "sdbx_test")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "whsec-test")
    monkeypatch.setenv("PADDLE_PRICE_STARTER", "pri_test_starter")
    monkeypatch.setenv("PADDLE_PRICE_PRO", "pri_test_pro")


def _v2sig(secret, body, ts=None):
    ts = ts if ts is not None else time.time()
    h1 = hmac.new(secret.encode(), f"paddle-{int(ts)};{body.decode('utf-8')}".encode(),
                  hashlib.sha256).hexdigest()
    return f"ts={int(ts)};h1={h1}"


def _completed_payload(user_id, plan, txn="txn_1", event="evt_1"):
    return json.dumps({
        "event_id": event, "event_type": "transaction.completed",
        "data": {"id": txn, "status": "completed",
                 "custom_data": {"user_id": str(user_id), "plan": plan},
                 "total": {"amount": 2900, "currency": "USD"},
                 "items": [{"price": {"id": "pri_test_starter"}}]},
        "metadata": {},
    }).encode()


def test_rt_w1_unsigned_rejected(client, monkeypatch):
    _paddle_env(monkeypatch)
    r = client.post("/api/payments/webhook", content=_completed_payload(1, "starter"),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400, r.status_code


def test_rt_w2_signed_grants_and_replay_idempotent(client, monkeypatch):
    _paddle_env(monkeypatch)
    u = _reg(client, "pay1@x.com")
    uid = u["user"]["id"]
    body = _completed_payload(uid, "starter", txn="txn_2", event="evt_2")
    r = client.post("/api/payments/webhook", content=body,
                    headers={"Paddle-Signature": _v2sig("whsec-test", body),
                             "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    u2 = db_mod.get_user_by_id(uid)
    assert u2["plan"] == "starter" and u2["credits"] == max(3, 25)
    # exact replay -> deduplicated, credits unchanged
    r2 = client.post("/api/payments/webhook", content=body,
                     headers={"Paddle-Signature": _v2sig("whsec-test", body),
                              "Content-Type": "application/json"})
    assert r2.status_code == 200 and r2.json()["deduplicated"] is True
    assert db_mod.get_user_by_id(uid)["credits"] == 25


def test_rt_w3_stale_signature_rejected(client, monkeypatch):
    _paddle_env(monkeypatch)
    u = _reg(client, "pay2@x.com")
    body = _completed_payload(u["user"]["id"], "starter", txn="txn_3", event="evt_3")
    sig = _v2sig("whsec-test", body, ts=time.time() - 3600)
    r = client.post("/api/payments/webhook", content=body,
                    headers={"Paddle-Signature": sig, "Content-Type": "application/json"})
    assert r.status_code == 400, r.status_code


def test_rt_w4_wrong_secret_rejected(client, monkeypatch):
    _paddle_env(monkeypatch)
    u = _reg(client, "pay3@x.com")
    body = _completed_payload(u["user"]["id"], "starter", txn="txn_4", event="evt_4")
    r = client.post("/api/payments/webhook", content=body,
                    headers={"Paddle-Signature": _v2sig("whsec-wrong", body),
                             "Content-Type": "application/json"})
    assert r.status_code == 400 and db_mod.get_user_by_id(u["user"]["id"])["plan"] == "demo"


def test_rt_w5_refund_downgrades_keeps_credits(client, monkeypatch):
    _paddle_env(monkeypatch)
    u = _reg(client, "pay4@x.com")
    uid = u["user"]["id"]
    body = _completed_payload(uid, "pro", txn="txn_5", event="evt_5")
    client.post("/api/payments/webhook", content=body,
                headers={"Paddle-Signature": _v2sig("whsec-test", body),
                         "Content-Type": "application/json"})
    ref = json.dumps({
        "event_id": "evt_adj_1", "event_type": "adjustment.created",
        "data": {"type": "refund", "transaction_id": "txn_5", "status": "completed"},
        "metadata": {},
    }).encode()
    r = client.post("/api/payments/webhook", content=ref,
                    headers={"Paddle-Signature": _v2sig("whsec-test", ref),
                             "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    u2 = db_mod.get_user_by_id(uid)
    assert u2["plan"] == "demo" and u2["credits"] == 120  # credits kept


# ---------------- ECONOMICS ----------------

def test_rt_e1_insufficient_credit_402(client):
    a = _reg(client, "econ@x.com")
    c = db_mod.get_user_by_id(a["user"]["id"])["credits"]
    assert c == 3  # demo allowance
    r = client.post("/api/subscribe/pro", headers={"Authorization": "Bearer " + a["token"]})
    assert r.status_code == 402  # gate closed -> no free upgrade


def test_rt_e2_dispatch_unlimited_on_shared_board(client):
    b = _reg(client, "spam@x.com")
    for _ in range(6):
        r = client.post("/api/projects/flux-demo-123/dispatch",
                        headers={"Authorization": "Bearer " + b["token"]})
        assert r.status_code == 200
    # no 429, no credit debit -> unbounded platform-cost windows (FLX-DEMO-1)


def test_rt_e3_account_delete_removes_owned_rows(client):
    a = _reg(client, "del@x.com")
    a["token"] and client.post("/api/projects", headers={"Authorization": "Bearer " + a["token"]},
                               json={"name": "P", "goal": "g"})
    uid = a["user"]["id"]
    r = client.delete("/api/account", headers={"Authorization": "Bearer " + a["token"]})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert db_mod.get_user_by_id(uid) is None
    assert len(db_mod.list_user_projects(uid)) == 0  # projects erased (boards are not; see Phase 6)


if __name__ == "__main__":
    import shutil
    pytest.main([__file__, "-v", "-q", "--tb=short"])
    shutil.rmtree(_TMP, ignore_errors=True)