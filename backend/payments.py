"""Pluggable payment gateway abstraction for FluxSwarm.

FluxSwarm keeps the dev-mode billing gate (``FLUXSWARM_PAYMENTS`` -> 402) until a
real provider is wired. This module defines a clean, provider-agnostic
interface so a real gateway (Stripe, Paddle, LemonSqueezy, …) can be dropped in
without touching the API layer.

Only stdlib is used here so the module is import-safe in any environment and
adds zero production dependencies. ``StubGateway`` is the default no-op
implementation used while ``FLUXSWARM_PAYMENTS`` is off (or for tests / local
dev). It deliberately never reports a session as paid.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CheckoutSession:
    """Opaque reference to a provider checkout session."""

    id: str
    status: str  # open | paid | expired
    url: str | None = None


class PaymentGateway(ABC):
    """Provider-agnostic contract every gateway must satisfy."""

    name: str = "abstract"

    @abstractmethod
    def create_checkout(
        self,
        *,
        plan: str,
        user_id: int,
        amount_cents: int,
        currency: str = "usd",
    ) -> CheckoutSession:
        """Open a checkout session for a paid plan. Returns an opaque id."""
        raise NotImplementedError

    @abstractmethod
    def is_paid(self, session_id: str) -> bool:
        """True only when the provider confirms the session was paid."""
        raise NotImplementedError

    @abstractmethod
    def handle_webhook(self, payload: bytes, signature: str | None = None) -> dict:
        """Verify + parse a provider webhook; return a normalized receipt."""
        raise NotImplementedError


class StubGateway(PaymentGateway):
    """No-op gateway. Used when no real provider is configured.

    It always reports ``is_paid == False`` so the dev 402 behaviour is
    preserved and no real money path is ever exercised by accident.
    """

    name = "stub"

    @property
    def configured(self) -> bool:
        return False

    @property
    def operative(self) -> bool:
        return False

    def create_checkout(
        self,
        *,
        plan: str,
        user_id: int,
        amount_cents: int,
        currency: str = "usd",
    ) -> CheckoutSession:
        return CheckoutSession(
            id=f"stub_{user_id}_{plan}_{amount_cents}",
            status="open",
            url=None,
        )

    def is_paid(self, session_id: str) -> bool:
        return False

    def handle_webhook(self, payload: bytes, signature: str | None = None) -> dict:
        return {"received": True, "gateway": self.name, "signature_ok": signature is not None}


# ---------------------------------------------------------------------------
# Paddle (Merchant of Record)
#
# Paddle is the recommended provider for a US launch because it acts as the
# Merchant of Record: Paddle is the seller of record, so global sales tax /
# VAT / GST collection and remittance are handled by Paddle, not by FluxSwarm.
# That collapses most cross-border compliance work into a single contract.
#
# Wiring (env):
#   FLUXSWARM_PAYMENT_PROVIDER=paddle
#   PADDLE_VENDOR_ID=<org id from Paddle Seller>          (old checkout backend uses it)
#   PADDLE_API_KEY=<server-side auth token>               (Bearer, full access)
#   PADDLE_PRICE_<PLAN>=<paddle price id>                 (per plan, uppercase)
#   PADDLE_WEBHOOK_SECRET=<secret shown when creating the webhook>
#   FLUXSWARM_PUBLIC_BASE_URL=https://<your-domain>      (used in checkout redirects)
#
# Sandbox first: set PADDLE_API_KEY / secrets from the SANDBOX environment,
# switch the webhook URL in Sandbox to /api/payments/webhook, complete a test
# payment, then promote to the live keys. The live environment never sees the
# sandbox secret values.
# ---------------------------------------------------------------------------
_PADDLE_API = "https://api.paddle.com"
_PADDLE_SANDBOX_API = "https://sandbox-api.paddle.com"
_WEBHOOK_SIG_MAX_AGE_S = 300  # replay-protection window for Paddle-Signature (ts=...)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _paddle_api_base() -> str:
    """Read the API base and warn loudly when it looks unset so we never bill."""
    base = _env("PADDLE_API_BASE")
    if base:
        return base
    vp = _env("PADDLE_INFO_URL", "")  # old env Paddle exposes "sandbox"/"live" URLs
    if "sandbox" in vp:
        return _PADDLE_SANDBOX_API
    return _PADDLE_API


def _paddle_price_for(plan: str) -> str:
    pid = _env(f"PADDLE_PRICE_{plan.upper()}")
    if not pid:
        raise ValueError(f"PADDLE_PRICE_{plan.upper()} is not configured")
    return pid


def verify_paddle_signature(payload: bytes, sig_header: str, secret: str, *, now: float | None = None) -> bool:
    """Verify a Paddle webhook signature. Supports both signing schemes:

    1. V1 (classic webhooks): `Paddle-Signature: <base64(hmac_sha256(body))>`.
       Verifying the raw body (not the re-encoded JSON) is what makes the check
       meaningful — the exact bytes that arrived must match.
    2. V2 (transaction webhooks): `Paddle-Signature: ts=<unix>;h1=<hex hmac>`.
       `h1 = hex(hmac_sha256(secret, f"paddle-{ts};{body}"))` and the timestamp
       must be within ``_WEBHOOK_SIG_MAX_AGE_S`` (replay protection).

    Returns True only if a scheme matches. Never raises on malformed headers.
    """
    if not secret or not sig_header:
        return False
    secret_b = secret.encode("utf-8")
    body = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    sig = sig_header.strip()

    # Scheme 2: ts=N;h1=HEX
    if sig.startswith("ts=") and ";h1=" in sig:
        try:
            ts_part, h1_part = sig.split(";h1=", 1)
            ts = float(ts_part[3:])
            if now is not None and abs(now - ts) > _WEBHOOK_SIG_MAX_AGE_S:
                return False
            expected = hmac.new(secret_b, f"paddle-{int(ts)};{body.decode('utf-8', 'replace')}".encode("utf-8"),
                                hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, h1_part.strip())
        except (ValueError, AttributeError, TypeError):
            return False

    # Scheme 1: base64(hmac_sha256(body))
    try:
        expected = base64.b64encode(
            hmac.new(secret_b, body, hashlib.sha256).digest()
        ).decode("ascii")
        return hmac.compare_digest(expected, sig)
    except (ValueError, TypeError):
        return False


class PaddleGateway(PaymentGateway):
    """Paddle Merchant-of-Record via the server-side Checkout API.

    ``create_checkout`` opens a Paddle transaction and returns the hosted
    checkout URL. Payment completion arrives as an authenticated webhook (the
    client redirecting back is NOT proof of payment — only a verified
    ``transaction.completed`` webhook may grant credits).
    """

    name = "paddle"

    def __init__(self) -> None:
        self._api_key = _env("PADDLE_API_KEY")
        # In sandbox mode a deterministic secret is used so the local signing
        # helper and the verifier agree without any Paddle account.
        self._secret = _env("PADDLE_WEBHOOK_SECRET") or ("mock-secret" if self._mock_enabled() else "")

    @property
    def configured(self) -> bool:
        """A paddle gateway is only operative with a server secret + webhook key."""
        return bool(self._api_key and self._secret)

    @property
    def operative(self) -> bool:
        """True when this gateway can actually process the sandbox OR live paths:
        real credentials configured, or the explicit local sandbox is on. Used
        by API routes to decide whether a checkout may be opened."""
        return self.configured or self._use_mock()

    def create_checkout(
        self,
        *,
        plan: str,
        user_id: int,
        amount_cents: int,
        currency: str = "usd",
    ) -> CheckoutSession:
        if self._use_mock():
            # Fully local sandbox: the checkout URL is a page on this same
            # backend that auto-"pays" by minting a *properly signed* Paddle
            # webhook and running it through the SAME code path a real
            # transaction.completed takes. No Paddle account needed.
            return CheckoutSession(
                id=f"mock_{user_id}_{plan}_{int(time.time())}",
                status="open",
                url=_env("FLUXSWARM_PUBLIC_BASE_URL", "http://127.0.0.1:8787")
                + f"/mock-checkout/{user_id}/{plan}",
            )
        if not self._api_key:
            raise RuntimeError("PADDLE_API_KEY is not configured — cannot open a real checkout")
        price_id = _paddle_price_for(plan)
        base = _paddle_api_base()
        return_url = _env("FLUXSWARM_PUBLIC_BASE_URL", "http://127.0.0.1:8787")
        body = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "custom_data": {"user_id": str(user_id), "plan": plan},
            "settings": {
                "redirect_url": f"{return_url}/?checkout=complete",
                "success_url": f"{return_url}/?checkout=complete",
            },
        }
        req = urllib.request.Request(
            f"{base}/transactions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Paddle-Version": "1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Paddle checkout failed: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}") from e
        d = data.get("data") or {}
        txn = d.get("id") or ""
        if not txn:
            raise RuntimeError("Paddle API returned no transaction id")
        # We serve the checkout page ourselves: Paddle composes its own
        # `checkout.url` from the org's default payment link (which needs the
        # org's own domain + Paddle.js). We control the URL instead by pointing
        # at our /checkout page, which opens the Paddle.js overlay for this
        # transaction via the `_ptxn` parameter.
        public = _env("FLUXSWARM_PUBLIC_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
        url = f"{public}/checkout?_ptxn={txn}"
        return CheckoutSession(id=txn, status="open", url=url)

    def is_paid(self, session_id: str) -> bool:
        # Secure payment confirmation comes exclusively via verified webhooks;
        # polling the API from the app is avoided (credits must never be granted
        # from an unauthenticated client poll).
        return False

    def handle_webhook(self, payload: bytes, signature: str | None = None) -> dict:
        """Verify + normalize a Paddle webhook into a receipt dict.

        Returns ``{"ok": False, "reason": ...}`` when verification fails and the
        caller must return 4xx (no state change). On success returns:
            gateway, event, event_id, transaction_id, user_id, plan,
            amount_cents, currency, status, idempotency_key (= event_id)
        """
        if not (self.configured or self._use_mock()):
            return {"ok": False, "reason": "paddle_not_configured"}
        if not self._verify_signature(payload, signature):
            return {"ok": False, "reason": "bad_signature"}
        data = _parse_json(payload)
        if not isinstance(data, dict):
            return {"ok": False, "reason": "invalid_json"}
        event_type = str(data.get("event_type") or "")
        event_data = data.get("data") or {}
        obj = event_data if isinstance(event_data, dict) else {}
        txn_id = str(obj.get("transaction_id") or obj.get("id") or "")
        status = str(obj.get("status") or "")
        meta = data.get("metadata") or {}
        custom = obj.get("custom_data") or meta or {}
        user_id = _coerce_int(custom.get("user_id") if isinstance(custom, dict) else None)
        plan = str(custom.get("plan") or "") if isinstance(custom, dict) else ""
        if not user_id:
            # Fallback attribution: transactions paid outside our app (e.g. a
            # dashboard test payment) still map to a user via their email.
            user_id = _resolve_user_id_from_email(
                (obj.get("customer") or {}).get("email") or "")
        if not plan:
            # Fallback plan: match the paid price against our configured prices.
            plan = _resolve_plan_from_price(_first_price_id(obj))
        amount = obj.get("total") or obj.get("amount") or {}
        amount_cents = 0
        currency = "usd"
        if isinstance(amount, dict):
            amount_cents = _coerce_int(amount.get("amount"))
            currency = str(amount.get("currency") or "usd").lower()
        kind = None
        if "adjustment" in event_type.lower():
            if str(obj.get("type") or "").lower() in ("refund", "chargeback"):
                kind = "payment.refunded"
        elif event_type.endswith("transaction.completed") or status == "completed":
            kind = "payment.succeeded"
        elif "refund" in event_type.lower() or status == "refunded":
            kind = "payment.refunded"
        return {
            "ok": True,
            "gateway": self.name,
            "event": kind,
            "event_id": str(data.get("event_id") or ""),
            "transaction_id": txn_id,
            "user_id": user_id,
            "plan": plan,
            "amount_cents": amount_cents,
            "currency": currency,
            "status": status,
            "idempotency_key": str(data.get("event_id") or txn_id),
        }

    def _verify_signature(self, payload: bytes, signature: str | None) -> bool:
        return verify_paddle_signature(
            payload,
            signature or "",
            secret=self._secret,
            now=time.time(),
        )

    @staticmethod
    def _mock_enabled() -> bool:
        """True when the operator explicitly turns on the local sandbox path
        (FLUXSWARM_PADDLE_MOCK=1). Never defaults on — a real checkout then never
        happens by surprise."""
        return _env("FLUXSWARM_PADDLE_MOCK", "").lower() in ("1", "true", "yes")

    def _use_mock(self) -> bool:
        """Sandbox mode is disabled the moment LIVE credentials are in play:
        with a live API key + the live Paddle base URL we must never hand out a
        fake checkout (a real user would 'pay' the mock and get credits free)."""
        if not self._mock_enabled():
            return False
        base = _env("PADDLE_API_BASE", "")
        if self._api_key and base in ("", _PADDLE_API):
            return False  # live env — mock is forbidden
        return True


def _parse_json(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None


def _coerce_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _first_price_id(obj: dict) -> str:
    """Pull a price id out of a Paddle transaction object (both nesting styles)."""
    for item in obj.get("items") or []:
        if isinstance(item, dict):
            price = item.get("price")
            if isinstance(price, dict) and price.get("id"):
                return str(price["id"])
            if item.get("price_id"):
                return str(item["price_id"])
    return ""


def _env_price_map() -> dict[str, str]:
    """Map configured Paddle price ids -> plan name from PADDLE_PRICE_<PLAN>."""
    out = {}
    for plan in ("starter", "pro", "scale"):
        pid = _env(f"PADDLE_PRICE_{plan.upper()}").strip()
        if pid:
            out[pid] = plan
    return out


def _resolve_plan_from_price(price_id: str) -> str:
    """Match a paid price id against PADDLE_PRICE_<PLAN> env vars."""
    if not price_id:
        return ""
    return _env_price_map().get(price_id, "")


def _resolve_user_id_from_email(email: str) -> int:
    """Map a customer email to a FluxSwarm user (lazy import keeps module
    import-safe for environments without a DB)."""
    if not email:
        return 0
    try:
        import db

        user = db.get_user_by_email(email)
        return user["id"] if user else 0
    except Exception:
        return 0


def get_gateway() -> PaymentGateway:
    """Resolve the configured gateway.

    ``FLUXSWARM_PAYMENT_PROVIDER`` selects the implementation:
      - ``paddle``  -> PaddleGateway (requires PADDLE_API_KEY + secret to be operative)
      - anything else / unset -> StubGateway (safe default; is_paid()==False).

    The stub is returned even for ``paddle`` when keys are missing, so a broken
    billing config fails safe (402) instead of charging by accident.
    """
    provider = _env("FLUXSWARM_PAYMENT_PROVIDER", "stub").lower()
    if provider in ("paddle", "paddle_sandbox"):
        gw = PaddleGateway()
        # Local sandbox mode is explicit and still signature-verified (the mock
        # completes payments through the real webhook path), so it is operative
        # even before real Paddle keys exist.
        if gw.configured or gw._mock_enabled():
            return gw
        # Intentionally returns a stub when the provider is requested but the
        # credentials are absent — the dev 402 gate stays closed.
    return StubGateway()
