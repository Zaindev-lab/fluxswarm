"""Tests for the in-process rate limiter (the backend that actually runs here).

Covers the fixed sliding-window expiry (_prune) and the login-failure lockout.
"""

import importlib

import ratelimit


def _fresh_limiter():
    """Build a clean _Limiter instance (the in-process backend)."""
    lim = ratelimit._Limiter()
    # Weakref-free: just return it; state is internal.
    return lim


def test_prune_expires_old_timestamps():
    lim = _fresh_limiter()
    # A timestamp far in the past must be dropped by the cutoff.
    old = 1_000_000.0
    now = 2_000_000.0
    cutoff = now - 100.0
    assert lim._prune([old, now], cutoff) == [now]


def test_prune_keeps_recent_timestamps():
    lim = _fresh_limiter()
    ts = [1000.0, 1001.0, 1002.0]
    assert lim._prune(ts, 999.0) == ts


def test_login_lockout_after_max_failures():
    lim = _fresh_limiter()
    ip, email = "10.0.0.1", "a@b.com"
    # Below the limit -> allowed.
    for _ in range(ratelimit._LOGIN_MAX_FAILURES - 1):
        assert lim.login_allowed(ip, email) is True
        lim.record_login_failure(ip, email)
    # At the limit -> still allowed on the final check? We record one more so it
    # tips over; the NEXT check must deny.
    lim.record_login_failure(ip, email)
    assert lim.login_allowed(ip, email) is False


def test_login_clear_resets_failures():
    lim = _fresh_limiter()
    ip, email = "10.0.0.2", "c@d.com"
    for _ in range(ratelimit._LOGIN_MAX_FAILURES):
        lim.record_login_failure(ip, email)
    assert lim.login_allowed(ip, email) is False
    lim.clear_login_failures(ip, email)
    assert lim.login_allowed(ip, email) is True


def test_ip_global_cap():
    lim = _fresh_limiter()
    ip = "10.0.0.3"
    for _ in range(ratelimit._IP_MAX_REQUESTS - 1):
        assert lim.ip_allowed(ip) is True
        lim.hit_ip(ip)
    lim.hit_ip(ip)
    assert lim.ip_allowed(ip) is False


def test_register_cap_and_purchase_cap():
    lim = _fresh_limiter()
    ip = "10.0.0.4"
    for _ in range(ratelimit._REG_MAX_PER_IP):
        lim.record_registration(ip)
    assert lim.register_allowed(ip) is False

    uid = 123456
    for _ in range(ratelimit._PURCHASE_MAX):
        lim.record_purchase(uid)
    assert lim.purchase_allowed(uid) is False
