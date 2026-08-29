"""RED/GREEN tests for the per-IP request rate limit.

Bug under test: ``api_register`` / ``api_login`` call ``limiter.ip_allowed(ip)``
but never call ``limiter.hit_ip(ip)``. The memory limiter's ``ip_allowed`` only
*reads* the counter, so the per-IP 20/60s cap is never incremented and the
guard is purely decorative. We assert that recording hits actually blocks.
"""
from __future__ import annotations

import ratelimit as rl_mod


def test_ip_limit_blocks_after_threshold():
    limiter = rl_mod._Limiter()
    ip = "1.2.3.4"
    for _ in range(rl_mod._IP_MAX_REQUESTS):
        assert limiter.ip_allowed(ip) is True
        limiter.hit_ip(ip)
    # The window is now exhausted -> further requests must be refused.
    assert limiter.ip_allowed(ip) is False


def test_ip_counter_prunes_after_window():
    limiter = rl_mod._Limiter()
    ip = "9.9.9.9"
    for _ in range(rl_mod._IP_MAX_REQUESTS):
        limiter.hit_ip(ip)
    assert limiter.ip_allowed(ip) is False
    # Simulate passage of time beyond the window.
    import time

    limiter._ip_hits[ip] = [t - (rl_mod._IP_WINDOW + 1) for t in limiter._ip_hits[ip]]
    assert limiter.ip_allowed(ip) is True
