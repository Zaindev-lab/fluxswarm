"""
FluxSwarm backend - FastAPI server (auth + squad + plans + referrals + demo).

Each authenticated user owns an isolated set of Hermes kanban boards (prefixed
by their user id). The ECC devops squad (swarm) is launched per project.
"""
from __future__ import annotations

import asyncio
import sys
import atexit
import ipaddress
import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import audit
import auth as auth_mod
import db
import hermes_client as hc
import serverlock
import vault
import security
import payments as payments_mod
from ratelimit import limiter

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
app = FastAPI(title="FluxSwarm", version="0.2.0")

# ---------- CORS (strict allow-list; never a wildcard) ----------
# Origins come from FLUXSWARM_CORS_ORIGINS (comma/whitespace-separated). If unset
# or empty, NO CORS middleware is registered at all — same-origin only,
# deny-by-default. We never fall back to "*": an open allow-list lets any site
# read responses (cookies/Bearer) and defeats the browser same-origin policy.
def _load_cors_origins() -> list[str]:
    """Resolve the CORS allow-list from FLUXSWARM_CORS_ORIGINS.

    A ``*`` wildcard is a hard error (RuntimeError) — an open allow-list lets
    any origin read responses (cookies/Bearer) and defeats the browser
    same-origin policy. Empty/unset => same-origin only (explicit empty list,
    which the middleware treats as 'no cross-origin origin permitted').
    """
    raw = os.environ.get("FLUXSWARM_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    origins = [o.strip() for o in raw.replace("\n", ",").split(",") if o.strip()]
    if "*" in origins:
        raise RuntimeError(
            "FLUXSWARM_CORS_ORIGINS must not contain '*' (open allow-list). "
            "List explicit origins only."
        )
    return origins

CORS_ALLOW_ORIGINS = _load_cors_origins()

# Always register the CORS middleware. With an empty allow-list (env unset) it
# denies every cross-origin request (same-origin only) — strict by default.
# A "*" can never appear: _cors_allow_origins() strips wildcards, so we never
# open the API to every origin nor combine a wildcard with credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,       # explicit list only — never "*"
    allow_credentials=bool(CORS_ALLOW_ORIGINS),  # no credentials unless origins are set
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
    expose_headers=["X-Requested-With"],
    max_age=600,
)

# ---------------------------------------------------------------------------
# Trusted proxy list for X-Forwarded-For. The client IP derived from
# X-Forwarded-For is only trustworthy when the request actually came through a
# configured reverse proxy — otherwise a client can spoof it and evade the
# per-IP rate limiter. Set FLUXSWARM_TRUSTED_PROXIES to the proxy IPs/CIDRs
# (comma-separated); if unset, XFF is ignored and request.client.host is used.
# ---------------------------------------------------------------------------
def _trusted_proxies() -> list[object]:
    """Parse FLUXSWARM_TRUSTED_PROXIES into exact IPs and CIDR networks.

    Each entry is either a single IPv4/IPv6 address or a CIDR (e.g. 10.0.0.0/8).
    A malformed entry is skipped (and logged) rather than silently trusting
    everything. Returns a list of ipaddress network objects. Reads the env
    live so callers that set the variable without reloading the module still
    see the change.
    """
    raw = os.environ.get("FLUXSWARM_TRUSTED_PROXIES", "").strip()
    if not raw:
        return []
    out: list[object] = []
    for p in raw.replace("\n", ",").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            out.append(ipaddress.ip_network(p, strict=False))
        except ValueError:
            print(f"[proxy] ignoring invalid FLUXSWARM_TRUSTED_PROXIES entry: {p!r}",
                  file=sys.stderr)
    return out


TRUSTED_PROXIES = _trusted_proxies()


def _peer_is_trusted(peer: str | None, nets: list[object] | None = None) -> bool:
    """True only if `peer` is a configured trusted proxy (exact IP or in CIDR).

    ``nets`` is the parsed trusted-proxy list (defaults to the module-level
    ``TRUSTED_PROXIES`` global). Callers/tests may pass plain-IP strings too,
    so the membership check tolerates both network objects and str entries.
    """
    nets = TRUSTED_PROXIES if nets is None else nets
    if not peer or not nets:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for net in nets:
        if isinstance(net, str):
            try:
                if addr == ipaddress.ip_address(net):
                    return True
            except ValueError:
                continue
        else:
            try:
                if addr in net:
                    return True
            except TypeError:
                continue
    return False

# Single instance: refuse a second live backend (port races / double superv  is
# what used to spawn orphan uvicorn processes). Bypass with FLUXSWARM_ALLOW_MULTI=1.
serverlock.acquire()
atexit.register(serverlock.release)

_START_TS = time.time()

_SUBS: dict[str, set[WebSocket]] = {}

def _payments_enabled() -> bool:
    """Read the billing gate live (not at import time) so tests / ops can switch
    FLUXSWARM_PAYMENTS without a module reload."""
    return os.environ.get("FLUXSWARM_PAYMENTS", "").strip().lower() in ("1", "true", "yes")


# ---------- security headers ----------
# Paddle Checkout overlay needs: SDK script (cdn.paddle.com), its iframe
# (checkout / sandbox-checkout), and API calls (api.paddle.com).
_PADDLE_ORIGINS = "https://cdn.paddle.com https://*.paddle.com"
_CSP = ("default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.paddle.com; "
        "style-src 'self' 'unsafe-inline' https://*.paddle.com; "
        "img-src 'self' data: https://*.paddle.com; "
        "font-src 'self' data: https://*.paddle.com; "
        "connect-src 'self' ws: wss: https://*.paddle.com wss://checkout.paddle.com; "
        "frame-src https://checkout.paddle.com https://sandbox-checkout.paddle.com "
        "https://buy.paddle.com https://sandbox-buy.paddle.com; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = _CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/health")
def api_health():
    db_ok = True
    try:
        db.get_user_by_id(1)
    except Exception:
        db_ok = False
    return {
        "ok": db_ok and hc.HERMES_BIN.exists(),
        "version": app.version,
        "pid": os.getpid(),
        "uptime_s": round(time.time() - _START_TS, 1),
        "db": db_ok,
        "hermes_bin": str(hc.HERMES_BIN),
        "hermes_bin_ok": hc.HERMES_BIN.exists(),
        "limiter_backend": getattr(limiter, "backend", "memory"),
    }


# ---------- background squad dispatch ----------
def _bg_dispatch(slug: str, plan: str, provider_keys=None) -> None:
    """One non-blocking dispatcher pass in a daemon thread (never holds the
    HTTP request hostage for the up-to-10-minute swarm run)."""
    try:
        hc.dispatch(slug, max_spawn=db.PLANS.get(plan, {}).get("parallel", 1),
                    provider_keys=provider_keys, blocking=False)
    except Exception as e:
        # Surface the failure for ops instead of silently dropping the swarm.
        try:
            audit.audit("dispatch.fire", outcome="error", slug=slug, reason=type(e).__name__)
        except Exception:
            pass


def _fire_dispatch(slug: str, plan: str, provider_keys=None) -> None:
    threading.Thread(target=_bg_dispatch, args=(slug, plan),
                     kwargs={"provider_keys": provider_keys}, daemon=True).start()


