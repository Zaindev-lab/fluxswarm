"""
FluxSwarm backend - FastAPI server (auth + squad + plans + referrals + demo).

Each authenticated user owns an isolated set of Hermes kanban boards (prefixed
by their user id). The ECC devops squad (swarm) is launched per project.
"""
from __future__ import annotations

import asyncio
import sys
import atexit
import datetime
import ipaddress
import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
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


def _operator_maintenance() -> bool:
    """Operator kill-switch (FIX-1): when FLUXSWARM_KILL_SWITCH=1, ALL
    cost-bearing/demo surfaces reject with 503. Default off."""
    return os.environ.get("FLUXSWARM_KILL_SWITCH", "").strip().lower() in ("1", "true", "yes")


_DEMO_DAILY_CAP = int(os.environ.get("FLUXSWARM_DEMO_DAILY_CAP", "25"))


def _today() -> str:
    """Local calendar day (YYYY-MM-DD) — the period key for durable demo caps."""
    return datetime.date.today().isoformat()


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
def _bg_dispatch(slug: str, plan: str, provider_keys=None, pid: int | None = None) -> None:
    """Drive the dispatcher to a terminal state in a daemon thread.

    A single non-blocking pass only processes the tasks that are READY at that
    instant. Swarm workflows are multi-wave (workers -> verifier -> synthesizer):
    tasks created as earlier ones finish would never run, leaving the board
    stuck forever (reviewer `ready`, builder `todo`). Using the blocking
    multi-pass loop (bounded by timeout_s) closes that gap while the HTTP
    request still returns immediately — the loop lives in this thread.

    Provider resilience: the loop is bounded so an upstream LLM outage cannot
    hold the driver for many hours. On any failure, the project's
    ``launch_status`` is persisted (and the launch credit refunded when no
    meaningful work was produced) so the user is never charged for a build that
    never executed, and the UI can surface a truthful paused/failed state
    instead of an indefinitely-running board.
    """
    try:
        res = hc.dispatch(slug, max_spawn=db.PLANS.get(plan, {}).get("parallel", 1),
                          provider_keys=provider_keys, blocking=True,
                          timeout_s=hc.DISPATCH_TIMEOUT_S)
    except Exception as e:
        # Surface the failure for ops instead of silently dropping the swarm,
        # and protect the credit: a launch that errored before any agent
        # produced work is refunded (idempotently) and marked on the project.
        try:
            if pid is not None:
                _finalize_launch(slug, pid,
                                 status="error", outcome="launch_error",
                                 reason=type(e).__name__)
            audit.audit("dispatch.fire", outcome="error", slug=slug, reason=type(e).__name__)
        except Exception:
            pass
        return
    if pid is None:
        # No project row to persist to (demo/standalone); just audit the outcome.
        try:
            audit.audit("dispatch.fire", outcome=res.get("outcome", "ok"), slug=slug,
                        timed_out=bool(res.get("timed_out")))
        except Exception:
            pass
        return
    res_outcome = res.get("outcome", "ok")
    if res_outcome == "ok" or res.get("timed_out") is False:
        _finalize_launch(slug, pid, status="ok", outcome="converged", reason="")
    else:
        # Provider/worker stall or error: land the launch in a recoverable,
        # truthful state and refund the credit when no real work was produced.
        reason = "no_progress" if res.get("stall") else "timeout"
        _finalize_launch(slug, pid, status="stuck", outcome="stuck", reason=reason)


def _finalize_launch(slug: str, pid: int, *, status: str, outcome: str, reason: str) -> None:
    """Persist the launch terminal state and reconcile the launch credit.

    Credit policy (use the existing single-credit model): a launch that ends
    stuck / timed-out / errored BEFORE any task reached ``done`` produced no
    meaningful work, so its single credit is refunded — once, idempotently
    (guarded by the project's ``launch_refunded`` flag). A launch that
    completed at least one task consumed real work and is never refunded.
    """
    try:
        existing = _project_by_pid(pid)
        was_refunded = bool(existing and existing.get("launch_refunded"))
        refunded = was_refunded
        if outcome != "converged" and not was_refunded:
            try:
                work_done = hc.board_has_completed_work(slug)
            except Exception:
                work_done = False
            if not work_done:
                proj = existing
                if proj is not None and not proj.get("launch_refunded"):
                    if db.refund_launch_credit(proj["user_id"]):
                        refunded = True
                        try:
                            audit.audit("dispatch.fire", outcome="credit_refund",
                                        slug=slug, project_id=pid, reason=reason)
                        except Exception:
                            pass
        db.set_launch_outcome(pid, status, outcome, reason, refunded=refunded)
    except Exception:
        # Never let bookkeeping failure crash the daemon thread.
        try:
            audit.audit("dispatch.fire", outcome="reconcile_error", slug=slug, project_id=pid)
        except Exception:
            pass


def _project_by_pid(pid: int) -> dict | None:
    """Fetch a project row by id, including the launch bookkeeping columns."""
    try:
        c = db._conn()
        try:
            row = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            return dict(row) if row else None
        finally:
            c.close()
    except Exception:
        return None


def _fire_dispatch(slug: str, plan: str, provider_keys=None, pid: int | None = None) -> None:
    threading.Thread(target=_bg_dispatch, args=(slug, plan),
                     kwargs={"provider_keys": provider_keys, "pid": pid}, daemon=True).start()


# ---------- auth dependency ----------
def _token_session_ok(user: dict, payload: dict) -> bool:
    """True only when the JWT was issued after the user's last logout/reset.

    On logout / password change / password reset we set ``users.logged_out_at``
    to "now"; any token whose ``iat`` predates it is refused. Stateless JWTs
    carry no server-side revocation list, so this timestamp is the minimal
    correct revocation primitive: it kills every session of that user (the safe
    behaviour for all three operations) without a blacklist table.
    """
    logged_out = user.get("logged_out_at") or 0
    if not logged_out:
        return True
    return float(payload.get("iat") or 0) >= float(logged_out)


