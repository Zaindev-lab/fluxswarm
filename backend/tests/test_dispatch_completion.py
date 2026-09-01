"""Gate 2 — dispatch must drive multi-wave swarms to completion.

Root cause from the Gate 1 live trace: after launch the app fired a SINGLE
non-blocking dispatcher pass, so subtasks that are only created/ready as
earlier ones finish (verifier after workers, synthesizer after the verifier)
were never dispatched again — boards stayed stuck (reviewer `ready`, builder
`todo`) forever.

FIX-A: the background dispatcher now runs the bounded multi-pass loop
(`blocking=True`) so the swarm converges. These tests pin the semantics with a
mocked Hermes CLI.
"""
from __future__ import annotations

import pytest

import hermes_client as hc_mod
import main as main_mod


class _FakeRun:
    """Fake subprocess result for `hc._run`."""

    def __init__(self, stdout="{}", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _wire_mocks(monkeypatch, state_seq):
    """Point hc._run / hc.list_tasks at canned responses.

    state_seq: list of task-state lists, one per list_tasks() call. No-op sleep
    keeps the multi-pass test instant.
    """
    calls = {"n": 0}

    def fake_run(args, board=None, capture=True, provider_keys=None):
        return _FakeRun(stdout="{}")

    def fake_list(board):
        idx = min(calls["n"], len(state_seq) - 1)
        calls["n"] += 1
        return state_seq[idx]

    monkeypatch.setattr(hc_mod, "_run", fake_run)
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)
    return calls


def test_blocking_dispatch_converges_multiwave(monkeypatch):
    """Wave 1: workers done, verifier ready, builder todo -> NOT terminal.
    Wave 2 (after a re-dispatch pass): everything done -> loop exits."""
    calls = _wire_mocks(monkeypatch, [
        [{"id": "t1", "state": "done"},
         {"id": "t2", "state": "done"},
         {"id": "t3", "state": "queued"},
         {"id": "t4", "state": "queued"}],
        [{"id": "t1", "state": "done"},
         {"id": "t2", "state": "done"},
         {"id": "t3", "state": "done"},
         {"id": "t4", "state": "done"}],
    ])
    res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True, timeout_s=600)
    assert res["terminal"] is True
    assert calls["n"] >= 2  # must have polled again after the first pass


def test_nonblocking_dispatch_single_pass_does_not_converge(monkeypatch):
    """A single non-blocking pass returns after ONE dispatch even though the
    swarm still has queued work — this is the old stuck-board behaviour: it
    never polls again, so later-ready subtasks never get dispatched."""
    calls = _wire_mocks(monkeypatch, [
        [{"id": "t1", "state": "done"}, {"id": "t2", "state": "ready"}, {"id": "t3", "state": "todo"}],
    ])
    res = hc_mod.dispatch("u1-proj", max_spawn=4, blocking=False)
    assert "terminal" not in res
    assert calls["n"] == 0  # no convergence loop ran at all (single CLI pass)


def test_bg_dispatch_uses_blocking_multipass(monkeypatch):
    """The app's background dispatcher (used by project create / demo / template
    buy) must request the converging multi-pass loop, not a single pass."""
    captured = {}

    def fake_dispatch(*args, **kwargs):
        captured.update(kwargs)
        return {"terminal": True}

    monkeypatch.setattr(main_mod.hc, "dispatch", fake_dispatch)
    main_mod._bg_dispatch("u1-proj", "pro", provider_keys=None)
    assert captured.get("blocking") is True
    # The driver ceiling is a documented, bounded constant (with early
    # no-progress stall detection) — not the old 1800s silent wait.
    assert captured.get("timeout_s") == hc_mod.DISPATCH_TIMEOUT_S
    # max_spawn comes from the user's plan
    assert captured.get("max_spawn") == main_mod.db.PLANS["pro"]["parallel"]


def test_dispatch_malformed_output_no_crash(monkeypatch):
    """A non-JSON dispatch dump must degrade to {'raw': ...} instead of raising."""
    def fake_run(args, board=None, capture=True, provider_keys=None):
        return _FakeRun(stdout="not json at all")

    def fake_list(board):
        return [{"id": "t1", "state": "done"}]

    monkeypatch.setattr(hc_mod, "_run", fake_run)
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)
    res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True)
    assert res.get("terminal") is True
    assert "raw" in res


# --------------------------------------------------------------------------- #
# Gate 4 regression — robust stall detection (worker ACTIVITY, not state only).
# --------------------------------------------------------------------------- #
def test_healthy_slow_worker_with_advancing_activity_not_stuck(monkeypatch):
    """Regression (Run-1 false positive): a healthy worker steadily producing
    heartbeats / task events — while its task states never change over several
    passes — must NOT be declared `no_progress`. The task-state-only signature
    used to flag exactly this as stuck."""
    shared = {"n": 0}
    active = [{"id": t, "state": "running"} for t in ("t1", "t2", "t3", "t4")]
    done = [{"id": f"t{i}", "state": "done"} for i in range(1, 5)]

    def fake_list(board):
        shared["n"] += 1
        # Task-states stay identical ('running') for the first 6 passes while the
        # worker is busy; only then does the swarm converge to done.
        if shared["n"] <= 6:
            return list(active)
        return list(done)

    def fake_activity(board):
        # Worker heartbeat/event activity advances every pass.
        return (shared["n"], shared["n"] * 10, shared["n"] * 10)

    monkeypatch.setattr(hc_mod, "_run", lambda *a, **k: _FakeRun(stdout="{}"))
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod, "_board_activity_sig", fake_activity)
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)

    # stall_passes=2 + min_wait_s=0 would have stalled the OLD state-only logic
    # after pass 2-3; the activity signal must keep it converging to done.
    res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True,
                          timeout_s=900, min_wait_s=0, stall_passes=2)
    assert res["outcome"] == "ok"
    assert res.get("terminal") is True
    assert "stall" not in res