# ---------- auth dependency ----------
def get_current_user_optional(request: Request) -> dict | None:
    ah = request.headers.get("Authorization", "")
    token = ah.replace("Bearer ", "") if ah.startswith("Bearer ") else request.cookies.get("fs_token")
    if not token:
        return None
    payload = auth_mod.decode_token(token)
    if not payload:
        return None
    return db.get_user_by_id(payload["uid"])


def get_current_user(request: Request) -> dict:
    ah = request.headers.get("Authorization", "")
    token = ah.replace("Bearer ", "") if ah.startswith("Bearer ") else request.cookies.get("fs_token")
    if not token:
        raise HTTPException(status_code=401, detail="غير مصرّح")
    payload = auth_mod.decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="جلسة غير صالحة")
    user = db.get_user_by_id(payload["uid"])
    if not user:
        raise HTTPException(status_code=401, detail="مستخدم غير موجود")
    return user


# ---------- schemas ----------
class RegisterIn(BaseModel):
    email: str
    name: str
    password: str
    ref: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=4000)
    ref: str | None = None


# ---------- marketing / public ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"title": "FluxSwarm"})


@app.get("/api/plans")
def api_plans():
    return [{"id": p, **db.PLANS[p]} for p in db.PLAN_ORDER]


@app.get("/api/squad")
def api_squad():
    return {
        "workers": [{"profile": p, "display": d, "role": r, "skills": s.split(",")}
                    for p, d, r, s in hc.SQUAD],
        "verifier": {"profile": hc.VERIFIER[0], "display": hc.VERIFIER[1], "role": hc.VERIFIER[2]},
        "synthesizer": {"profile": hc.SYNTHESIZER[0], "display": hc.SYNTHESIZER[1], "role": hc.SYNTHESIZER[2]},
    }


@app.get("/api/demo/launch")
def api_demo_launch(request: Request):
    """Pre-seeded instant demo: launches a sample swarm under the demo user.

    Returns immediately; the dispatcher pass runs in a background thread so the
    request never blocks for the (minutes-long) swarm. max_spawn honours the
    demo user's own plan cap (demo -> parallel 1), not a fixed 8.

    A per-IP burst cap prevents anonymous abuse (each launch runs a real,
    compute-costly Hermes swarm).
    """
    if not limiter.ip_allowed(_client_ip(request)):
        raise HTTPException(status_code=429, detail="محاولات كثيرة جداً — انتظر قليلاً")
    demo = db.get_user_by_id(1) or db.get_user_by_ref("demo")
    if demo is None:
        # db.seed_demo() runs at import, but guard anyway: a missing demo user
        # must not crash the endpoint (it would 500 on `demo.get(...)`).
        return {"error": "demo_user_missing", "demo": True}
    plan = demo.get("plan", "demo")
    goal = "Build a sample FastAPI notes API with tests and CI/CD (DEMO)"
    slug = "flux-demo-" + str(int(time.time()))
    hc.ensure_board(slug)
    swarm = hc.launch_swarm(slug, goal)
    _fire_dispatch(slug, plan)
    audit.audit("demo.launch", uid=demo["id"] if demo else None, ip="internal",
                outcome="ok", slug=slug, plan=plan)
    return {"slug": slug, "root_id": swarm.root_id, "demo": True}


# ---------- auth ----------
def _client_ip(request: Request) -> str:
    """Resolve the real client IP for rate limiting / audit.

    X-Forwarded-For is ONLY honoured when the immediate peer is a configured
    trusted proxy (FLUXSWARM_TRUSTED_PROXIES, which accepts exact IPs or CIDRs).
    Otherwise any client could forge XFF and evade the per-IP rate limiter. When
    the peer is not a trusted proxy we use the socket peer (request.client.host),
    which cannot be spoofed from the network.
    """
    xff = request.headers.get("x-forwarded-for")
    # request.client.host can be an IPv4Address object (Starlette) OR a str
    # depending on version; coerce to str so the trusted-proxy check and the
    # returned value are always a plain IP string (never an object repr).
    peer_obj = request.client.host if request.client else None
    peer = str(peer_obj) if peer_obj is not None else None
    if xff and _peer_is_trusted(peer):
        return xff.split(",")[0].strip()
    return peer or "unknown"


def make_project_slug(user_id: int) -> str:
    """Build an unguessable, ownership-prefixed board slug for a new project.

    Format: ``u{user_id}-{epoch_seconds}-{random16}``. The trailing
    ``secrets.token_hex(4)`` (16 bits of entropy per minute, more across time)
    makes the slug non-sequential and unenumerable, so an attacker cannot probe
    another user's boards by guessing ``u2-<timestamp>``. The ``u{user_id}-``
    prefix is what the ownership guard (``slug.startswith(f"u{uid}-")``) relies on.
    """
    return f"u{user_id}-{int(time.time())}-{secrets.token_hex(4)}"


@app.post("/api/auth/register")
def api_register(p: RegisterIn, request: Request):
    ip = _client_ip(request)
    limiter.hit_ip(ip)
    if not limiter.ip_allowed(ip):
        raise HTTPException(status_code=429, detail="محاولات كثيرة جداً — انتظر قليلاً")
    if not limiter.register_allowed(ip):
        raise HTTPException(status_code=429, detail="تجاوزت حد التسجيل — جرب لاحقاً")
    email = p.email.lower().strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="بريد إلكتروني غير صالح")
    if len(p.password) < 8:
        raise HTTPException(status_code=400, detail="كلمة المرور قصيرة جداً (8 أحرف على الأقل)")
    if not p.name or not p.name.strip():
        raise HTTPException(status_code=400, detail="الاسم مطلوب")
    try:
        user = db.create_user(p.email, p.name, p.password, p.ref)
    except ValueError as e:
        audit.audit("auth.register", email=p.email, ip=ip, outcome="fail",
                    reason="email_taken")
        raise HTTPException(status_code=409, detail=str(e))
    limiter.record_registration(ip)
    token = auth_mod.make_token(user)
    audit.audit("auth.register", uid=user["id"], email=user["email"], ip=ip, outcome="ok")
    return {"token": token, "user": public_user(user)}


@app.post("/api/auth/login")
def api_login(p: LoginIn, request: Request):
    ip = _client_ip(request)
    email = p.email.lower().strip()
    limiter.hit_ip(ip)
    if not limiter.ip_allowed(ip):
        raise HTTPException(status_code=429, detail="محاولات كثيرة جداً — انتظر قليلاً")
    if not limiter.login_allowed(ip, email):
        audit.audit("auth.login", email=email, ip=ip, outcome="fail", reason="locked")
        raise HTTPException(status_code=429, detail="محاولات دخول فاشلة متكررة — انتظر قبل المحاولة مرة أخرى")
    user = db.authenticate(p.email, p.password)
    if not user:
        limiter.record_login_failure(ip, email)
        audit.audit("auth.login", email=email, ip=ip, outcome="fail", reason="bad_credentials")
        raise HTTPException(status_code=401, detail="بيانات غير صحيحة")
    limiter.clear_login_failures(ip, user["email"])
    token = auth_mod.make_token(user)
    audit.audit("auth.login", uid=user["id"], email=user["email"], ip=ip, outcome="ok")
    return {"token": token, "user": public_user(user)}


