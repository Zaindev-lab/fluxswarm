"""Health monitoring and webhook alerting (Task 4, session 3).

Standalone module: never imported at app startup by main (the reaper pulls it
in lazily), and main module is imported lazily inside the alert path so tests
that import monitoring alone never drag in the FastAPI app.

``alert_if_unhealthy``:
  - builds the same /health snapshot as ``snapshot_health()``;
  - when the snapshot says not-ok AND ``ALERT_WEBHOOK_URL`` is set, POSTs a
    Slack-compatible JSON payload (``{"text": ..., "priority": "high"}``);
  - throttles to one alert per 5 minutes so an outage doesn't retry-spam the
    webhook every reaper sweep (30s);
  - always returns the snapshot dict (with ``alert_sent`` added when it fired)
    so callers/tests get a truthy return cheaply.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

_LAST_ALERT_AT = 0.0
_ALERT_MIN_INTERVAL_S = 300


def send_webhook(url: str, payload: dict) -> bool:
    """POST *payload* as JSON to *url*. Returns True on HTTP 2xx.

    Failures (DNS, timeout, non-2xx) return False — an alerting path must
    never raise into the reaper cadence.
    """
    if not url:
        return False
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def alert_if_unhealthy() -> dict:
    """Alert the operator webhook when the /health snapshot is not ok."""
    from main import snapshot_health  # lazy: avoid import cycle at startup

    health = snapshot_health()
    webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if health.get("ok"):
        return health
    if not webhook:
        return health
    global _LAST_ALERT_AT
    now = time.time()
    if now - _LAST_ALERT_AT < _ALERT_MIN_INTERVAL_S:
        return health  # throttled; still unhealthy
    _LAST_ALERT_AT = now
    payload = {
        "text": f"FluxSwarm unhealthy: {json.dumps(health, ensure_ascii=False)[:4000]}",
        "priority": "high",
    }
    send_webhook(webhook, payload)
    return {**health, "alert_sent": True}