def get_current_user_optional(request: Request) -> dict | None:
    ah = request.headers.get("Authorization", "")
    token = ah.replace("Bearer ", "") if ah.startswith("Bearer ") else request.cookies.get("fs_token")
    if not token:
        return None
    payload = auth_mod.decode_token(token)
    if not payload:
        return None
    user = db.get_user_by_id(payload["uid"])
    if not user or not _token_session_ok(user, payload):
        return None
    return user


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
    if not _token_session_ok(user, payload):
        raise HTTPException(status_code=401, detail="انتهت الجلسة — سجّل الدخول مجدداً")
    return user


# ---------- schemas ----------
class RegisterIn(BaseModel):
    email: str
    name: str
    password: str = Field(max_length=4096)
    ref: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str = Field(max_length=4096)


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=4096)
    new: str = Field(max_length=4096)


class ResetRequestIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str = Field(min_length=8, max_length=128)
    new_password: str = Field(max_length=4096)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=4000)
    ref: str | None = None


# ---------- marketing / public ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    base = os.environ.get("FLUXSWARM_PUBLIC_BASE_URL", "").strip().rstrip("/")
    return templates.TemplateResponse(request=request, name="index.html",
                                      context={"title": "FluxSwarm", "canonical": base + "/" if base else ""})


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
    compute-costly Hermes swarm); a durable daily cap bounds total cost per
    bucket and an operator kill-switch can halt ALL demo/cost-bearing surfaces.
    """
    if _operator_maintenance():
        raise HTTPException(status_code=503, detail="الخدمة في صيانة مؤقتة — حاول لاحقاً")
    if not limiter.ip_allowed(_client_ip(request)):
        raise HTTPException(status_code=429, detail="محاولات كثيرة جداً — انتظر قليلاً")
    if db.bump_demo_usage("anon", _today()) > _DEMO_DAILY_CAP:
        raise HTTPException(status_code=429, detail="تجاوزت حد الاستخدام التجريبي اليومي")
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


@app.post("/api/auth/logout")
def api_logout(request: Request, user: dict = Depends(get_current_user)):
    """Log out the user: invalidates EVERY currently-issued session token.

    Stateless JWTs have no server-side list, so we record ``logged_out_at`` and
    refuse any token whose ``iat`` predates it. (The client should also discard
    its stored token.)
    """
    db.mark_logged_out(user["id"])
    audit.audit("auth.logout", uid=user["id"], email=user["email"],
                ip=_client_ip(request), outcome="ok")
    return {"ok": True, "note": "سُجّل خروجك — أزل الرمز من المتصفح"}


@app.post("/api/auth/password")
def api_change_password(p: PasswordChangeIn, request: Request,
                        user: dict = Depends(get_current_user)):
    """Authenticated password change: verify the current password, then set the
    new hash and invalidate all existing sessions (log out everywhere)."""
    if len(p.new) < 8:
        raise HTTPException(status_code=400, detail="كلمة المرور الجديدة قصيرة جداً (8 أحرف على الأقل)")
    if not db.authenticate(user["email"], p.current):
        audit.audit("auth.password", uid=user["id"], email=user["email"],
                    ip=_client_ip(request), outcome="fail", reason="bad_current")
        raise HTTPException(status_code=401, detail="كلمة المرور الحالية غير صحيحة")
    db.set_password(user["id"], p.new)
    audit.audit("auth.password", uid=user["id"], email=user["email"],
                ip=_client_ip(request), outcome="ok")
    return {"ok": True, "note": "تغيّرت كلمة المرور وخرجت من كل الجلسات"}


def _reset_self_service() -> bool:
    """Dev/test-only echo flag.

    There is no mailer in this install, so a minted reset token has no delivery
    channel. When FLUXSWARM_RESET_SELF_SERVICE=1 the token is returned in the
    response so the full flow can be exercised (tests, local dev). Production
    keeps it OFF: the token is still single-use + expiring + hashed at rest, but
    it is discarded after minting and the client only ever receives the same
    generic response (anti-enumeration).
    """
    return os.environ.get("FLUXSWARM_RESET_SELF_SERVICE", "").strip().lower() in ("1", "true", "yes")


@app.post("/api/auth/reset-request")
def api_reset_request(p: ResetRequestIn, request: Request):
    """Start a password reset. Identical response whether or not the email
    exists (no account enumeration); rate-limited like login/register."""
    ip = _client_ip(request)
    limiter.hit_ip(ip)
    if not limiter.ip_allowed(ip):
        raise HTTPException(status_code=429, detail="محاولات كثيرة جداً — انتظر قليلاً")
    email = p.email.lower().strip()
    user = db.get_user_by_email(email)
    data = {"ok": True, "detail": "إذا كان البريد مسجّلاً فيتلقّى رابط إعادة التعيين"}
    if user:
        raw = db.create_password_reset(user["id"])
        if _reset_self_service():
            data["reset_token"] = raw  # dev/test channel only (no mailer)
        audit.audit("auth.reset.request", uid=user["id"], email=user["email"],
                    ip=ip, outcome="ok", delivered=_reset_self_service())
    else:
        audit.audit("auth.reset.request", email=email, ip=ip, outcome="miss")
    return data


@app.post("/api/auth/reset")
def api_reset(p: ResetIn, request: Request):
    """Complete a reset: consume the single-use token, set a new password and
    invalidate all existing sessions (also usable by an operator from support)."""
    if len(p.new_password) < 8:
        raise HTTPException(status_code=400, detail="كلمة المرور قصيرة جداً (8 أحرف على الأقل)")
    uid = db.consume_password_reset(p.token)
    if not uid:
        raise HTTPException(status_code=400, detail="رمز إعادة التعيين غير صالح أو منتهٍ أو مستخدم من قبل")
    db.set_password(uid, p.new_password)
    u = db.get_user_by_id(uid)
    audit.audit("auth.reset", uid=uid, email=(u or {}).get("email", ""),
                ip=_client_ip(request), outcome="ok")
    return {"ok": True, "note": "أُعيد تعيين كلمة المرور — سجّل الدخول من جديد"}


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
    pid = db.add_project(user["id"], slug, payload.name or "مشروع", goal)
    _fire_dispatch(slug, user["plan"], _user_provider_keys(user), pid=pid)
    audit.audit("project.create", uid=user["id"], email=user["email"], ip=_client_ip(request),
                outcome="ok", slug=slug, plan=user["plan"])
    return {
        "slug": slug, "goal": payload.goal, "root_id": swarm.root_id,
        "workers": swarm.worker_ids, "verifier_id": swarm.verifier_id,
        "synthesizer_id": swarm.synthesizer_id,
    }


@app.get("/api/projects/{slug}/workspace")
def api_workspace(slug: str, user: dict = Depends(get_current_user)):
    # Same ownership rules as the task board: private boards require the owner,
    # demo boards are public showcase. Generated files are the user's "result".
    if not slug.startswith(f"u{user['id']}-") and not slug.startswith("flux-demo-"):
        raise HTTPException(status_code=403, detail="غير مصرّح")
    try:
        return {"slug": slug, "content": hc.read_workspace(slug)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    if _operator_maintenance():
        raise HTTPException(status_code=503, detail="الخدمة في صيانة مؤقتة — حاول لاحقاً")
    if slug.startswith("flux-demo-") and db.bump_demo_usage(f"u{user['id']}", _today()) > _DEMO_DAILY_CAP:
        raise HTTPException(status_code=429, detail="تجاوزت حد الاستخدام التجريبي اليومي")
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
    processed = _process_paddle_payload(body, signature, request)
    return {"accepted": True, "deduplicated": processed.get("deduplicated", False)}


@app.get("/api/payments/webhook")
def api_payments_webhook_get():
    raise HTTPException(status_code=405, detail="method not allowed")


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
        return """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>FluxSwarm · Checkout</title></head>