@app.get("/api/me")
def api_me(user: dict = Depends(get_current_user)):
    return public_user(user)


def public_user(u: dict) -> dict:
    return {
        "id": u["id"], "email": u["email"], "name": u["name"],
        "plan": u["plan"], "credits": u["credits"], "ref_code": u["ref_code"],
    }


# ---------- user projects (auth required) ----------
@app.get("/api/projects")
def api_projects(user: dict = Depends(get_current_user)):
    return db.list_user_projects(user["id"])


@app.post("/api/projects")
def api_create_project(payload: ProjectCreate, request: Request,
                       user: dict = Depends(get_current_user)):
    # Basic input validation (goal drives a subprocess launch).
    goal = (payload.goal or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="اكتب هدف البناء")
    if len(goal) > 4000:
        raise HTTPException(status_code=400, detail="الهدف أطول من الحد المسموح")
    # Credit gating: each launch costs 1 credit.
    if not db.deduct_credit(user["id"]):
        raise HTTPException(status_code=402, detail="نفدت الرصيد — حدّث باقتك أو استخدم رمز إحالة")
    slug = make_project_slug(user["id"])
    hc.ensure_board(slug)
    try:
        swarm = hc.launch_swarm(slug, goal, provider_keys=_user_provider_keys(user))
    except Exception as e:
        # refund credit on failure
        import sqlite3
        c = db._conn()
        c.execute("UPDATE users SET credits = credits + 1 WHERE id=?", (user["id"],))
        c.commit()
        c.close()
        raise HTTPException(status_code=500, detail=str(e))
    db.add_project(user["id"], slug, payload.name or "مشروع", goal)
    _fire_dispatch(slug, user["plan"], _user_provider_keys(user))
    audit.audit("project.create", uid=user["id"], email=user["email"], ip=_client_ip(request),
                outcome="ok", slug=slug, plan=user["plan"])
    return {
        "slug": slug, "goal": payload.goal, "root_id": swarm.root_id,
        "workers": swarm.worker_ids, "verifier_id": swarm.verifier_id,
        "synthesizer_id": swarm.synthesizer_id,
    }


@app.get("/api/projects/{slug}/tasks")
def api_tasks(slug: str, user: dict = Depends(get_current_user)):
    # Only allow if the board belongs to this user (prefix guard).
    if not slug.startswith(f"u{user['id']}-") and not slug.startswith("flux-demo-"):
        raise HTTPException(status_code=403, detail="غير مصرّح")
    try:
        return hc.list_tasks(slug)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/{slug}/dispatch")
def api_dispatch(slug: str, dry_run: bool = False, user: dict = Depends(get_current_user)):
    if not slug.startswith(f"u{user['id']}-") and not slug.startswith("flux-demo-"):
        raise HTTPException(status_code=403, detail="غير مصرّح")
    try:
        return hc.dispatch(slug, max_spawn=db.PLANS[user["plan"]]["parallel"], dry_run=dry_run)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- BYOK keys ----------
@app.get("/api/keys")
def api_list_keys(user: dict = Depends(get_current_user)):
    # Return masked info only; never the raw token.
    data = {}
    for prov in ("anthropic", "openai", "gemini", "kimi", "opencode-free"):
        tok = vault.get_user_key(user["id"], prov)
        if tok:
            data[prov] = vault.mask_key(tok) if tok != "free" else "opencode-free (مجاني)"
    return {"keys": data, "byok_active": bool(data)}


@app.post("/api/keys")
def api_set_key(payload: dict, request: Request, user: dict = Depends(get_current_user)):
    prov = (payload.get("provider") or "").lower()
    tok = (payload.get("token") or "").strip()
    # Supported: real BYOK providers (need a key) + the free hosted provider
    # (opencode-free) which the user can enable explicitly without any key.
    if prov not in ("anthropic", "openai", "gemini", "kimi", "opencode-free"):
        raise HTTPException(status_code=400, detail="مزوّد غير مدعوم")
    if prov == "opencode-free":
        if tok and tok.lower() not in ("free", "on", "true", ""):
            raise HTTPException(status_code=400, detail="المزوّد المجاني لا يحتاج مفتاحاً")
        # Store a sentinel so _user_provider_keys picks the free model.
        vault.set_user_key(user["id"], "opencode-free", "free")
        audit.audit("keys.set", uid=user["id"], email=user["email"], ip=_client_ip(request),
                    outcome="ok", provider="opencode-free")
        return {"ok": True, "provider": prov, "masked": "opencode-free (مجاني)"}
    if not tok:
        raise HTTPException(status_code=400, detail="المفتاح فارغ")
    vault.set_user_key(user["id"], prov, tok)
    audit.audit("keys.set", uid=user["id"], email=user["email"], ip=_client_ip(request),
                outcome="ok", provider=prov)
    return {"ok": True, "provider": prov, "masked": vault.mask_key(tok)}


def _user_provider_keys(user: dict) -> dict:
    """Collect the user's BYOK keys so THEY pay for tokens and the squad gets a
    higher-quality model than the free default (Claude/GPT/Gemini/Kimi).

    Available on ALL plans (including demo): bringing a paid key is how a user
    tests stronger code quality at their own token cost — FluxSwarm pays nothing.
    If the user enabled 'opencode-free', we return that sentinel so the squad
    pins to the free hosted model instead of needing a paid key.
    """
    keys = {}
    for prov in ("anthropic", "openai", "gemini", "kimi"):
        tok = vault.get_user_key(user["id"], prov)
        if tok:
            keys[prov] = tok
    if vault.get_user_key(user["id"], "opencode-free") == "free":
        keys["opencode-free"] = "free"
    return keys


# ---------- referrals ----------
@app.get("/api/referrals")
def api_referrals(user: dict = Depends(get_current_user)):
    return {"ref_code": user["ref_code"], "reward_credits": db.REFERRAL_REWARD_CREDITS,
            "link": f"/?ref={user['ref_code']}"}


