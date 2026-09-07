"""
Focused unit tests for FluxSwarm backend logic that does NOT require the
Hermes CLI (hermes.exe). These cover the security/correctness fixes made
during the architecture review:

  - auth: JWT round-trip + rejection of bad/tampered tokens
  - db:   atomic credit debit, non-clawback plan upgrade,
          race-safe template purchase, one-time referral reward
  - ratelimit: in-process limiter counters
  - security: static secret/injection scan
  - vault: encrypt/decrypt round-trip (Fernet), key masking
  - schemas: ProjectCreate input validation

The real data/users.db is never mutated: every DB test points the module at a
temporary SQLite file.
"""
from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path

import pytest


# ----------------------------------------------------------------------------
# auth
# ----------------------------------------------------------------------------
def test_auth_token_roundtrip():
    import auth
    tok = auth.make_token({"id": 7, "email": "a@b.co", "plan": "demo"})
    payload = auth.decode_token(tok)
    assert payload["uid"] == 7
    assert payload["email"] == "a@b.co"
    assert payload["plan"] == "demo"
    # exp present and in the future
    import time
    assert payload["exp"] > time.time()


def test_auth_token_rejects_garbage():
    import auth
    assert auth.decode_token("not.a.jwt") is None
    assert auth.decode_token("") is None
    assert auth.decode_token(None) is None  # type: ignore[arg-type]


def test_auth_token_rejects_tamper():
    import auth
    tok = auth.make_token({"id": 1, "email": "x@y.z", "plan": "demo"})
    bad = tok[:-3] + ("aaa" if not tok.endswith("aaa") else "bbb")
    assert auth.decode_token(bad) is None


# ----------------------------------------------------------------------------
# db (isolated temp database)
# ----------------------------------------------------------------------------
@pytest.fixture
def tmp_db(monkeypatch):
    import db
    importlib.reload(db)  # reset module-level state
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    monkeypatch.setattr(db, "DB", Path(path))
    db.init_db()
    db.seed_demo()
    yield db
    Path(path).unlink(missing_ok=True)


def test_deduct_credit_atomic(tmp_db):
    u = tmp_db.create_user("credit@test.co", "Credit", "password123")
    before = tmp_db.get_user_by_id(u["id"])["credits"]
    assert before > 0
    # spend down to zero
    while tmp_db.deduct_credit(u["id"]):
        pass
    assert tmp_db.get_user_by_id(u["id"])["credits"] == 0
    # further deduct returns False, balance stays 0 (no negative)
    assert tmp_db.deduct_credit(u["id"]) is False
    assert tmp_db.get_user_by_id(u["id"])["credits"] == 0


def test_upgrade_plan_no_credit_clawback(tmp_db):
    u = tmp_db.create_user("up@test.co", "Up", "password123")
    # give the user extra credits earned elsewhere
    c = tmp_db._conn()
    c.execute("UPDATE users SET credits=? WHERE id=?", (100, u["id"]))
    c.commit()
    c.close()
    tmp_db.upgrade_plan(u["id"], "starter")  # starter allowance is 20
    after = tmp_db.get_user_by_id(u["id"])
    assert after["plan"] == "starter"
    # credits preserved, never reset to the lower plan allowance
    assert after["credits"] == 100
    # re-subscribing to the same tier does not refill
    tmp_db.upgrade_plan(u["id"], "starter")
    assert tmp_db.get_user_by_id(u["id"])["credits"] == 100


def test_buy_template_insufficient_funds(tmp_db):
    buyer = tmp_db.create_user("buyer@test.co", "Buyer", "password123")
    author = tmp_db.create_user("author@test.co", "Author", "password123")
    c = tmp_db._conn()
    c.execute("UPDATE users SET credits=? WHERE id=?", (3, buyer["id"]))
    c.commit()
    c.close()
    tid = tmp_db.publish_template(author["id"], "T", "desc", ["Planner"], price_credits=10)
    # buyer has 3 credits, template costs 10 -> should fail atomically
    assert tmp_db.buy_template(tid, buyer["id"]) is False
    # no purchase recorded, buyer balance unchanged
    assert tmp_db.get_user_by_id(buyer["id"])["credits"] == 3
    rows = c.execute("SELECT * FROM template_purchases").fetchall() if (c := tmp_db._conn()) else []
    assert len(rows) == 0
    c.close()