<body style="font-family:system-ui;max-width:560px;margin:60px auto;text-align:center">
<h2>Checkout is not ready yet</h2>
<p>PADDLE_CLIENT_TOKEN is not set — add the client token from your Paddle dashboard and restart.</p>
</body></html>"""
    env = ""
    if sandbox:
        env = "try { Paddle.Environment.set(\"sandbox\"); } catch (e) {}\n"
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>FluxSwarm · Secure checkout</title>
<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
</head>
<body style="font-family:system-ui;max-width:560px;margin:60px auto;text-align:center;line-height:1.8">
<h2 id="fs-status">Opening the secure payment window…</h2>
<p style="color:#666;font-size:14px">If the window does not appear within a few seconds, allow pop-ups and press the button, or reopen the link.</p>
<button id="fs-retry" onclick="openCheckout()" style="margin:14px 0;font-size:15px;padding:10px 24px;cursor:pointer;border-radius:8px;border:1px solid #1b6ef3;background:#1b6ef3;color:#fff">Open the payment window</button>
<script>
__PADDLE_ENV__
var fsStatus = document.getElementById('fs-status');
var fsLog = function (m) { console.log('[fluxswarm]', m); if (fsStatus) fsStatus.textContent = m; };
window.onerror = function (msg, src, line) { fsLog("Script error: " + msg + " (" + line + ")"); };
window.addEventListener('unhandledrejection', function (e) {
  fsLog("Unhandled failure: " + (e.reason ? (e.reason.message || e.reason) : "unknown"));
});

var txn = new URLSearchParams(window.location.search).get('_ptxn');
var watchdog = null;
var initialized = false;

function openCheckout() {
  if (typeof Paddle === 'undefined') {
    fsLog("The payment engine (cdn.paddle.com) did not load in this browser. Try: refresh the page, a different browser, or disable the ad blocker.");
    return;
  }
  if (!txn) {
    fsLog("This link is incomplete — reopen it from the subscription page.");
    return;
  }
  fsLog("Opening the secure payment window…");
  try {
    Paddle.Checkout.open({ transactionId: txn, settings: { displayMode: "overlay" } });
    watchdog = setTimeout(function () {
      fsLog("The window was not confirmed within 8 seconds — press the button above to try again.");
    }, 8000);
  } catch (err) {
    fsLog("Could not open checkout: " + err.message);
  }
}

var sdkAttempts = 0;
function retrySdk() {
  if (sdkAttempts >= 3) {
    fsLog("Repeated failure — the payment engine cannot be reached from this browser. Try a different browser or disable the ad blocker, then reload the page.");
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
    fsLog("Failed to initialize Paddle: " + err.message);
    return;
  }
  Paddle.Checkout.on('checkout.loaded', function () { watchdog && clearTimeout(watchdog); fsLog("The payment window is open."); });
  Paddle.Checkout.on('checkout.closed', function () { fsLog("The window was closed — press the button to continue."); });
  Paddle.Checkout.on('error', function (data) { watchdog && clearTimeout(watchdog); fsLog("Paddle error: " + (data && data.error ? data.error : "unknown") + " — press the button to retry."); });
  Paddle.Checkout.on('transaction.completed', function () { fsLog("Payment completed — confirming…"); });
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
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>FluxSwarm · Simulated payment</title></head>
<body style="font-family:system-ui;max-width:560px;margin:60px auto;text-align:center;line-height:1.8">
<h2>Simulated payment (local mode — no real money)</h2>
<p>Plan: <b>{db.PLANS[plan]['name']}</b> · Amount: <b>${price}</b> (simulated)</p>
<form method="get" action="/api/payments/dev-complete/{user_id}/{plan}">
<button style="font-size:16px;padding:10px 22px;cursor:pointer">Complete payment (simulated)</button>
</form>
<p style="color:#888;font-size:13px">This page goes through the same Paddle webhook path: a correctly-signed payload is generated and processed by the production webhook handler.</p>
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
 line-height:1.7;color:#222}}h1{{font-size:1.6rem}}a{{color:#0b59c5}}</style></head>
 <body><p style="color:#777;font-size:.85rem">آخر تحديث: 30 أغسطس 2026</p>{body}</body></html>"""

