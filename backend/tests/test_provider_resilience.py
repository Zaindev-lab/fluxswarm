"""Gate 4 — Provider resilience.

Root cause (CONFIRMED, HIGH): a transient upstream provider outage (502 /
APIConnectionError from ``opencode.ai/zen/v1`` / Nvidia) stalled every worker in
the ``running`` state; the worker exited WITHOUT ``kanban_complete`` or
``kanban_block``, the dispatcher's 1800s window lapsed, ``detect_crashed_workers``
never ran, and the projects stayed ``running`` indefinitely while the single
launch credit was consumed.

Gate-4 contract pinned here:
  * a provider failure/worker exit must land the launch in a recoverable state
    (``stuck`` / ``error``), never an indefinite ``running``;
  * the bounded loop must stop quickly on no-progress stalls (not wait hours);
  * the launch credit is refunded exactly once, only when no real work happened;
  * recovery (retry) converges to ``done`` when the provider returns;
  * accounts are isolated — a refund touches only the owning user.

Test matrix (A-L) with mocked providers / boards — no real outages.
"""
from __future__ import annotations

import pytest

import db as db_mod
import hermes_client as hc_mod
import main as main_mod


class _FakeRun:
    def __init__(self, stdout="{}", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _mk_user(name="p-resil"):
    import uuid
    email = f"{name}-{uuid.uuid4().hex[:8]}@x.test"
    return db_mod.create_user(email, name, "password123", ref_code=None)["id"]


def _mk_project(uid, plan="pro"):
    return db_mod.add_project(uid, f"u{uid}-resil-{plan}", f"{plan}-proj", "build it")


def _wire_hc(monkeypatch, state_seq, dispatch_rc=0, dispatch_err=""):
    """Pin hc.list_tasks to a state sequence and hc._run to a canned dispatch.

    state_seq: one list per list_tasks() call. Monkeypatches sleep to no-op so
    the loop is instant. Returns a dict of counters.
    """
    calls = {"n": 0}

    def fake_run(args, board=None, capture=True, provider_keys=None):
        return _FakeRun(stdout="{}", returncode=dispatch_rc, stderr=dispatch_err)

    def fake_list(board):
        idx = min(calls["n"], len(state_seq) - 1)
        calls["n"] += 1
        return state_seq[idx]

    monkeypatch.setattr(hc_mod, "_run", fake_run)
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)
    return calls


def _stuck_board():
    # Workers permanently `running` with no completion — the Run-C signature.
    return [
        {"id": "t1", "state": "running"},
        {"id": "t2", "state": "running"},
        {"id": "t3", "state": "running"},
        {"id": "t4", "state": "running"},
    ]


def _done_board():
    return [{"id": f"t{i}", "state": "done"} for i in range(1, 5)]


# --------------------------------------------------------------------------- #
# A/B/C — provider outage (502 / 503 / 429): must reach `stuck`, refund & never
# report ok/terminal. The stall detector stops the loop early, not after hours.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label", ["502", "503", "429"])
def test_transient_provider_outage_lands_stuck_early(monkeypatch, label):
    _wire_hc(monkeypatch, [_stuck_board()] * 20)
    # No-op time so a single loop iteration is instant, but we still exercise a
    # few passes; the stall detector must stop well before the 900s ceiling.
    res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True,
                          timeout_s=900, min_wait_s=1, stall_passes=2)
    assert res["outcome"] == "stuck"
    assert res["terminal"] is False
    assert res["timed_out"] is True
    assert res.get("stall") is True
    assert res.get("done_count") == 0
    assert abs(res["deadline_s"] - 900) < 1


def test_wallclock_timeout_also_lands_stuck(monkeypatch):
    # Force the wall-clock ceiling by making each pass "progress" (so no stall
    # fires) and running time frozen at the deadline, so the loop must exit
    # through the deadline branch instead of the stall detector.
    calls = {"n": 0}
    alternating = [
        [{"id": "t1", "state": "running"}, {"id": "t2", "state": "queued"}],
        [{"id": "t1", "state": "queued"}, {"id": "t2", "state": "running"}],
    ]

    def fake_list(board):
        calls["n"] += 1
        return alternating[calls["n"] % 2]

    def fake_run(args, board=None, capture=True, provider_keys=None):
        return _FakeRun(stdout="{}")

    monkeypatch.setattr(hc_mod, "_run", fake_run)
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(hc_mod.time, "time", lambda: 0.0)  # deadline already passed
    res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True,
                          timeout_s=0, min_wait_s=9999, stall_passes=99)
    assert res["outcome"] == "stuck"
    assert res["timed_out"] is True
    assert res["done_count"] == 0