def test_buy_template_atomic_transfer(tmp_db):
    buyer = tmp_db.create_user("b2@test.co", "B2", "password123")
    author = tmp_db.create_user("a2@test.co", "A2", "password123")
    c = tmp_db._conn()
    c.execute("UPDATE users SET credits=? WHERE id=?", (50, buyer["id"]))
    c.commit()
    c.close()
    tid = tmp_db.publish_template(author["id"], "T2", "desc", ["Planner"], price_credits=10)
    assert tmp_db.buy_template(tid, buyer["id"]) is True
    assert tmp_db.get_user_by_id(buyer["id"])["credits"] == 40  # 50 - 10
    # author starts with the demo plan's 5 credits, then earns 50% of price (5)
    assert tmp_db.get_user_by_id(author["id"])["credits"] == 10  # 5 + 5
    c = tmp_db._conn()
    rows = c.execute("SELECT * FROM template_purchases").fetchall()
    c.close()
    assert len(rows) == 1


def test_referral_rewarded_once(tmp_db):
    referrer = tmp_db.create_user("ref@test.co", "Ref", "password123")
    ref_code = tmp_db.get_user_by_id(referrer["id"])["ref_code"]
    referred = tmp_db.create_user("referred@test.co", "Refd", "password123", ref_code)
    # sanity: a referral row exists
    assert referred["referred_by"] == ref_code
    ok1 = tmp_db.reward_referrer_once("referred@test.co")
    assert ok1 is True
    after_first = tmp_db.get_user_by_id(referrer["id"])["credits"]
    # a second reward for the same email must not pay again
    ok2 = tmp_db.reward_referrer_once("referred@test.co")
    assert ok2 is False
    assert tmp_db.get_user_by_id(referrer["id"])["credits"] == after_first


# ----------------------------------------------------------------------------
# ratelimit (in-process backend)
# ----------------------------------------------------------------------------
def test_ratelimit_login_burst():
    from ratelimit import _Limiter
    lim = _Limiter()
    ip, email = "1.2.3.4", "x@y.co"
    for _ in range(5):
        assert lim.login_allowed(ip, email) is True
        lim.record_login_failure(ip, email)
    # 6th attempt is blocked
    assert lim.login_allowed(ip, email) is False
    lim.clear_login_failures(ip, email)
    assert lim.login_allowed(ip, email) is True


def test_ratelimit_ip_and_registration():
    from ratelimit import _Limiter
    lim = _Limiter()
    assert lim.ip_allowed("9.9.9.9") is True
    for _ in range(20):
        lim.hit_ip("9.9.9.9")
    assert lim.ip_allowed("9.9.9.9") is False
    assert lim.register_allowed("8.8.8.8") is True
    for _ in range(10):
        lim.record_registration("8.8.8.8")
    assert lim.register_allowed("8.8.8.8") is False


def test_ratelimit_purchase_burst():
    from ratelimit import _Limiter
    lim = _Limiter()
    uid = 42
    for _ in range(5):
        assert lim.purchase_allowed(uid) is True
        lim.record_purchase(uid)
    assert lim.purchase_allowed(uid) is False


# ----------------------------------------------------------------------------
# security static scan
# ----------------------------------------------------------------------------
def test_security_static_scan_detects_leaks():
    from security import _static_scan, severity_label
    code = 'aws = "AKIA1234567890ABCDEF"\nkey = "sk-ant-abcdefghijklmnopqrst"\n'
    res = _static_scan(code)
    types = {f["rule"] for f in res["findings"]}
    assert "aws_key" in types
    assert "anthropic_key" in types
    assert res["score"] < 100
    assert severity_label(res["score"]) in ("minor", "needs_review", "dangerous")