@app.post("/api/subscribe/{plan}")
def api_subscribe(plan: str, request: Request, user: dict = Depends(get_current_user)):
    if plan not in db.PLANS:
        raise HTTPException(status_code=400, detail="باقة غير صالحة")
    is_paid = db.PLANS[plan]["price"] > 0
    if not is_paid:
        db.upgrade_plan(user["id"], plan)
        audit.audit("plan.subscribe", uid=user["id"], email=user["email"], ip=_client_ip(request),
                    outcome="ok", plan=plan)
        return public_user(db.get_user_by_id(user["id"]))
    # Paid plan: must pass through a real billing gate. Until a gateway is wired
    # (FLUXSWARM_PAYMENTS=1 + operative provider creds), claiming a paid plan is
    # rejected outright: this closes the free-upgrade / infinite-credit-reset exploit.
    if not _payments_enabled():
        gw = payments_mod.get_gateway()
        audit.audit("plan.subscribe", uid=user["id"], email=user["email"], ip=_client_ip(request),
                    outcome="fail", reason="billing_gate_closed", plan=plan,
                    gateway=getattr(gw, "name", "none"))
        raise HTTPException(status_code=402,
                            detail="الاشتراك المدفوع مقفل في وضع التطوير — يتم تفعيل بوابة الدفع قريباً")
    gw = payments_mod.get_gateway()
    if not getattr(gw, "operative", False):
        # Provider requested but neither credentials nor the explicit local
        # sandbox flag are present -> stay closed (never charge by accident).
        audit.audit("plan.subscribe", uid=user["id"], email=user["email"], ip=_client_ip(request),
                    outcome="fail", reason="gateway_not_configured", plan=plan,
                    gateway=getattr(gw, "name", "none"))
        raise HTTPException(status_code=402,
                            detail="بوابة الدفع لم تُضبط بعد — المرجو المحاولة لاحقاً")
    try:
        session = gw.create_checkout(
            plan=plan,
            user_id=user["id"],
            amount_cents=db.PLANS[plan]["price"] * 100,
        )
    except Exception as exc:  # provider down / misconfigured
        audit.audit("plan.subscribe", uid=user["id"], email=user["email"], ip=_client_ip(request),
                    outcome="fail", reason="checkout_error", plan=plan, error=str(exc)[:200])
        raise HTTPException(status_code=502, detail="تعذر فتح جلسة الدفع — حاول مرة أخرى")
    audit.audit("plan.subscribe", uid=user["id"], email=user["email"], ip=_client_ip(request),
                outcome="ok", plan=plan, mode="checkout", session_id=session.id, gateway=gw.name)
    return {"checkout_url": session.url, "session_id": session.id,
            "plan": plan, "note": "الترقية تُفعَّل تلقائياً عند تأكيد الدفع"}


@app.post("/api/payments/webhook")
async def api_payments_webhook(request: Request):
    """Paddle webhook endpoint. Unauthenticated by design but signature-verified:
    credits are granted ONLY when a webhook with a valid FLUXSWARM-provided
    signature arrives; a client redirecting back is never proof of payment.

    Idempotent: a replayed event_id returns 200 without double-granting.
    """
    gw = payments_mod.get_gateway()
    if not getattr(gw, "operative", False):
        audit.audit("payments.webhook", outcome="fail", reason="gateway_not_configured",
                    ip=_client_ip(request))
        raise HTTPException(status_code=503, detail="webhook غير مهيأ")
    body = await request.body()
    signature = request.headers.get("Paddle-Signature")
    if not getattr(gw, "operative", False):
        audit.audit("payments.webhook", outcome="fail", reason="gateway_not_configured",
                    ip=_client_ip(request))
        raise HTTPException(status_code=503, detail="webhook غير مهيأ")
    try:
        processed = _process_paddle_payload(body, signature, request)
    except HTTPException:
        _debug_webhook_failure(body, signature, request)
        raise
    return {"accepted": True, "deduplicated": processed.get("deduplicated", False)}


@app.get("/api/payments/webhook")
def api_payments_webhook_get():
    raise HTTPException(status_code=405, detail="method not allowed")


@app.get("/api/payments/debug-verify")
def api_payments_debug_verify(request: Request, h: str = "", sig: str = ""):
    """TEMPORARY localhost-only: report what the LIVE process computes for a
    given webhook body (hex) + Paddle-Signature, to root-cause mismatches."""
    if _client_ip(request) not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="local only")
    import hashlib
    import hmac
    gw = payments_mod.get_gateway()
    body = bytes.fromhex(h)
    ok = gw._verify_signature(body, sig or None)
    secret = getattr(gw, "_secret", "")
    ts_part, _, h1_part = (sig or "").partition(";h1=")
    parsed_ts = ts_part[3:] if ts_part.startswith("ts=") else ""
    try:
        float_ts = float(parsed_ts) if parsed_ts else None
        int_ts = int(float_ts) if float_ts is not None else None
    except ValueError:
        int_ts = None
    expected = None
    if int_ts is not None:
        expected = hmac.new(
            secret.encode(),
            f"paddle-{int_ts};{body.decode('utf-8', 'replace')}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    return {
        "verify_result": ok,
        "sig_received": (sig or "")[:60],
        "h1_received": h1_part[:24],
        "ts_parsed": parsed_ts,
        "ts_int": int_ts,
        "expected_h1": (expected or "")[:24],
        "now_server": time.time(),
        "secret_len": len(secret),
        "secret_sha": hashlib.sha256(secret.encode()).hexdigest()[:16],
        "body_len": len(body),
        "body_sha": hashlib.sha256(body).hexdigest()[:24],
    }


def _debug_webhook_failure(body: bytes, signature: str | None, request: Request) -> None:
    """TEMPORARY diagnostic: record facts about a rejected webhook so the signed
    bytes can be compared locally. Writes to the local temp dir only."""
    import hashlib

    try:
        gw = payments_mod.get_gateway()
        secret = getattr(gw, "_secret", "")
        direct = gw._verify_signature(body, signature or None)
        redo = payments_mod.verify_paddle_signature(body, signature or "", secret=secret, now=time.time())
        ts_part, _, h1_part = (signature or "").partition(";h1=")
        try:
            float_ts = float(ts_part[3:]) if ts_part.startswith("ts=") else None
            int_ts = int(float_ts) if float_ts is not None else None
        except ValueError:
            int_ts = None
        expected = None
        if int_ts is not None:
            expected = hmac.new(secret.encode(),
                                f"paddle-{int_ts};{body.decode('utf-8', 'replace')}".encode("utf-8"),
                                hashlib.sha256).hexdigest()
        path = os.environ.get("FLUXSWARM_TMP") or r"C:\Users\DELL\AppData\Local\Temp\opencode\webhook_debug.log"
        hashlib_bytes = hashlib.sha256(body).hexdigest()
        body_file = rf"C:\Users\DELL\AppData\Local\Temp\opencode\wh_body_{hashlib_bytes[:16]}.bin"
        with open(body_file, "wb") as fh:
            fh.write(body)
        line = (
            "FAIL ts={ts} ctype={ct} clen={clen} sha={sha} sig={sig} "
            "direct={direct} redo={redo} exp={exp} secret_sha={ss} dump={dump}\n"
        ).format(
            ts=time.time(),
            ct=request.headers.get("Content-Type"),
            clen=request.headers.get("Content-Length") or len(body),
            sha=hashlib_bytes[:24],
            sig=(signature or "").replace("\n", " "),
            direct=direct,
            redo=redo,
            exp=(expected or "")[:24],
            ss=hashlib.sha256(secret.encode()).hexdigest()[:16],
            dump=body_file,
        )
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _process_paddle_payload(body: bytes, signature: str | None, request: Request) -> dict:
    """Signature-verify a Paddle payload, apply it once (idempotent), audit it.

    Shared by the real webhook and the explicit local sandbox mode so the mock
    path exercises exactly the same grant logic as production.
    """
    gw = payments_mod.get_gateway()
    receipt = gw.handle_webhook(body, signature=signature)
    if not receipt.get("ok"):
        audit.audit("payments.webhook", outcome="fail", reason=receipt.get("reason", "rejected"),
                    ip=_client_ip(request))
        raise HTTPException(status_code=400, detail="webhook مرفوض")
    event_id = receipt["event_id"] or receipt["idempotency_key"]
    if not db.record_payment_event(
        event_id, gateway=receipt["gateway"], kind=receipt["event"],
        user_id=receipt.get("user_id") or 0,
        detail={"plan": receipt.get("plan"), "amount_cents": receipt.get("amount_cents"),
                "currency": receipt.get("currency"), "txn": receipt.get("transaction_id")},
    ):
        return {"deduplicated": True}  # already processed
    detail_inner = {"gateway": receipt["gateway"], "receipt_kind": receipt["event"],
                    "plan": receipt.get("plan"),
                    "amount_cents": receipt.get("amount_cents"), "currency": receipt.get("currency"),
                    "txn": receipt.get("transaction_id"), "event_id": event_id}
    if receipt["event"] == "payment.succeeded":
        uid = receipt.get("user_id")
        if not uid or not receipt.get("plan"):
            audit.audit("payments.webhook", outcome="fail", reason="incomplete_receipt",
                        ip=_client_ip(request), **detail_inner)
            raise HTTPException(status_code=422, detail="بيانات الدفع ناقصة")
        db.upgrade_plan(uid, receipt["plan"])
        u2 = db.get_user_by_id(uid)
        if u2 and u2.get("referred_by") and receipt.get("plan"):
            db.reward_referrer_once(u2["email"])
        audit.audit("payments.webhook", outcome="ok", ip=_client_ip(request), **detail_inner)
    elif receipt["event"] == "payment.refunded":
        uid = receipt.get("user_id")
        if not uid and receipt.get("transaction_id"):
            # Paddle v1 refund payloads (adjustment.*) carry only the original
            # transaction id — map it back to the paying user before revoking.
            uid = db.payment_user_by_txn(receipt["transaction_id"])
        if uid:
            db.downgrade_subscription(uid)
        audit.audit("payments.webhook", outcome="ok", ip=_client_ip(request), **detail_inner)
    else:
        audit.audit("payments.webhook", outcome="ok", ip=_client_ip(request),
                    handled="unhandled-kind", **detail_inner)
    return {"deduplicated": False}