_LEGAL_BASE_EN = '<!doctype html><html lang="en"><head><meta charset="utf-8">\n' \
    '<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>\n' \
    '<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;\n' \
    'line-height:1.7;color:#222}}h1{{font-size:1.6rem}}a{{color:#0b59c5}}</style></head>\n' \
    '<body><p style="color:#777;font-size:.85rem">Last updated: 30 August 2026</p>{body}</body></html>'


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
<p>تُجمع البيانات التالية لتشغيل الخدمة فقط: البريد الإلكتروني والاسم وكلمات المرور (مشفّرة Argon2id) ومفاتيح مزوّدي الذكاء الاصطناعي (مشفّرة فورياً بـ Fernet) وأهداف المشاريع ومخرجاتها وسجلُّ الاستخدام والتدقيق ومعلومات الدفع الأساسية.</p>
<p>لا تُباع البيانات ولا تُستخدم في الإعلانات. نشاركها فقط: (1) مع معالج الدفع Paddle (تاجر السجلّ) لإتمام المعاملات، و(2) مع مزوّد الذكاء الاصطناعي الذي تختاره أنت عند تشغيل السرب (BYOK) لتنفيذ هدفك، وفق شروط ذلك المزوّد. لا تدرب المنصة على بياناتك.</p>
<p>حقوقك (CCPA/CPRA): حق الاطلاع على بياناتك عبر <code>GET /api/account/export</code> (أو من لوحة الحساب)، وحق التصحيح والحذف الكامل عبر <code>DELETE /api/account</code> وحذف مفاتيحك فوراً، ولن تمرّ طلبات التصحيح الأخرى وسيلة <a href="mailto:{c}">{c}</a>. سياق بيانات التخزين: عند الإطلاق تُستضاف الخوادم في أمريكا الشمالية؛ اخترنا هذا الموقع لحوسبة الدفع والتشفير — راجع/ي «النقل الدولي» في النسخة الإنجليزية.</p>
<p>سجلّ التدقيق الأمني (Append-only) مستبعد من الحذف: يُحتفظ به للأغراض الأمنية والتحقيقية فقط ولا يستخدم تسويقياً ولا للتدريب، وقد تتضمن مدخلاته البريد الإلكتروني وعنوان IP تلقائياً لأغراض التحقيق في إساءة الاستخدام، وهي غير قابلة للمحو. الأثاث الناتج عن تشغيل السرب (ملفات المنتج المولّدة على القرص) تُحذف عند حذف الحساب في الإصدارات اللاحقة؛ إلى حينه يمكنك طلب الحذف عبر البريد. تفاصيل الملفات المنقولة وعوامل الاحتفاظ موجودة في صفحة <a href="/cookies">ملفات تعريف الارتباط والتتبّع</a> واسترداد الأموال في <a href="/refund">سياسة الاسترداد والرصيد</a>.</p>
<p>انات المملكة المتحدة والاتحاد الأوروبي (إضافة بريطانية/أوروبية): الأساس القانوني للمعالجة هو تنفيذ العقد، والمصلحة المشروعة (أمان النظام ومكافحة الاحتيال)، والالتزام القانوني (سجلات الفوترة). حقوقك تشمل الوصول والتصحيح والمحو ونقل البيانات والاعتراض على المعالجة وشكوى لدى سلطة حماية البيانات (في بريطانيا: مكتب مفوّض المعلومات). قد تُنقل بياناتك إلى مزوّدي الذكاء الاصطناعي خارج المملكة/الاتحاد وفق شروطهم؛ ولا ننقلها لأغراض تسويقية.</p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("ar")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>أسئلة: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE.format(title="سياسة الخصوصية", body=body)


@app.get("/privacy-en", response_class=HTMLResponse)
def privacy_page_en():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>Privacy Policy</h1>
<p>We process only the data needed to operate the service: email, name, password (Argon2id), user-supplied AI provider keys (Fernet-encrypted), project goals and generated outputs, usage/audit records, and minimal payment metadata.</p>
<p>We do not sell your data and do not use it for ads. We share it only (1) with Paddle (merchant of record) to complete transactions and (2) with the AI provider of your choice (BYOK) to execute your goal under that provider's terms. We do not train on your data.</p>
<p>Your rights (CCPA/CPRA): access via <code>GET /api/account/export</code>, rectification and full erasure via <code>DELETE /api/account</code> (including immediate key deletion). Other correction requests: <a href="mailto:{c}">{c}</a>. On launch, data is hosted on servers in North America.</p>
<p>The security audit log is append-only and excluded from erasure: it is retained for security/investigation purposes only, never for marketing or training; its entries may include your email address and IP address automatically.</p>
<p>UK/EU addendum: lawful bases are performance of the contract, legitimate interests (system security, fraud prevention) and legal obligation (billing records). Your rights include access, rectification, erasure, portability, objection, and complaint to your supervisory authority (in the UK: the ICO). Your data may be transferred to the AI provider you choose, outside the UK/EU, under that provider's terms; we do not transfer it for marketing. Cookies and tracking are described on <a href="/cookies-en">/cookies</a>; refunds and credits on <a href="/refund-en">/refund</a>.</p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("en")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>Questions: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE_EN.format(title="Privacy Policy", body=body)


@app.get("/terms", response_class=HTMLResponse)
def terms_page():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>شروط الاستخدام (Terms of Service)</h1>
<p>تُقدَّم الخدمة «كما هي». تُعالَج المدفوعات بواسطة Paddle (تاجر السجلّ) وفق شروطها، وتشمل الضرائب وضريبة القيمة المضافة حيثما انطبق.</p>
<h2>الائتمانات</h2><p>الائتمانات رصيد خدمة مسبق الدفع يُمنح فقط بعد تأكيد الدفع. كل إطلاق مشروع يكلّف رصيداً واحداً ولا تنتهي صلاحية الائتمانات. يُعاد الرصيد تلقائياً عند فشل الإطلاق قبل استهلاك أي عمل، وفي حال استرداد مبلغ من Paddle تُحوَّل الباقة إلى Demo ويبقى الرصيد الحالي.</p>
<h2>بياناتك ومفاتيحك</h2><p>أنت مسؤول عن الأهداف التي ترسلها وعن مفاتيح المزوّدين التي تخزّنها (راجع سياسة الخصوصية لطريقة حمايتها). تُستخدم المفاتيح فقط لتنفيذ إطلاقك الخاص.</p>
<h2>المخرجات</h2><p>مخرجات السرب ملكك، وفق شروط مزوّدي الذكاء الاصطناعي المستخدمين وأي مكونات طرف ثالث داخلها. تستخدم الخدمة برمجيات تشغيل ومهارات وكلاء مفتوحة المصدر (Hermes؛ ECC) وتراخيصها تعود لمؤلفيها.</p>
<h2>الاستخدام المقبول</h2><p>إساءة الاستخدام أو المحتوى غير القانوني أو النشاط الضار ممنوع وقد يؤدي لإيقاف الحساب (انظر <a href="/acceptable-use">سياسة الاستخدام المقبول</a>).</p>
<h2>التوفر وإنهاء الحساب</h2><p>نعمل على إبقاء الخدمة متاحة دون ضمان استمرارية غير منقطعة. يمكنك حذف حسابك (وكل بياناته ومجالسه) من التطبيق في أي وقت، وقد نعلّق الحسابات المخالفة.</p>
<h2>تحديد المسؤولية</h2><p>إلى أقصى حد يسمح به القانون، تُقدَّم الخدمة «كما هي» دون ضمانات، وتُحدَّد المسؤولية عن الخدمة ومخرجاتها كما يسمح به القانون؛ ولا يُسقَط ما لا يمكن إسقاطه قانوناً ولا حقوق المستهلك الإلزامية (بما فيها في المملكة المتحدة والاتحاد الأوروبي).</p>
<h2>القانون الحاكم والاختصاص</h2><p>تخضع هذه الشروط للقانون المعمول به؛ حقوق المستهلك الإلزامية في بلدك لا تتأثر. تُراجع تفاصيل الاختصاص قانونياً مع توسع الخدمة. أسئلة: <a href="mailto:{c}">{c}</a>.</p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("ar")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>اتصل بنا: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE.format(title="شروط الاستخدام", body=body)


