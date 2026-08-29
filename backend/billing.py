"""Billing gateway accessor used by main.api_subscribe.

Thin compatibility shim over the canonical ``payments`` module (which holds the
real PaymentGateway interface + StubGateway). ``main`` imports this as
``billing_mod`` and calls ``get_gateway()`` (no args). Keeping the seam here lets
the API layer stay decoupled from the concrete provider package.

The dev 402 gate (FLUXSWARM_PAYMENTS) is enforced in main.py; this accessor just
resolves the gateway object so the wiring is exercised even when the gate is closed.
"""
from __future__ import annotations

from payments import CheckoutSession, PaymentGateway, StubGateway, get_gateway

__all__ = ["CheckoutSession", "PaymentGateway", "StubGateway", "get_gateway"]