# ---------- hosted checkout page (Paddle.js overlay) ----------
def _paddle_client_token() -> str:
    return os.environ.get("PADDLE_CLIENT_TOKEN", "").strip()


@app.get("/checkout", response_class=HTMLResponse)
def checkout_page(request: Request):
    """Paddle.js checkout page. Paddle transaction payment links point at
    `/<this>/?_ptxn=txn_...`; this page includes Paddle.js, initializes with the
    client token, and opens Paddle's overlay checkout for the transaction named
    in the `_ptxn` query parameter. Works for sandbox and live depending on
    PADDLE_API_BASE. A fallback button + event handlers cover blocked popups."""
    token = _paddle_client_token()
    sandbox = "sandbox" in os.environ.get("PADDLE_API_BASE", "")
    if not token:
        return """<!doctype html><html lang="ar"><head><meta charset="utf-8"><title>FluxSwarm · الدفع</title></head>
<body style="font-family:system-ui;max-width:560px;margin:60px auto;text-align:center">
<h2>الدفع غير جاهز بعد</h2>
<p>PADDLE_CLIENT_TOKEN غير مضبوط — أضف رمز العميل من لوحة Paddle ثم أعد التشغيل.</p>
</body></html>"""
    env = ""
    if sandbox:
        env = "try { Paddle.Environment.set(\"sandbox\"); } catch (e) {}\n"
    page = """<!doctype html><html lang="ar"><head><meta charset="utf-8">
<title>FluxSwarm · الدفع الآمن</title>
<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
</head>
<body style="font-family:system-ui;max-width:560px;margin:60px auto;text-align:center;line-height:1.8">
<h2 id="fs-status">جارٍ فتح نافذة الدفع الآمنة…</h2>
<p style="color:#666;font-size:14px">إذا لم تظهر النافذة خلال لحظات، تأكد من السماح بالنوافذ المنبثقة ثم اضغط الزر، أو أعد فتح الرابط.</p>
<button id="fs-retry" onclick="openCheckout()" style="margin:14px 0;font-size:15px;padding:10px 24px;cursor:pointer;border-radius:8px;border:1px solid #1b6ef3;background:#1b6ef3;color:#fff">اضغط هنا لفتح نافذة الدفع</button>
<script>
__PADDLE_ENV__
var fsStatus = document.getElementById('fs-status');
var fsLog = function (m) { console.log('[fluxswarm]', m); if (fsStatus) fsStatus.textContent = m; };
window.onerror = function (msg, src, line) { fsLog("خطأ برمجي: " + msg + " (" + line + ")"); };
window.addEventListener('unhandledrejection', function (e) {
  fsLog("فشل غير معالج: " + (e.reason ? (e.reason.message || e.reason) : "unknown"));
});

var txn = new URLSearchParams(window.location.search).get('_ptxn');
var watchdog = null;
var initialized = false;

function openCheckout() {
  if (typeof Paddle === 'undefined') {
    fsLog("لم يُحمَّل محرك الدفع (cdn.paddle.com) من هذا المتصفح. جرّب: تحديث الصفحة، أو متصفح آخر، أو تعطيل مانع الإعلانات.");
    return;
  }
  if (!txn) {
    fsLog("الرابط غير مكتمل — أعد فتحه من صفحة الاشتراك.");
    return;
  }
  fsLog("جارٍ فتح نافذة الدفع الآمنة…");
  try {
    Paddle.Checkout.open({ transactionId: txn, settings: { displayMode: "overlay" } });
    watchdog = setTimeout(function () {
      fsLog("لم تتأكد النافذة خلال 8 ثوانٍ — اضغط الزر أعلاه لتجربة أخرى.");
    }, 8000);
  } catch (err) {
    fsLog("تعذّر فتح الدفع: " + err.message);
  }
}

var sdkAttempts = 0;
function retrySdk() {
  if (sdkAttempts >= 3) {
    fsLog("الإخفاق متكرر — محرك الدفع لا يصل من هذا المتصفح. جرّب متصفحاً آخر أو عطّل مانع الإعلانات ثم أعد تحميل الصفحة.");
    return;
  }
  sdkAttempts++;
  var s = document.createElement('script');
  s.src = 'https://cdn.paddle.com/paddle/v2/paddle.js';
  s.onload = function () { bootstrap(); };
  s.onerror = function () { setTimeout(retrySdk, 1200); };
  document.head.appendChild(s);
}

function bootstrap() {
  if (typeof Paddle === 'undefined') {
    retrySdk();
    return;
  }
  if (initialized) return;
  initialized = true;
  try {
    Paddle.Initialize({ token: "__PADDLE_TOKEN__" });
  } catch (err) {
    fsLog("فشل تهيئة Paddle: " + err.message);
    return;
  }
  Paddle.Checkout.on('checkout.loaded', function () { watchdog && clearTimeout(watchdog); fsLog("نافذة الدفع مفتوحة."); });
  Paddle.Checkout.on('checkout.closed', function () { fsLog("أُغلقت النافذة — اضغط الزر للمتابعة."); });
  Paddle.Checkout.on('error', function (data) { watchdog && clearTimeout(watchdog); fsLog("خطأ Paddle: " + (data && data.error ? data.error : "غير معروف") + " — اضغط الزر للمحاولة."); });
  Paddle.Checkout.on('transaction.completed', function () { fsLog("اكتمل الدفع — جارٍ التأكيد…"); });
  window.addEventListener('load', openCheckout);
  setTimeout(openCheckout, 1200);
}

document.addEventListener('DOMContentLoaded', bootstrap);
</script>
</body></html>"""
    return page.replace("__PADDLE_ENV__", env).replace("__PADDLE_TOKEN__", token)