@app.get("/terms-en", response_class=HTMLResponse)
def terms_page_en():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>Terms of Service</h1>
<p>The service is provided &quot;as is&quot;. Paid transactions are processed by
Paddle (merchant of record) under its own terms; any country-specific tax or VAT
is handled by Paddle.</p>
<h2>Credits</h2><p>Credits are a prepaid service balance, granted only after a
confirmed payment. Each launched project costs 1 credit. Credits never expire.
A launch that fails before the squad does any work refunds the credit to your
account automatically. A merchant refund downgrades your plan to Demo and keeps
your current credit balance.</p>
<h2>Your data and your keys</h2><p>You remain responsible for the goals you submit
and for the provider keys you store (see the privacy policy for how they are
protected). User-supplied API keys are used only to execute your own launches.</p>
<h2>Output</h2><p>You own the generated output, subject to the terms of the AI
providers you used and to any third-party components included in it. The service
uses third-party execution software and open-source agent skill profiles (Hermes;
ECC); their licences belong to their respective authors.</p>
<h2>Acceptable use</h2><p>Abuse, unlawful content, or harmful swarm activity is
prohibited and may result in account suspension. See the <a href="/acceptable-use-en">acceptable-use policy</a>.</p>
<h2>Availability</h2><p>We work to keep the service available but do not guarantee
uninterrupted availability.</p>
<h2>Termination</h2><p>You can delete your account (and its data and boards) at any
time from the app. We may suspend accounts that violate these terms or the
acceptable-use policy.</p>
<h2>Limitation of liability</h2><p>To the maximum extent permitted by applicable
law, the service is provided &quot;as is&quot; without warranties, and liability
for the service and the generated output is limited as permitted by law. This does
not limit or exclude liability that cannot be limited or excluded by law, and
does not affect any statutory consumer rights you have (including in the UK and
the EU).</p>
<h2>Governing law and jurisdiction</h2><p>These terms are governed by applicable
law. If you are a consumer in the UK, EU or another jurisdiction with mandatory
consumer protections, your rights under that law are not affected. Jurisdiction
specifics are kept under legal review as the service expands. Questions: <a href="mailto:{c}">{c}</a>.</p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("en")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>Contact: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE_EN.format(title="Terms of Service", body=body)


# ---------- legal: refund & credits (no-refund absolutes) ----------
@app.get("/refund", response_class=HTMLResponse)
def refund_page():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>سياسة الاسترداد والرصيد</h1>
<p>الرصيد (الائتمانات) هومنتج عضوياً: لا تنتهي صلاحيته ولا يمكن شحنه خارج الخدمة.</p>
<p>متى تُسترد الأموال تلقائياً:</p>
<ul>
<li>عند فشل إطلاق السرب ولم يُستهلك أي عمل — يُعاد الرصيد تلقائياً إلى حسابك.</li>
<li>عند استرداد مبلغ من Paddle — تُحوَّل باقتك إلى Demo ويبقى رصيدك الحالي بحوزتك.</li>
</ul>
<p>متى يُنظر في استرداد نقدي (استثمارية، خلال 14 يوماً من أول تفعيل لأي باقة مدفوعة، بعد خصم العمل المستهلَك): لا استرداد كامل تلقائياً؛ المتاجر الإلكترونية إن كانت قد حدّت استخدامك. الطلبات خلال 14 يوماً من الشراء تُعالج قبل سحب الرصيد المستهلك. بعد 14 يوماً: لا استرداد نقدي للاستخدام المستهلك، لكن الرصيد غير المستهلَك قابل للاسترداد النقدي حسب <a href="mailto:{c}">{c}</a> وحسب شروط Paddle.</p>
<p>العمليات تتم حصراً عبر Paddle (تاجر السجلّ) وسياسة الاسترداد التي تفرضها قوانين بلدك (بما فيها حقوق المستهلك في المملكة المتحدة والاتحاد الأوروبي) لا تُسقَط هذه البنود. لمطالبات نزاعات: <a href="mailto:{c}">{c}</a>.</p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("ar")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>استفسارات: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE.format(title="سياسة الاسترداد والرصيد", body=body)


@app.get("/refund-en", response_class=HTMLResponse)
def refund_page_en():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>Refund &amp; Credit Policy</h1>
<p>Credits are service credits: they never expire and cannot be withdrawn outside the service.</p>
<p>Automatic grants:</p>
<ul>
<li>If a swarm launch fails and no work was consumed, the credits are returned to your account automatically.</li>
<li>If you obtain a monetary refund from Paddle, your plan is downgraded to Demo and your current credit balance stays with you.</li>
</ul>
<p>Monetary refunds (at our discretion, within 14 days of your first paid activation, net of consumed work): there is no automatic full-refund policy; we review requests individually. Requests within 14 days of purchase are processed before deducting consumed credits. After 14 days, no monetary refund for consumed usage, but any unconsumed credit balance may be refunded via <a href="mailto:{c}">{c}</a> subject to Paddle's process.</p>
<p>All payments are handled by Paddle (merchant of record). Your statutory consumer rights (including UK and EU) are not waived by any of these terms. Disputes: <a href="mailto:{c}">{c}</a>.</p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("en")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>Questions: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE_EN.format(title="Refund &amp; Credit Policy", body=body)