def test_security_static_scan_clean():
    from security import _static_scan, severity_label
    res = _static_scan("def add(a, b):\n    return a + b\n")
    assert res["findings"] == []
    assert res["score"] == 100
    assert severity_label(100) == "secure"


# ----------------------------------------------------------------------------
# vault encrypt/decrypt
# ----------------------------------------------------------------------------
def test_vault_roundtrip():
    import vault
    importlib.reload(vault)
    vault.set_user_key(123, "anthropic", "sk-secret-token-xyz")
    got = vault.get_user_key(123, "anthropic")
    assert got == "sk-secret-token-xyz"
    # unknown provider returns None
    assert vault.get_user_key(123, "does-not-exist") is None
    # masking keeps only a prefix and suffix
    masked = vault.mask_key("sk-abcdefghijklmnop")
    assert masked == "sk-a…mnop"
    assert "*" not in vault.get_user_key(123, "anthropic")


# ----------------------------------------------------------------------------
# schemas (input validation)
# ----------------------------------------------------------------------------
def test_project_create_validation():
    import main  # noqa: F401  (imports app + schema definitions)
    from main import ProjectCreate
    from pydantic import ValidationError
    # valid
    ok = ProjectCreate(name="Demo", goal="Build a notes API")
    assert ok.name == "Demo"
    # empty goal rejected (min_length=1)
    with pytest.raises(ValidationError):
        ProjectCreate(name="X", goal="")
    # goal too long rejected (max_length=4000)
    with pytest.raises(ValidationError):
        ProjectCreate(name="X", goal="a" * 4001)
    # name too long rejected (max_length=120)
    with pytest.raises(ValidationError):
        ProjectCreate(name="a" * 121, goal="ok")


# ----------------------------------------------------------------------------
# Stage 1 security fixes
# ----------------------------------------------------------------------------
def test_cors_allows_no_wildcard(monkeypatch):
    """A '*' in the CORS allow-list must be rejected (startup guard), and a
    concrete list must be passed through verbatim (no wildcard ever reaches
    the middleware)."""
    from fastapi.middleware.cors import CORSMiddleware

    import main as main_mod

    # wildcard is forbidden -> hard error at parse time
    monkeypatch.setenv("FLUXSWARM_CORS_ORIGINS", "https://app.fluxswarm.ai,*")
    with pytest.raises(RuntimeError):
        main_mod._load_cors_origins()

    # explicit origins accepted; no "*" present
    monkeypatch.setenv("FLUXSWARM_CORS_ORIGINS", "https://app.fluxswarm.ai,https://admin.fluxswarm.ai")
    origins = main_mod._load_cors_origins()
    assert origins == ["https://app.fluxswarm.ai", "https://admin.fluxswarm.ai"]
    assert "*" not in origins

    # unset => empty (same-origin only)
    monkeypatch.delenv("FLUXSWARM_CORS_ORIGINS", raising=False)
    assert main_mod._load_cors_origins() == []

    # the app must register the CORS middleware (so the allow-list is enforced)
    cors_mw = [m for m in main_mod.app.user_middleware
               if getattr(m, "cls", None) is CORSMiddleware]
    assert cors_mw, "CORS middleware must be registered"


def test_trusted_proxy_xff_only_from_proxy(monkeypatch):
    """X-Forwarded-For must be ignored unless the immediate peer is a configured
    trusted proxy. An untrusted client forging XFF must NOT be able to spoof
    their IP (which would let them evade the per-IP rate limiter).

    The canonical _peer_is_trusted reads the TRUSTED_PROXIES module global
    (parsed ipaddress network objects), so we monkeypatch that global.
    """
    import main as main_mod
    import ipaddress

    monkeypatch.setattr(
        main_mod, "TRUSTED_PROXIES",
        [ipaddress.ip_network("10.0.0.1"), ipaddress.ip_network("10.0.0.0/8")],
    )

    class _Req:
        def __init__(self, peer, xff=None):
            self.client = type("C", (), {"host": peer})()
            self.headers = {}
            if xff is not None:
                self.headers["x-forwarded-for"] = xff

    # Trusted proxy peers -> leftmost XFF used.
    assert main_mod._client_ip(_Req("10.0.0.5", "203.0.113.9, 10.0.0.5")) == "203.0.113.9"
    assert main_mod._client_ip(_Req("10.1.2.3", "198.51.100.7")) == "198.51.100.7"
    # Untrusted peer (direct client) forging XFF -> XFF ignored, real peer used.
    assert main_mod._client_ip(_Req("192.0.2.50", "6.6.6.6")) == "192.0.2.50"
    # No trusted proxies configured -> XFF never trusted.
    monkeypatch.setattr(main_mod, "TRUSTED_PROXIES", [])
    assert main_mod._client_ip(_Req("192.0.2.99", "6.6.6.6")) == "192.0.2.99"