# ---------- local sandbox (FLUXSWARM_PADDLE_MOCK=1 only) ----------
def _mock_active() -> bool:
    """Sandbox endpoints are only reachable while FLUXSWARM_PADDLE_MOCK=1 AND no
    live Paddle credentials are in play (live base URL + API key). This keeps a
    forgotten mock flag from ever minting free credits in production."""
    if os.environ.get("FLUXSWARM_PADDLE_MOCK", "").strip().lower() not in ("1", "true", "yes"):
        return False
    base = os.environ.get("PADDLE_API_BASE", "").strip()
    key = os.environ.get("PADDLE_API_KEY", "").strip()
    if key and base in ("", "https://api.paddle.com"):
        return False
    return True


@app.get("/mock-checkout/{user_id}/{plan}", response_class=HTMLResponse)
def mock_checkout_page(user_id: int, plan: str):
    """Local sandbox checkout page: shows the order and auto-completes the
    payment through the same signed path a real Paddle webhook uses."""
    if not _mock_active():
        raise HTTPException(status_code=404, detail="not found")
    if plan not in db.PLANS:
        raise HTTPException(status_code=404, detail="باقة غير صالحة")
    price = db.PLANS[plan]["price"]
    return f"""<!doctype html><html lang="ar"><head><meta charset="utf-8"><title>FluxSwarm · دفعة تجريبية</title></head>
<body style="font-family:system-ui;max-width:560px;margin:60px auto;text-align:center;line-height:1.8">
<h2>دفعة تجريبية (وضع محلي — بلا مال حقيقي)</h2>
<p>الباقة: <b>{db.PLANS[plan]['name']}</b> · المبلغ: <b>${price}</b> (تجريبي)</p>
<form method="get" action="/api/payments/dev-complete/{user_id}/{plan}">
<button style="font-size:16px;padding:10px 22px;cursor:pointer">إتمام الدفع تجريبياً</button>
</form>
<p style="color:#888;font-size:13px">تُدار هذه الصفحة بنفس مسار ربط Paddle: تُولَّد حمولة موقّعة بتوقيع صحيح وتُعالج بنقطة webhook نفسها.</p>
</body></html>"""


@app.get("/api/payments/dev-complete/{user_id}/{plan}")
def dev_complete_mock_payment(request: Request, user_id: int, plan: str, aud: str = "default"):
    """Local sandbox ONLY: mint a correctly-signed Paddle transaction.completed
    payload for the given user/plan and run it through the production webhook
    handler (signature verification included). Grants credits exactly as a real
    Paddle callback would. Never exposed with FLUXSWARM_PADDLE_MOCK off."""
    if not _mock_active():
        raise HTTPException(status_code=404, detail="not found")
    if plan not in db.PLANS:
        raise HTTPException(status_code=404, detail="باقة غير صالحة")
    import hashlib
    import hmac
    import base64

    secret = os.environ.get("PADDLE_WEBHOOK_SECRET", "mock-secret")
    event_id = f"evt_mock_{user_id}_{plan}_{aud}_{int(time.time())}"
    payload = {
        "event_id": event_id,
        "event_type": "transaction.completed",
        "data": {
            "id": f"txn_mock_{event_id}",
            "status": "completed",
            "custom_data": {"user_id": str(user_id), "plan": plan},
            "total": {"amount": db.PLANS[plan]["price"] * 100, "currency": "USD"},
        },
        "metadata": {},
    }
    body = json.dumps(payload).encode("utf-8")
    sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    _process_paddle_payload(body, sig, request)
    u = db.get_user_by_id(user_id)
    return {"paid": True, "plan": plan, "now": (u or {}).get("plan"),
            "credits": (u or {}).get("credits"), "event_id": event_id}


# ---------- compliance: public legal pages ----------
_LEGAL_BASE = """<!doctype html><html lang="ar"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;
line-height:1.7;color:#222}}h1{{font-size:1.6rem}}a{{color:#0b59c5}}</style></head><body>{body}</body></html>"""

_LEGAL_BASE_EN = '<!doctype html><html lang="en"><head><meta charset="utf-8">\n' \
    '<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>\n' \
    '<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;\n' \
    'line-height:1.7;color:#222}}h1{{font-size:1.6rem}}a{{color:#0b59c5}}</style></head><body>{body}</body></html>'


def _legal_entity_block(lang: str) -> str:
    """Render the operating-entity disclosure from env config (empty fragment when unset).

    The legal-entity fields are env-driven (FLUXSWARM_LEGAL_*), so the company
    info stays out of the repo: add them to `.env` at deploy time.
    """
    ent = os.environ.get("FLUXSWARM_LEGAL_ENTITY", "").strip()
    if not ent:
        return ""
    if lang == "en":
        labels = ("Commercial registry no.", "Tax ID", "Registered office", "Phone")
    else:
        labels = ("السجل التجاري", "الرقم الضريبي", "المقر المسجّل", "الهاتف")
    vals = [os.environ.get(k, "").strip() for k in
            ("FLUXSWARM_LEGAL_REGISTRY_NO", "FLUXSWARM_LEGAL_TAX_ID",
             "FLUXSWARM_LEGAL_ADDRESS", "FLUXSWARM_LEGAL_PHONE")]
    bits = [f"{lab}: <b>{v}</b>" for lab, v in zip(labels, vals) if v]
    suffix = (" — " + " · ".join(bits)) if bits else ""
    heading = "Operating entity" if lang == "en" else "الكيان التشغيلي"
    return f"<h2>{heading}</h2><p><b>{ent}</b>{suffix}</p>"


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>سياسة الخصوصية (Privacy Policy)</h1>
<p>تُجمع البيانات التالية لتشغيل الخدمة فقط: البريد الإلكتروني والاسم وكلمات المرور (مشفّرة Argon2id) ومفاتيح مزوّدي الذكاء الاصطناعي (مشفّرة فورياً بـ Fernet) وسجلُّ الاستخدام والتدقيق.</p>
<p>لا تُباع البيانات ولا تُشارك مع أطراف ثالثة، ما عدا معالج الدفع Paddle (تاجر السجلّ) لإتمام المعاملات. تُخزَّن البيانات في أمريكا الشمالية.</p>
<p>حقوقك (CCPA/CPRA): حق الاطلاع على بياناتك عبر <code>GET /api/account/export</code>، وحق الحذف الكامل عبر <code>DELETE /api/account</code>.</p>
<p>يُستبعد سجلّ التدقيق الأمني (Append-only) من الحذف: يُحتفظ به للأغراض الأمنية والتحقيقية ولا يُستخدم لأي غرض تسويقي. قد تتضمن مدخلاته البريد الإلكتروني وعنوان IP تلقائياً لأغراض التحقيق في إساءة الاستخدام، وهي غير قابلة للمحو.</p>"""
    body += _legal_entity_block("ar")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>أسئلة: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE.format(title="سياسة الخصوصية", body=body)


@app.get("/privacy-en", response_class=HTMLResponse)
def privacy_page_en():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>Privacy Policy</h1>
<p>We process only the data needed to operate the service: email address, name, password (hashed with Argon2id), user-supplied AI provider keys (encrypted at rest with Fernet), and usage/audit records.</p>
<p>We do not sell your data and do not share it with third parties except Paddle (the merchant of record) to complete transactions. Data is stored in North America.</p>
<p>Your rights (CCPA/CPRA): access your data via <code>GET /api/account/export</code>, and request full erasure via <code>DELETE /api/account</code>.</p>
<p>The security audit log is append-only and is excluded from erasure: it is retained for security and investigation purposes only, is never used for marketing, and its entries may include your email address and IP address automatically.</p>"""
    body += _legal_entity_block("en")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>Questions: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE_EN.format(title="Privacy Policy", body=body)