# ---------- legal: cookies & tracking ----------
@app.get("/cookies", response_class=HTMLResponse)
def cookies_page():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>ملفات تعريف الارتباط والتتبّع</h1>
<p>لا يضع الخادم أي ملفات تعريف ارتباط للتتبع؛ الجلسة تعتمد رمز JWT يُحفظ في <code>localStorage</code> ويتلاشى خلال 7 أيام أو عند خروجك. القيمة المحلية الوحيدة الأخرى هي <code>flux-lang</code> (تفضيل اللغة).</p>
<p>لا أدوات تحليلات، لا إعلانات، لا بكسل تتبّع، ولا أطراف ثالثة متتبّعة. منذ أن لا نستخدم ملفات تعريف ارتباط للإعلان أو التحليلات، لا يُشترَط لافتة موافقة مسبقة بموجب لوائح الكوكيز البريطانية (PECR) على موقعنا. عند الدفع ينشئ Paddle ملفات تعريف ارتباط على نطاقه الخاص فقط، وليس على نطاقنا.</p>
<p>لمزيد: <a href="/refund">الاسترداد</a> · <a href="/privacy">الخصوصية</a> · <a href="/acceptable-use">الاستخدام المقبول</a>.</p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("ar")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>استفسارات: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE.format(title="ملفات تعريف الارتباط والتتبّع", body=body)


@app.get("/cookies-en", response_class=HTMLResponse)
def cookies_page_en():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>Cookies &amp; Tracking</h1>
<p>The FluxSwarm server sets no tracking cookies. Your session uses a JWT held in browser <code>localStorage</code>; the only other local value is <code>flux-lang</code> (language preference).</p>
<p>There is no analytics, no advertising, no tracking pixels and no third-party trackers. When you pay, Paddle sets cookies on its own domain only, never ours.</p>
<p>Because we do not use cookies for advertising or analytics, no prior consent banner is required under UK PECR for our own site. Paid pages are served by Paddle's checkout under its own notice. Contact: <a href="mailto:{c}">{c}</a></p>
<p>See also <a href="/refund-en">refund policy</a> and <a href="/privacy-en">privacy</a>.</p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("en")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>Questions: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE_EN.format(title="Cookies &amp; Tracking", body=body)


# ---------- marketing: public product pages ----------
_MARKET_CSS = """body{font-family:ui-sans-serif,system-ui,"Segoe UI",Tahoma;margin:0;background:#0a0c11;color:#eef1f6;line-height:1.6}
.wrap{max-width:980px;margin:0 auto;padding:28px 20px 60px}
.top{display:flex;align-items:center;gap:14px;padding:14px 20px;border-bottom:1px solid #232837;background:#0e1117}
.top .logo{font-weight:800;background:linear-gradient(90deg,#6d7cfa,#9d8bff);-webkit-background-clip:text;background-clip:text;color:transparent;font-size:19px}
.top nav{margin-left:auto;display:flex;gap:16px} .top nav a{color:#98a0af;text-decoration:none;font-size:14px} .top nav a:hover{color:#6d7cfa}
h1{font-size:30px;margin:18px 0 6px;letter-spacing:-.3px} h2{font-size:20px;margin:26px 0 8px;color:#cdd4e2}
p{color:#98a0af} a{color:#6d7cfa} li{color:#98a0af;margin:5px 0}
.plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin:20px 0}
.pl{background:#0e1117;border:1px solid #232837;border-radius:14px;padding:18px}
.pl.hot{border-color:#6d7cfa;box-shadow:0 0 0 1px #6d7cfa}
.pl .n{font-size:17px;font-weight:800} .pl .p{font-size:24px;font-weight:800;margin:6px 0}
.pl .p small{color:#98a0af;font-weight:400;font-size:13px} .pl ul{list-style:none;padding:0;margin:8px 0;font-size:13px}
.cmp{width:100%;border-collapse:collapse;font-size:14px} .cmp th,.cmp td{border:1px solid #232837;padding:10px 12px;text-align:left}
.cmp th{color:#cdd4e2} .cmp td{color:#98a0af}
.foot{border-top:1px solid #232837;padding:16px 0;font-size:13px;color:#7a8699}
.foot a{color:#6d7cfa}"""

_MARKET_BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{desc}"><title>{title}</title>
<style>{css}</style></head><body>
<div class="top"><span class="logo">FluxSwarm</span><nav>
<a href="/">Go to app</a><a href="/pricing">Pricing</a><a href="/how-it-works">How it works</a><a href="/faq">FAQ</a></nav></div>
<div class="wrap">{body}<div class="foot">FluxSwarm · <a href="/privacy-en">Privacy</a> ·
<a href="/terms-en">Terms</a> · <a href="/refund-en">Refund</a> ·
<a href="/cookies-en">Cookies</a> · <a href="/acceptable-use-en">Acceptable use</a></div></div>
</body></html>"""


@app.get("/pricing", response_class=HTMLResponse)
def pricing_page():
    plans = [{"id": pid, **db.PLANS[pid]} for pid in db.PLAN_ORDER]
    rows = "".join(
        f'<div class="pl {"hot" if p["name"].lower() == "pro" else ""}">'
        f'<div class="n">{p["name"]}</div>'
        f'<div class="p">${p["price"]}<small> /once (credit pack)</small></div>'
        f'<ul><li>{p["credits"]} credits · 1 credit = 1 swarm launch</li>'
        f'<li>{p["parallel"]}-agent parallel cap</li>'
        f'<li>{p["desc"]}</li></ul></div>'
        for p in plans)
    body = f"""<h1>Simple pricing, no token meters</h1>
<p>Every launch costs exactly <strong>1 credit</strong>. Bring your own AI key and
you pay only your provider's token rate — FluxSwarm charges the flat 1-credit
coordination fee per launched project and nothing else. No key yet? The default
free hosted model runs the squad end-to-end too.</p>
<div class="plans">{rows}</div>
<p>Credits are prepaid and never expire; a failed launch is refunded automatically.
Payments are processed by Paddle (merchant of record), which handles sales tax and
VAT remittance. A merchant refund downgrades you to Demo and keeps your current
balance. See the <a href="/refund-en">refund policy</a>. Prices are shown in USD;
GBP pricing is applied by Paddle at checkout for UK customers.</p>"""
    return _MARKET_BASE.format(title="Pricing — FluxSwarm", desc="1 credit per launch, BYOK AI builders", css=_MARKET_CSS, body=body)


@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works_page():
    body = """<h1>How FluxSwarm works</h1>