def test_genuinely_stalled_worker_still_stuck(monkeypatch):
    """A worker that is BOTH state-unchanging AND activity-silent (no heartbeats,
    no task events) is genuinely stalled and must still be caught."""
    def fake_list(board):
        return [{"id": t, "state": "running"} for t in ("t1", "t2", "t3", "t4")]

    monkeypatch.setattr(hc_mod, "_run", lambda *a, **k: _FakeRun(stdout="{}"))
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod, "_board_activity_sig", lambda board: ())
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)

    res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True,
                          timeout_s=900, min_wait_s=0, stall_passes=2)
    assert res["outcome"] == "stuck"
    assert res.get("stall") is True
    assert res.get("terminal") is False


def test_pinning_unchanged_by_stall_fix(monkeypatch):
    """The stall fix must not regress runtime pinning: a stale paid key still
    cannot hijack an explicitly-selected opencode-free runtime, and the new
    activity-signal helper degrades gracefully (to ()) for an absent board."""
    assert hc_mod._resolve_runtime({"openai": "sk-fake", "opencode-free": "free"}) == (
        "nemotron-3-ultra-free", "opencode-free")
    assert hc_mod._resolve_runtime({"opencode-free": "free"}) == (
        "nemotron-3-ultra-free", "opencode-free")
    assert hc_mod._resolve_runtime(None) == (
        "nemotron-3-ultra-free", "opencode-free")
    # Not a real board => no DB => () -> the caller falls back to state-only sig.
    assert hc_mod._board_activity_sig("u-no-such-board-xyz") == ()


def test_fresh_heartbeat_worker_not_stuck(monkeypatch):
    """Regression (the real Run-1/2 false positive): a healthy long-running
    worker whose max heartbeat is RECENT — but whose task states are unchanged
    for several passes and whose activity tuple is FLAT (no new events between
    passes) — must NOT be declared `no_progress`.

    This is exactly the gap between two ~60s heartbeats: the dispatcher pool runs
    ~10-15s passes with an identical activity tuple, which previously let
    `unchanged` reach stall_passes and false-stall the worker. A fresh heartbeat
    proves the worker is alive and mid-work, so the stall must not fire; the loop
    keeps going until the swarm eventually converges to done.
    """
    shared = {"n": 0}
    active = [{"id": t, "state": "running"} for t in ("t1", "t2", "t3", "t4")]
    done = [{"id": f"t{i}", "state": "done"} for i in range(1, 5)]

    def fake_list(board):
        shared["n"] += 1
        # Task-states stay identical ('running') for the first 7 passes while the
        # worker is busy; only then does the swarm converge to done.
        if shared["n"] <= 7:
            return list(active)
        return list(done)

    def fake_activity(board):
        import time
        # Flat activity tuple AND a fresh heartbeat: identical across passes, but
        # max_hb is ~now, so the worker is demonstrably alive and NOT stale.
        now = int(time.time())
        return (now, 0, now)

    monkeypatch.setattr(hc_mod, "_run", lambda *a, **k: _FakeRun(stdout="{}"))
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod, "_board_activity_sig", fake_activity)
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)

    # stall_passes=2 + min_wait_s=0: the OLD tuple-unchanged logic would stall
    # after pass 2-3 even though the heartbeat is fresh. The freshness gate must
    # keep it converging to done.
    res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True,
                          timeout_s=900, min_wait_s=0, stall_passes=2)
    assert res["outcome"] == "ok"
    assert res.get("terminal") is True
    assert "stall" not in res


def test_stale_heartbeat_genuinely_stalled_stuck(monkeypatch):
    """A worker whose heartbeat has gone STALE (max_hb far in the past — i.e. it
    stopped producting activity) while task states stay unchanged IS genuinely
    stalled and must still be caught by the freshness gate."""
    def fake_list(board):
        return [{"id": t, "state": "running"} for t in ("t1", "t2", "t3", "t4")]

    def fake_activity(board):
        # Hardware-dead / stale: heartbeat is ancient, so max_hb is way in the past.
        return (1, 0, 1)

    monkeypatch.setattr(hc_mod, "_run", lambda *a, **k: _FakeRun(stdout="{}"))
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod, "_board_activity_sig", fake_activity)
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)

    res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True,
                          timeout_s=900, min_wait_s=0, stall_passes=2)
    assert res["outcome"] == "stuck"
    assert res.get("stall") is True
    assert res.get("terminal") is False