@app.get("/terms", response_class=HTMLResponse)
def terms_page():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>شروط الاستخدام (Terms of Service)</h1>
<p>تُقدَّم الخدمة «كما هي». الاشتراكات المدفوعة تدار بواسطة Paddle (Merchant of Record) وفق شروطها.</p>
<p>تُمنح الائتمانات عند تأكيد الدفع فقط. تُرفض أنشطة إساءة الاستخدام أو المحتوى غير القانوني أو إشباع السرب بشكل ضار، وقد يوقف الحساب.</p>
<p>تُطبَّق هذه الشروط بموجب قوانين الولايات المتحدة.</p>"""
    body += _legal_entity_block("ar")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>اتصل بنا: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE.format(title="شروط الاستخدام", body=body)


@app.get("/terms-en", response_class=HTMLResponse)
def terms_page_en():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>Terms of Service</h1>
<p>The service is provided &quot;as is&quot;. Paid subscriptions are processed by Paddle (Merchant of Record) under its own terms.</p>
<p>Credits are granted only after a confirmed payment. Abuse, unlawful content, or harmful swarm activity is prohibited and may result in account suspension.</p>
<p>These terms are governed by the laws of the United States.</p>"""
    body += _legal_entity_block("en")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>Contact: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE_EN.format(title="Terms of Service", body=body)


# ---------- compliance: CCPA/CPRA account rights ----------
@app.get("/api/account/export")
def api_account_export(request: Request, user: dict = Depends(get_current_user)):
    payload = db.account_payload(user["id"])
    audit.audit("account.export", uid=user["id"], email=user["email"], ip=_client_ip(request), outcome="ok")
    return payload


@app.delete("/api/account")
def api_account_delete(request: Request, user: dict = Depends(get_current_user)):
    uid = user["id"]
    vault.delete_user_key(uid)
    removed = db.delete_user(uid)
    audit.audit("account.delete", uid=uid, email=user["email"], ip=_client_ip(request),
                outcome="ok" if removed else "missing")
    return {"ok": removed, "note": "تم حذف الحساب وكل البيانات المرتبطة به"}


# ---------- Telegram account linking ----------
@app.get("/api/telegram/link")
def api_telegram_link(request: Request, user: dict = Depends(get_current_user)):
    """Issue (or reuse) the one-time pairing code the user types to the bot.

    The code binds the bot chat to this account; at most one active code per
    user (TTL 10 min), so repeated calls simply re-serve the same code.
    """
    code = db.new_telegram_link_code(user["id"])
    audit.audit("telegram.code", uid=user["id"], email=user["email"],
                ip=_client_ip(request), outcome="ok")
    return {"code": code, "ttl_seconds": db.TELEGRAM_LINK_TTL,
            "bot": os.environ.get("TELEGRAM_BOT_USERNAME", "fluxswarm_bot")}


@app.get("/api/telegram/status")
def api_telegram_status(request: Request, user: dict = Depends(get_current_user)):
    link = db.get_telegram_link(user["id"])
    return {"linked": bool(link), "telegram_chat_id": link["telegram_chat_id"] if link else None,
            "linked_at": link["linked_at"] if link else None}


@app.delete("/api/telegram/link")
def api_telegram_unlink(request: Request, user: dict = Depends(get_current_user)):
    removed = db.unlink_telegram(user["id"])
    audit.audit("telegram.unlink", uid=user["id"], email=user["email"],
                ip=_client_ip(request), outcome="ok" if removed else "missing")
    return {"ok": removed}


# ---------- squad marketplace ----------
class TemplateIn(BaseModel):
    name: str
    description: str = ""
    agents: list[str]
    price_credits: int = 10


class BuyIn(BaseModel):
    goal: str = ""


# Content curbs: block empty / oversized / scriptable templates at the gate.
_TPL_NAME_MAX = 80
_TPL_DESC_MAX = 500
_TPL_AGENTS_MAX = 6
_TEMPLATE_LIMITS = {"price_min": 1, "price_max": 500, "goal_max_len": 2000}


def _clean_text(s: str, *, name: str, max_len: int) -> str:
    """Strip control chars; reject anything that could render as HTML.

    The previous implementation built the allowed-whitespace set with
    ``"\\n\\r\\t".strip()`` — which returns an EMPTY string (strip removes all
    those chars), so EVERY character was stripped, collapsing any input to "".
    That made every template name/description fail the subsequent empty check,
    blocking ALL template publishing. We now keep the real whitespace chars and
    a sensible printable set, and additionally cap length as a hard input limit.
    """
    if s is None:
        raise HTTPException(status_code=400, detail=f"{name} فارغ")
    # Keep real whitespace (space, tab, newline, carriage return) plus printable.
    allowed = set(" \t\n\r")
    s = "".join(ch for ch in s if ch in allowed or ch.isprintable())
    s = s.strip()
    if not s:
        raise HTTPException(status_code=400, detail=f"{name} فارغ")
    # Reject oversized input up-front (don't silently truncate a user's
    # submission — the caller's contract is validation, and a truncated goal/name
    # would be surprising and could break downstream length assumptions).
    if len(s) > max_len:
        raise HTTPException(status_code=400, detail=f"{name} أطول من الحد ({max_len})")
    if any(ch in s for ch in "<>") or "script" in s.lower() \
            or "javascript:" in s.lower() or "onerror=" in s.lower():
        raise HTTPException(status_code=400, detail=f"{name} يحتوي وسوم/سكربتات غير مسموحة")
    return s


def _validate_template(payload: TemplateIn):
    payload.name = _clean_text(payload.name, name="اسم القالب", max_len=_TPL_NAME_MAX)
    desc = (payload.description or "").strip()
    if desc:
        payload.description = _clean_text(desc, name="الوصف", max_len=_TPL_DESC_MAX)
    else:
        payload.description = ""
    if not payload.agents:
        raise HTTPException(status_code=400, detail="أضف وكلاء للقالب")
    if len(payload.agents) > _TPL_AGENTS_MAX:
        raise HTTPException(status_code=400, detail=f"الحد الأقصى {_TPL_AGENTS_MAX} وكلاء لكل قالب")
    seen = set()
    resolved = []
    for name in payload.agents:
        name = name.strip()
        if not name:
            continue
        if name in seen:
            raise HTTPException(status_code=400, detail=f"وكيل مكرر: {name}")
        seen.add(name)
        if name not in hc.AGENT_REGISTRY:
            raise HTTPException(status_code=400,
                                detail=f"وكيل غير معروف: {name} — المرخّصون: {', '.join(hc.AGENT_REGISTRY)}")
        resolved.append(name)
    if not resolved:
        raise HTTPException(status_code=400, detail="لا يوجد وكلاء صالحين في القالب")
    payload.agents = resolved
    if payload.price_credits < _TEMPLATE_LIMITS["price_min"] or \
       payload.price_credits > _TEMPLATE_LIMITS["price_max"]:
        raise HTTPException(status_code=400,
                            detail=f"السعر بين {_TEMPLATE_LIMITS['price_min']} و {_TEMPLATE_LIMITS['price_max']} رصيد")