<h2>What is FluxSwarm?</h2><p>FluxSwarm is a hosted AI development squad. You
describe a product you want built; a team of specialized agents plans, builds,
tests, reviews and assembles it on a live task board while you watch.</p>
<h2>Who is it for?</h2><p>Solo developers, startups and small teams who want a
concrete first version built — with a live picture of the work and the generated
files in your workspace — without wiring up an agent pipeline themselves.</p>
<h2>1. Type a goal</h2><p>Describe the product in one paragraph. The squad plans
the rest.</p>
<h2>2. A 6-agent squad takes over</h2><p>Your goal is broken into work and
assigned to a real agent crew, watched live on a kanban board:
<strong>Planner</strong> (breakdown) → <strong>Architect</strong> (structure) →
<strong>DevOps</strong> (scaffold &amp; CI/CD) → <strong>TDD</strong> (tests) →
<strong>Reviewer</strong> (verify) → <strong>Builder</strong> (merge to output).
The agents run inside the Hermes execution runtime using the open-source ECC
skill profiles — you do not need to install or manage either.</p>
<h2>3. Pick a model — bring a key or use the free one</h2><p>Bring your own AI
provider key (Anthropic Claude, OpenAI, Gemini or Kimi) for stronger output; your
key is Fernet-encrypted at rest, injected into the agent process only at launch,
and never returned by the API. You pay your provider's token price. Without a key,
the squad runs on a free hosted model included with the service.</p>
<h2>4. Pick it up from the workspace</h2><p>Generated files land in your project
workspace, browsable in the UI on the board, ready to push to your own repository.</p>
<h2>Billing</h2><p>1 credit per launched project across every plan. Credits are
prepaid, never expire, and are refunded automatically if a launch fails. There is
no monthly fee — credit packs set your plan tier (parallel execution cap) and
credit balance. See <a href="/pricing">pricing</a>.</p>
<h2>Transparency</h2><p>FluxSwarm is the product. Hermes is the execution runtime
that drives the board, and ECC is an underlying open-source component (agent skill
profiles) used inside it. Both are third-party components; FluxSwarm is not Hermes
and does not own ECC. Their availability is required to run a launch, and their
licences are their respective authors'.</p>"""
    return _MARKET_BASE.format(title="How it works — FluxSwarm", desc="A 6-agent AI development squad on a live board, 1 credit per launch", css=_MARKET_CSS, body=body)


@app.get("/faq", response_class=HTMLResponse)
def faq_page():
    body = """<h1>FAQ</h1>
<h2>What is FluxSwarm?</h2><p>An AI development squad: type a goal and six
specialized agents (Planner, Architect, DevOps, TDD, Reviewer, Builder) plan,
build, test, verify and assemble it on a live task board, with the generated files
in your workspace.</p>
<h2>Does the squad need my AI key?</h2><p>No. A free hosted model is the default, so
the squad runs end-to-end with no key. Bringing your own key (Anthropic Claude,
OpenAI, Gemini or Kimi) is optional and lifts output quality; your key pays your
provider&rsquo;s token rate. Keys are Fernet-encrypted at rest, injected only at
launch, and never returned by the API.</p>
<h2>What is Hermes? What is ECC?</h2><p>Hermes is the execution runtime that drives
the board. ECC is an underlying open-source component: the agent skill profiles
the squad uses. FluxSwarm is the product that orchestrates them; it is not Hermes
and does not own ECC. Both are required to run a launch.</p>
<h2>Does running without a key cost me anything?</h2><p>Each launch costs 1 credit.
The Demo plan starts you with 3 free credits (no card). Credit never pays model
tokens on the free model — you pay the 1-credit coordination fee only.</p>
<h2>How much do paid plans cost?</h2><p>Credit packs, not subscriptions. Starter
$29/25 credits, Pro $99/120 credits, Scale $299/500 credits (USD; GBP applied at
checkout by Paddle). Credits never expire. Billing runs through Paddle (merchant
of record).</p>
<h2>How fast are launches?</h2><p>Swarm duration depends on the goal, the model
in use and system load. The board streams progress live so you can watch it from
start to finish rather than guess.</p>
<h2>Can I cancel a Launch?</h2><p>There is no recurring subscription to cancel —
you buy credit packs and spend them. You can stop watching a board at any time;
refunds and unused credits are covered below.</p>
<h2>What if a Launch fails?</h2><p>If the launch fails before the squad does any
work, the credit is refunded to your account automatically. The run state stays
visible on the board for debugging.</p>
<h2>What if Hermes or the model provider fails?</h2><p>The launch reports a clear
error (the runtime or a provider key may be unavailable or misconfigured) and
the credit is refunded automatically. Your stored keys are never consumed by the
failure.</p>
<h2>What happens when Credits run out?</h2><p>Launching requires 1 credit. With zero
credits you keep access to your boards and data; you just cannot start new
launches until you add credits to the account.</p>
<h2>Can I sell what the squad builds?</h2><p>You own the generated output (subject
to the terms of the AI provider you used and any third-party components). You can
also publish your own squad templates on the marketplace and earn a 50% author
share on every sale, paid in credits.</p>
<h2>How do referrals work?</h2><p>Share your referral link; when a referred account
subscribes to a paid plan you earn 25 credits, once per referred email. Self-referral
and abusing the program (for example creating fake referrals) is prohibited and
rewards may be clawed back.</p>
<h2>Is my API key stored? Can FluxSwarm access my provider account?</h2><p>Keys are
stored encrypted (Fernet) and used only to execute your own launches. FluxSwarm
does not hold your provider account credentials, cannot browse your provider
account, and never shows a stored key back to anyone. You can delete a key or your
whole account from the app.</p>
<h2>Is my data private?</h2><p>Projects are namespace-isolated per account
(cross-user access returns 403). You can export your data and erase your account
(and its boards) from the app&rsquo;s account section. We do not sell data and do
not train on it. Details on the <a href="/privacy-en">privacy policy</a>.</p>
<h2>How long is data retained?</h2><p>Until you delete your account, or per the data
lifecycle described in the <a href="/privacy-en">privacy policy</a>. A security
audit log is kept separately for abuse investigation and is excluded from
deletion. Cookies and local storage are described on the <a href="/cookies-en">cookies</a> page.</p>
<h2>What about unused credits after I stop paying?</h2><p>Credits never expire and
are not tied to a recurring payment. Unused credits stay on the account; the
refund policy covers the rest.</p>
<h2>What is the refund policy?</h2><p>Failed launches refund the credit
automatically. Merchant refunds downgrade the plan to Demo and keep your balance.
Monetary refunds are reviewed case-by-case (see the <a href="/refund-en">refund policy</a>);
statutory consumer rights are not waived.</p>
<h2>What support is available?</h2><p>Email support at the contact address on the
legal pages and in the app footer. Self-serve: this FAQ, the how-it-works guide,
and the live board you can inspect during every launch.</p>"""
    return _MARKET_BASE.format(title="FAQ — FluxSwarm", desc="Answers on pricing, credits, BYOK, privacy and the agent squad", css=_MARKET_CSS, body=body)