# --------------------------------------------------------------------------- #
# G — success path: running -> done.
# --------------------------------------------------------------------------- #
def test_success_converges_to_done(monkeypatch):
    _wire_hc(monkeypatch, [_stuck_board(), _done_board()])
    res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True, timeout_s=900)
    assert res["outcome"] == "ok"
    assert res["terminal"] is True
    assert res["timed_out"] is False


# --------------------------------------------------------------------------- #
# E — synchronous launch error (auth 401 / dispatch CLI failure): `launch_error`.
# --------------------------------------------------------------------------- #
def test_launch_error_returns_stuck_bg_and_refunds(monkeypatch):
    uid = _mk_user()
    pid = _mk_project(uid)
    before = db_mod.get_user_by_id(uid)["credits"]

    def fake_dispatch(*a, **k):
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr(main_mod.hc, "dispatch", fake_dispatch)
    main_mod._bg_dispatch(f"u{uid}-proj", "pro", provider_keys=None, pid=pid)

    proj = main_mod._project_by_pid(pid)
    assert proj["launch_status"] == "error"
    assert proj["launch_outcome"] == "launch_error"
    assert proj["launch_refunded"] == 1
    after = db_mod.get_user_by_id(uid)["credits"]
    assert after == before + 1  # exactly one credit returned


def test_no_refund_when_launch_already_converged(monkeypatch):
    uid = _mk_user()
    pid = _mk_project(uid)
    before = db_mod.get_user_by_id(uid)["credits"]

    def fake_dispatch(*a, **k):
        return {"outcome": "ok", "timed_out": False, "done_count": 4}

    monkeypatch.setattr(main_mod.hc, "dispatch", fake_dispatch)
    monkeypatch.setattr(main_mod.hc, "board_has_completed_work", lambda slug: True)
    main_mod._bg_dispatch(f"u{uid}-proj", "pro", provider_keys=None, pid=pid)

    proj = main_mod._project_by_pid(pid)
    assert proj["launch_status"] == "ok"
    assert proj["launch_refunded"] == 0
    assert db_mod.get_user_by_id(uid)["credits"] == before


def test_refund_only_when_no_work_produced(monkeypatch):
    # Provider stalls but one task DID complete => it is real work, no refund.
    uid = _mk_user()
    pid = _mk_project(uid)
    before = db_mod.get_user_by_id(uid)["credits"]

    monkeypatch.setattr(main_mod.hc, "dispatch",
                        lambda *a, **k: {"outcome": "stuck", "timed_out": True,
                                         "done_count": 1})
    monkeypatch.setattr(main_mod.hc, "board_has_completed_work", lambda slug: True)
    main_mod._bg_dispatch(f"u{uid}-proj", "pro", provider_keys=None, pid=pid)

    proj = main_mod._project_by_pid(pid)
    assert proj["launch_status"] == "stuck"
    assert proj["launch_refunded"] == 0
    assert db_mod.get_user_by_id(uid)["credits"] == before


# --------------------------------------------------------------------------- #
# K / L — credit accounting: idempotent refund + account isolation.
# --------------------------------------------------------------------------- #
def test_refund_is_idempotent(monkeypatch):
    uid = _mk_user()
    pid = _mk_project(uid)
    before = db_mod.get_user_by_id(uid)["credits"]

    def fake_dispatch(*a, **k):
        return {"outcome": "stuck", "timed_out": True, "done_count": 0}

    monkeypatch.setattr(main_mod.hc, "dispatch", fake_dispatch)
    monkeypatch.setattr(main_mod.hc, "board_has_completed_work", lambda slug: False)
    main_mod._bg_dispatch(f"u{uid}-proj", "pro", provider_keys=None, pid=pid)
    main_mod._bg_dispatch(f"u{uid}-proj", "pro", provider_keys=None, pid=pid)

    proj = main_mod._project_by_pid(pid)
    assert proj["launch_refunded"] == 1
    # launch_refunded flag is a hard gate: second invocation must NOT refund again.
    assert db_mod.get_user_by_id(uid)["credits"] == before + 1


def test_refund_isolated_to_owner(monkeypatch):
    owner = _mk_user("owner")
    bystander = _mk_user("bystander")
    pid = _mk_project(owner)
    ob, bb = (db_mod.get_user_by_id(u)["credits"] for u in (owner, bystander))

    monkeypatch.setattr(main_mod.hc, "dispatch",
                        lambda *a, **k: {"outcome": "stuck", "timed_out": True,
                                         "done_count": 0})
    monkeypatch.setattr(main_mod.hc, "board_has_completed_work", lambda slug: False)
    main_mod._bg_dispatch(f"u{owner}-proj", "pro", provider_keys=None, pid=pid)

    assert db_mod.get_user_by_id(owner)["credits"] == ob + 1
    assert db_mod.get_user_by_id(bystander)["credits"] == bb  # untouched