@app.post("/api/templates")
def api_publish_template(payload: TemplateIn, request: Request,
                         user: dict = Depends(get_current_user)):
    # price_credits is pydantic-typed as int (non-numeric input -> auto 422).
    _validate_template(payload)
    tid = db.publish_template(user["id"], payload.name, payload.description, payload.agents, payload.price_credits)
    audit.audit("template.publish", uid=user["id"], email=user["email"], ip=_client_ip(request),
                outcome="ok", template_id=tid, price=payload.price_credits,
                agents=payload.agents)
    return {"id": tid, "ok": True}


@app.get("/api/templates")
def api_list_templates():
    return db.list_templates()


@app.get("/api/templates/mine")
def api_my_templates(user: dict = Depends(get_current_user)):
    return db.list_templates(author_id=user["id"])


@app.post("/api/templates/{tid}/buy")
def api_buy_template(tid: int, payload: BuyIn, request: Request,
                     user: dict = Depends(get_current_user)):
    tpl = db.get_template(tid)
    if not tpl:
        raise HTTPException(status_code=404, detail="القالب غير موجود")
    if tpl.get("author_id") == user["id"]:
        audit.audit("template.buy", uid=user["id"], email=user["email"], ip=_client_ip(request),
                    outcome="fail", reason="own_template", template_id=tid)
        raise HTTPException(status_code=400, detail="لا يمكنك شراء قالبك الخاص")
    # Burst guard: max template purchases per user per hour (prevents runaway
    # parallel squad spawns while keeping the store usable).
    if not limiter.purchase_allowed(user["id"]):
        audit.audit("template.buy", uid=user["id"], email=user["email"], ip=_client_ip(request),
                    outcome="fail", reason="burst_limit", template_id=tid)
        raise HTTPException(status_code=429, detail="تجاوزت حد الشراء (5 قوالب/ساعة) — انتظر قليلاً")
    if not db.buy_template(tid, user["id"]):
        audit.audit("template.buy", uid=user["id"], email=user["email"], ip=_client_ip(request),
                    outcome="fail", reason="insufficient_credits", template_id=tid)
        raise HTTPException(status_code=402, detail="رصيد غير كافٍ لشراء هذا القالب")
    limiter.record_purchase(user["id"])
    audit.audit("template.buy", uid=user["id"], email=user["email"], ip=_client_ip(request),
                outcome="ok", template_id=tid, author_id=tpl.get("author_id"),
                price=tpl.get("price_credits"))
    # Launched squad is auto-dispatched now (no manual dispatch wait).
    goal = (payload.goal or "").strip() or tpl.get("description") or f"Build using squad template: {tpl.get('name')}"
    goal = goal.strip()
    if len(goal) > _TEMPLATE_LIMITS["goal_max_len"]:
        goal = goal[:_TEMPLATE_LIMITS["goal_max_len"]]
    slug = f"u{user['id']}-t{tid}-{int(time.time())}-{secrets.token_hex(4)}"
    launched = False
    launch_error = None
    try:
        db.add_project(user["id"], slug, tpl.get("name", "marketplace-squad"), goal)
        hc.ensure_board(slug)
        hc.launch_from_template(slug, goal, tpl.get("agents", []),
                                provider_keys=_user_provider_keys(user))
        hc.dispatch(slug, max_spawn=db.PLANS[user["plan"]]["parallel"],
                    provider_keys=_user_provider_keys(user), blocking=False)
        launched = True
    except Exception as e:
        launched = False
        launch_error = str(e)
        # The purchase already debited the buyer (and paid the author). If the
        # squad failed to launch, refund so the user isn't charged for nothing.
        try:
            db.refund_template_purchase(tid, user["id"])
        except Exception:
            pass
    resp = {"ok": True, "user": public_user(db.get_user_by_id(user["id"])),
            "slug": slug, "launched": launched}
    if not launched:
        resp["launch_error"] = launch_error
    return resp


# ---------- security (ECC AgentShield) ----------
@app.get("/api/projects/{slug}/security")
def api_security(slug: str, include_llm: bool = False, user: dict | None = Depends(get_current_user_optional)):
    # Demo boards are public for showcasing; owned boards require the owner.
    if slug.startswith("flux-demo-"):
        pass
    elif not (user and (slug.startswith(f"u{user['id']}-"))):
        raise HTTPException(status_code=403, detail="غير مصرّح")
    goal = ""
    try:
        goal = hc.list_tasks(slug)[0].get("title", "") if hc.list_tasks(slug) else ""
    except Exception:
        pass
    try:
        res = security.scan_project(slug, goal, include_llm=include_llm)
        res["label"] = security.severity_label(res.get("score", 0))
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.websocket("/ws/{slug}")
async def ws_board(websocket: WebSocket, slug: str):
    # Token auth via query param. Strict, fail-closed rules:
    #   1. No token  -> reject (no silent anonymous board access).
    #   2. Malformed/unsigned token -> reject (do NOT treat as anonymous).
    #   3. Token uid maps to a deleted user -> reject (user-existence check).
    #   4. slug not owned by the user (and not a demo board) -> reject.
    token = websocket.query_params.get("token")
    if not token:
        await websocket.accept()
        await websocket.send_json({"type": "error", "detail": "unauthorized"})
        await websocket.close()
        return
    payload = auth_mod.decode_token(token)
    if not payload or "uid" not in payload:
        await websocket.accept()
        await websocket.send_json({"type": "error", "detail": "unauthorized"})
        await websocket.close()
        return
    user = db.get_user_by_id(payload["uid"])
    if not user:
        await websocket.accept()
        await websocket.send_json({"type": "error", "detail": "unauthorized"})
        await websocket.close()
        return
    if not (slug.startswith(f"u{payload['uid']}-") or slug.startswith("flux-demo-")):
        await websocket.accept()
        await websocket.send_json({"type": "error", "detail": "forbidden"})
        await websocket.close()
        return
    await websocket.accept()
    _SUBS.setdefault(slug, set()).add(websocket)
    try:
        try:
            await websocket.send_json({"type": "snapshot", "tasks": hc.list_tasks(slug)})
        except Exception:
            pass
        while True:
            await asyncio.sleep(4)
            try:
                tasks = hc.list_tasks(slug)
                await websocket.send_json({"type": "update", "tasks": tasks})
            except Exception:
                await websocket.send_json({"type": "error", "detail": "board poll failed"})
    except WebSocketDisconnect:
        _SUBS.get(slug, set()).discard(websocket)


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)