@app.get("/acceptable-use", response_class=HTMLResponse)
def acceptable_use_page():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>الاستخدام المقبول</h1>
<p>باستخدامك FluxSwarm تقرّ بأنك: (1) لن تستخدم المنصة في المحتوى غير القانوني أو الخبيث أو الاستغلالي أو انتهاك الحقوق (بما فيها الملكية الفكرية وحقوق الغير)؛ (2) لن تشغّل سرباً يهدف لإحداث ضرر أو أي أنشطة عنيفة أو احتيالية؛ (3) لن تعيد بيع الائتمانات أو تحويلها نقداً خارج سياسة الاسترداد؛ (4) لن تحاول الوصول غير المصرّح به أو تسريب مفاتيح الغير أو كشط الواجهة آلياً بما يتجاوز الحدود؛ (5) ستلتزم بشروط مزوّدي الذكاء الاصطناعي الذين تستخدمهم عبر BYOK.</p>
<p>قد تُعلّق الحسابات المخالفة وتُحال التفاصيل المشبوهة للجهات المختصة، وقد تُستردّ الائتمانات عبر التحقيق وفق <a href="/refund">سياسة الاسترداد</a>.</p>
<p>استفسارات: <a href="mailto:{c}">{c}</a></p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("ar")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>أسئلة: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE.format(title="الاستخدام المقبول", body=body)


@app.get("/acceptable-use-en", response_class=HTMLResponse)
def acceptable_use_page_en():
    contact = os.environ.get("FLUXSWARM_CONTACT_EMAIL", "support@fluxswarm.ai")
    body = """<h1>Acceptable Use</h1>
<p>By using FluxSwarm you agree that you will (1) not use the platform for unlawful, malicious, exploitative or infringing content (including intellectual property and others' rights); (2) not run a swarm aimed at harm, violence or fraud; (3) not resell credits or convert them to cash outside the refund policy; (4) not attempt unauthorized access, leak others' keys, or scrape the API beyond stated limits; (5) comply with the terms of the AI providers you use via BYOK.</p>
<p>Violating accounts may be suspended and suspicious activity may be reported to authorities; credits may be recovered following investigation per the <a href="/refund-en">refund policy</a>.</p>
<p>Questions: <a href="mailto:{c}">{c}</a></p>"""
    body = body.format(c=contact)
    body += _legal_entity_block("en")
    _phone = os.environ.get("FLUXSWARM_LEGAL_PHONE", "").strip()
    body += f'<p>Questions: <a href="mailto:{contact}">{contact}</a>'
    body += f" · {_phone}</p>" if _phone else "</p>"
    return _LEGAL_BASE_EN.format(title="Acceptable Use", body=body)


# ---------- compliance: CCPA/CPRA account rights ----------
@app.get("/api/account/export")
def api_account_export(request: Request, user: dict = Depends(get_current_user)):
    payload = db.account_payload(user["id"])
    audit.audit("account.export", uid=user["id"], email=user["email"], ip=_client_ip(request), outcome="ok")
    return payload


@app.delete("/api/account")
def api_account_delete(request: Request, user: dict = Depends(get_current_user)):
    uid = user["id"]
    # Collect the user's OWN board slugs BEFORE the DB rows are erased, then
    # delete their Hermes kanban workspaces from disk too (CCPA right to
    # erasure). Demo boards (flux-demo-*) are shared/public and never user-owned,
    # so they are intentionally left intact.
    own_slugs = [pr["board_slug"] for pr in db.list_user_projects(uid)
                 if pr["board_slug"].startswith(f"u{uid}-")]
    vault.delete_user_key(uid)
    removed = db.delete_user(uid)
    boards_deleted = 0
    if removed:
        try:
            boards_deleted = hc.delete_boards(own_slugs)
        except Exception:
            boards_deleted = -1  # DB already erased; disk cleanup stays best-effort
    audit.audit("account.delete", uid=uid, email=user["email"], ip=_client_ip(request),
                outcome="ok" if removed else "missing", boards_deleted=boards_deleted)
    return {"ok": removed, "note": "تم حذف الحساب وكل البيانات المرتبطة به",
            "boards_deleted": boards_deleted}


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
    pid = None
    try:
        pid = db.add_project(user["id"], slug, tpl.get("name", "marketplace-squad"), goal)
        hc.ensure_board(slug)
        hc.launch_from_template(slug, goal, tpl.get("agents", []),
                                provider_keys=_user_provider_keys(user))
        # Auto-dispatch through the same background path as projects/demo so the
        # multi-wave swarm (workers -> verifier -> synthesizer) drives to
        # completion instead of stalling after the first ready-wave.
        _fire_dispatch(slug, user["plan"], _user_provider_keys(user), pid=pid)
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
    if not _token_session_ok(user, payload):
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


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return (BASE / "static" / "robots.txt").read_text(encoding="utf-8")


@app.get("/sitemap.xml", response_class=Response)
def sitemap_xml():
    return Response((BASE / "static" / "sitemap.xml").read_text(encoding="utf-8"),
                    media_type="application/xml")


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)