def test_create_project_slug_has_entropy(monkeypatch):
    """Project slug must carry random entropy and be ownership-prefixed, so it
    is not enumerable by guessing u{id}-{timestamp}."""
    import main as main_mod

    got = []
    monkeypatch.setattr(main_mod.secrets, "token_hex",
                        lambda n: (got.append(n) or "deadbeef"))
    slug = main_mod.make_project_slug(42)
    assert slug.startswith("u42-")
    assert slug.endswith("-deadbeef")
    assert "deadbeef" in slug  # random component present
    assert got and got[0] > 0


def test_ws_token_rejects_missing_and_bad():
    """WS auth must reject (a) no token, (b) a malformed/unsigned token, and
    (c) a token whose uid maps to a deleted/absent user — fail closed, never
    treat as anonymous. We exercise the auth helper directly (the WS handler
    shares the same decode + user-existence checks)."""
    import auth as auth_mod
    import db as db_mod

    # token decoding helpers must never raise; they return None on garbage.
    assert auth_mod.decode_token("") is None
    assert auth_mod.decode_token("garbage") is None

    # uid that exists in the test DB must resolve; a bogus uid must not.
    demo = db_mod.get_user_by_id(1)
    assert demo is not None
    assert db_mod.get_user_by_id(999999) is None

    # A raw dict is not a valid HS256 JWT, so decode returns None — replicating
    # the WS handler's `not payload` reject branch (never treated as anonymous).
    fake = {"uid": 1, "email": "x@y.z", "plan": "demo", "exp": 9999999999}
    assert auth_mod.decode_token(str(fake)) is None


# ----------------------------------------------------------------------------
# Stage 3: pluggable payment gateway (dev stub)
# ----------------------------------------------------------------------------
def test_payment_gateway_stub_is_safe_default():
    """The default gateway is the no-op StubGateway: it never reports a paid
    session, so the dev 402 gate can never be bypassed by a real charge."""
    import payments as payments_mod

    gw = payments_mod.get_gateway()
    assert gw.name == "stub"
    sess = gw.create_checkout(plan="pro", user_id=1, amount_cents=9900)
    assert sess.status == "open"
    assert gw.is_paid(sess.id) is False
    # webhook parsing is side-effect-free and signature-aware
    rc = gw.handle_webhook(b'{"event":"checkout"}', signature="sig")
    assert rc["gateway"] == "stub"
    assert rc["signature_ok"] is True


def test_payment_gateway_interface_contract():
    """Every PaymentGateway subclass must honour the abstract contract."""
    import payments as payments_mod
    from payments import CheckoutSession

    class FakeGW(payments_mod.PaymentGateway):
        name = "fake"

        def create_checkout(self, *, plan, user_id, amount_cents, currency="usd"):
            return CheckoutSession(id=f"fk-{user_id}", status="open")

        def is_paid(self, session_id):
            return session_id == "paid-session"

        def handle_webhook(self, payload, signature=None):
            return {"ok": True}

    gw = FakeGW()
    assert isinstance(gw.create_checkout(plan="pro", user_id=7, amount_cents=100), CheckoutSession)
    assert gw.is_paid("paid-session") is True
    assert gw.is_paid("other") is False