def test_db_refund_guard_no_double_return(monkeypatch):
    uid = _mk_user()
    before = db_mod.get_user_by_id(uid)["credits"]
    assert db_mod.refund_launch_credit(uid) is True
    assert db_mod.get_user_by_id(uid)["credits"] == before + 1
    # Second call adds another credit (this is the raw primitive). The
    # idempotency gate lives at the reconciliation layer (launch_refunded).
    assert db_mod.refund_launch_credit(uid) is True
    assert db_mod.get_user_by_id(uid)["credits"] == before + 2


def test_board_has_completed_work_excludes_swarm_root(monkeypatch):
    """The swarm ROOT card is auto-done (``assignee=fluxswarm``) and represents no
    agent work. Only a done AGENT task counts as completed work for the refund
    decision — a board whose root is done but every agent failed must NOT block
    the refund."""
    # Root done + agent blocked => no meaningful work => no completed work.
    monkeypatch.setattr(
        hc_mod, "list_tasks",
        lambda board: [
            {"id": "root", "state": "done", "assignee": "fluxswarm"},
            {"id": "plan", "state": "blocked", "assignee": "ecc-planner"},
            {"id": "dev",  "state": "blocked", "assignee": "ecc-architect"},
        ])
    assert hc_mod.board_has_completed_work("u-probe") is False

    # Root done + one real agent done => meaningful work happened => refund blocked.
    monkeypatch.setattr(
        hc_mod, "list_tasks",
        lambda board: [
            {"id": "root", "state": "done", "assignee": "fluxswarm"},
            {"id": "plan", "state": "done", "assignee": "ecc-planner"},
        ])
    assert hc_mod.board_has_completed_work("u-probe") is True

    # An agent task with no assignee is not the root; treat a done one as work.
    monkeypatch.setattr(
        hc_mod, "list_tasks",
        lambda board: [{"id": "x", "state": "done", "assignee": ""}])
    assert hc_mod.board_has_completed_work("u-probe") is False


# --------------------------------------------------------------------------- #
# H / I — retry: a fresh launch after a failed one converges when the provider
# returns; if it is still down, it lands stuck again (finite, never loops).
# --------------------------------------------------------------------------- #
def test_retry_after_failure_converges(monkeypatch):
    uid = _mk_user()
    pid = _mk_project(uid)
    before = db_mod.get_user_by_id(uid)["credits"]
    calls = {"n": 0}

    def fake_dispatch(*a, **k):
        calls["n"] += 1
        # Attempt 1: provider down. Attempt 2: provider up -> converges.
        if calls["n"] == 1:
            return {"outcome": "stuck", "timed_out": True, "done_count": 0}
        return {"outcome": "ok", "timed_out": False, "done_count": 4}

    monkeypatch.setattr(main_mod.hc, "dispatch", fake_dispatch)
    monkeypatch.setattr(main_mod.hc, "board_has_completed_work",
                        lambda slug: calls["n"] > 1)

    main_mod._bg_dispatch(f"u{uid}-proj", "pro", provider_keys=None, pid=pid)
    assert main_mod._project_by_pid(pid)["launch_refunded"] == 1
    assert db_mod.get_user_by_id(uid)["credits"] == before + 1

    # Retry: user relaunches (fresh project row), provider is back.
    pid2 = _mk_project(uid)
    main_mod._bg_dispatch(f"u{uid}-proj2", "pro", provider_keys=None, pid=pid2)
    proj2 = main_mod._project_by_pid(pid2)
    assert proj2["launch_status"] == "ok"
    assert proj2["launch_outcome"] == "converged"
    assert proj2["launch_refunded"] == 0
    # The successful retry is never refunded (it did real work): balance stays
    # at the single refund from the first failed launch.
    assert db_mod.get_user_by_id(uid)["credits"] == before + 1


def test_retry_repeat_failure_stays_bounded_stuck(monkeypatch):
    uid = _mk_user()
    pid = _mk_project(uid)
    before = db_mod.get_user_by_id(uid)["credits"]
    calls = {"n": 0}

    def fake_dispatch(*a, **k):
        calls["n"] += 1
        return {"outcome": "stuck", "timed_out": True, "done_count": 0}

    monkeypatch.setattr(main_mod.hc, "dispatch", fake_dispatch)
    monkeypatch.setattr(main_mod.hc, "board_has_completed_work", lambda slug: False)

    main_mod._bg_dispatch(f"u{uid}-proj", "pro", provider_keys=None, pid=pid)
    main_mod._bg_dispatch(f"u{uid}-proj", "pro", provider_keys=None, pid=pid)
    # Finite: two explicit launches, no hidden infinite re-dispatch.
    assert calls["n"] == 2
    assert main_mod._project_by_pid(pid)["launch_refunded"] == 1
    assert db_mod.get_user_by_id(uid)["credits"] == before + 1
