"""Gate 2 — concurrent credit debit must never overspend.

Credits gate each Launch (1 credit = 1 launch). A burst of N parallel launches
must debit exactly N credits; a burst larger than the balance must stop at zero
credit spent, never going negative, never double-spending under racing threads.
"""
from __future__ import annotations

import threading

import pytest

import db as db_mod


def _force_credits(uid: int, amount: int):
    c = db_mod._conn()
    c.execute("UPDATE users SET credits=? WHERE id=?", (amount, uid))
    c.commit()
    c.close()


def _race_deduct(uid: int, threads: int) -> list[bool]:
    results = [None] * threads

    def worker(i):
        results[i] = db_mod.deduct_credit(uid)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return results


def test_concurrent_launch_debits_exactly_once_each():
    u = db_mod.create_user("race-10@fluxswarm.test", "R10", "s3cure-Pass-123")
    _force_credits(u["id"], 10)

    results = _race_deduct(u["id"], 10)
    assert sum(results) == 10  # all ten launches got a credit
    assert db_mod.get_user_credits(u["id"]) == 0  # never negative, exactly spent


def test_concurrent_launch_never_overspends_balance():
    u = db_mod.create_user("race-3@fluxswarm.test", "R3", "s3cure-Pass-123")
    _force_credits(u["id"], 3)

    results = _race_deduct(u["id"], 10)  # 10 threads race for 3 credits
    assert sum(results) == 3  # only three succeed
    assert all(r in (True, False) for r in results)
    assert db_mod.get_user_credits(u["id"]) == 0  # floor reached, never -1


def test_credit_api_path_rejects_at_zero(monkeypatch):
    """The route's own guard: a user with zero credits cannot even reach the
    launch — the debit is atomic and returns 402 without creating a board."""
    import main as main_mod

    class _PermissiveLimiter:
        def ip_allowed(self, ip): return True
        def hit_ip(self, ip): return None
        def register_allowed(self, ip): return True
        def record_registration(self, ip): return None
        def login_allowed(self, ip, email): return True
        def record_login_failure(self, ip, email): return None
        def clear_login_failures(self, ip, email): return None
        def purchase_allowed(self, uid): return True
        def record_purchase(self, uid): return None

    monkeypatch.setattr(main_mod, "limiter", _PermissiveLimiter())

    from fastapi.testclient import TestClient
    client = TestClient(main_mod.app)
    r = client.post("/api/auth/register",
                    json={"email": "nocred@fluxswarm.test", "name": "T",
                          "password": "s3cure-Pass-123", "tos_accept": True})
    assert r.status_code == 200, r.text
    uid = r.json()["user"]["id"]
    token = r.json()["token"]
    _force_credits(uid, 0)

    resp = client.post("/api/projects",
                       headers={"Authorization": f"Bearer {token}"},
                       json={"name": "X", "goal": "Build a notes API"})
    assert resp.status_code == 402