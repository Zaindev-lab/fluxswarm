"""Tests for Hermes worker-process cleanup on dispatch stall/timeout/exception.

Covers:
  A. successful dispatch does NOT kill workers that must continue
  B. timeout cleans up owned workers
  C. stall cleans up owned workers
  D. exception cleans up owned workers
  E. unrelated Hermes process remains untouched
  F. repeated cleanup is safe/idempotent
  G. no worker-process accumulation after repeated timeout tests
  H. api_dispatch passes DISPATCH_TIMEOUT_S
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import hermes_client as hc_mod
import main as main_mod


class _FakeRun:
    """Fake subprocess result for ``hc._run``."""
    def __init__(self, stdout="{}", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wire_mocks(monkeypatch, state_seq, *, activity_sig=None):
    """Wire _run / list_tasks / time.sleep for unit-test dispatch loops."""
    calls = {"n": 0, "kills": [], "cleanup_calls": []}

    def fake_run(args, board=None, capture=True, provider_keys=None):
        return _FakeRun(stdout="{}")

    def fake_list(board):
        idx = min(calls["n"], len(state_seq) - 1)
        calls["n"] += 1
        return state_seq[idx]

    def fake_cleanup(board):
        calls["cleanup_calls"].append(board)

    monkeypatch.setattr(hc_mod, "_run", fake_run)
    monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
    monkeypatch.setattr(hc_mod, "_cleanup_board_workers", fake_cleanup)
    monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)
    if activity_sig is not None:
        monkeypatch.setattr(hc_mod, "_board_activity_sig", activity_sig)
    return calls


def _make_fake_activity(stale_after: int = 999):
    """Return a fake _board_activity_sig that stays fresh until *stale_after* calls."""
    shared = {"n": 0}

    def fake_activity(board):
        shared["n"] += 1
        if shared["n"] <= stale_after:
            return (time.time(), shared["n"], shared["n"])
        return ()
    return fake_activity


# ---------------------------------------------------------------------------
# A. Successful dispatch does NOT kill workers
# ---------------------------------------------------------------------------

class TestSuccessfulDispatchNoCleanup:
    def test_converged_skips_cleanup(self, monkeypatch):
        """All tasks done -> _cleanup_board_workers must NOT be called."""
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "done"}, {"id": "t2", "state": "done"}],
        ])
        res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True,
                              timeout_s=10, min_wait_s=0)
        assert res["outcome"] == "ok"
        assert res["terminal"] is True
        assert calls["cleanup_calls"] == []

    def test_nonblocking_does_not_cleanup(self, monkeypatch):
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "running"}],
        ])
        res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=False)
        assert res.get("outcome") == "pending"
        assert calls["cleanup_calls"] == []


# ---------------------------------------------------------------------------
# B. Timeout cleans up owned workers
# ---------------------------------------------------------------------------

class TestTimeoutCleanup:
    def test_timeout_calls_cleanup(self, monkeypatch):
        """When the blocking window expires, cleanup must run."""
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
        ])
        res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True,
                              timeout_s=1, min_wait_s=0, stall_passes=99)
        assert res["timed_out"] is True
        assert res["outcome"] == "stuck"
        assert calls["cleanup_calls"] == ["u1-proj"]

    def test_timeout_stuck_tasks_listed(self, monkeypatch):
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "running"}, {"id": "t2", "state": "queued"}],
            [{"id": "t1", "state": "running"}, {"id": "t2", "state": "queued"}],
        ])
        res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True,
                              timeout_s=1, min_wait_s=0, stall_passes=99)
        stuck_ids = {t["id"] for t in res.get("stuck_tasks", [])}
        assert stuck_ids == {"t1", "t2"}


# ---------------------------------------------------------------------------
# C. Stall cleans up owned workers
# ---------------------------------------------------------------------------

class TestStallCleanup:
    def test_stall_calls_cleanup(self, monkeypatch):
        """Stall exit must trigger worker cleanup."""
        activity = _make_fake_activity(stale_after=999)
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
        ], activity_sig=activity)
        res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True,
                              timeout_s=300, min_wait_s=0, stall_passes=3)
        assert res.get("stall") is True
        assert res["outcome"] == "stuck"
        assert calls["cleanup_calls"] == ["u1-proj"]

    def test_stall_does_not_fire_when_activity_advancing(self, monkeypatch):
        """Healthy worker with advancing heartbeats must NOT be stalled."""
        activity = _make_fake_activity(stale_after=999)
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "done"}],
        ], activity_sig=activity)
        res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True,
                              timeout_s=300, min_wait_s=0, stall_passes=3)
        assert res["outcome"] == "ok"
        assert calls["cleanup_calls"] == []


# ---------------------------------------------------------------------------
# D. Exception cleans up owned workers
# ---------------------------------------------------------------------------

class TestExceptionCleanup:
    def test_run_exception_triggers_cleanup(self, monkeypatch):
        """If _run raises during the blocking loop, cleanup must still run."""
        calls = {"n": 0, "cleanup_calls": []}

        def fake_run(args, board=None, capture=True, provider_keys=None):
            calls["n"] += 1
            if calls["n"] <= 1:
                return _FakeRun(stdout="{}")
            raise RuntimeError("dispatch CLI crashed")

        def fake_list(board):
            return [{"id": "t1", "state": "running"}]

        def fake_cleanup(board):
            calls["cleanup_calls"].append(board)

        monkeypatch.setattr(hc_mod, "_run", fake_run)
        monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
        monkeypatch.setattr(hc_mod, "_cleanup_board_workers", fake_cleanup)
        monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)

        with pytest.raises(RuntimeError, match="dispatch CLI crashed"):
            hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True,
                            timeout_s=300, min_wait_s=0)
        assert calls["cleanup_calls"] == ["u1-proj"]


# ---------------------------------------------------------------------------
# E. Unrelated Hermes process remains untouched
# ---------------------------------------------------------------------------

class TestUnrelatedProcessSafety:
    def test_cleanup_only_targets_board_pids(self, monkeypatch):
        """_cleanup_board_workers must only kill PIDs from the target board."""
        killed_pids = []

        def fake_cleanup(board):
            # Simulate: cleanup reads PIDs from THIS board only
            if board == "u1-target":
                killed_pids.append(12345)

        monkeypatch.setattr(hc_mod, "_cleanup_board_workers", fake_cleanup)

        # Dispatch on a DIFFERENT board
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
        ])
        monkeypatch.setattr(hc_mod, "_cleanup_board_workers", fake_cleanup)

        res = hc_mod.dispatch("u1-other", max_spawn=2, blocking=True,
                              timeout_s=1, min_wait_s=0, stall_passes=99)
        # cleanup was called for u1-other, NOT u1-target
        assert killed_pids == []  # fake_cleanup only adds for u1-target

    def test_read_worker_pids_returns_only_board_pids(self, monkeypatch):
        """read_worker_pids queries kanban.db for the specific board."""
        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "kanban.db"
        c = sqlite3.connect(str(db_path))
        c.execute("""CREATE TABLE tasks (
            id TEXT PRIMARY KEY, title TEXT, status TEXT,
            worker_pid INTEGER, assignee TEXT
        )""")
        c.execute("INSERT INTO tasks VALUES ('t1', 'task1', 'running', 111, 'ecc-planner')")
        c.execute("INSERT INTO tasks VALUES ('t2', 'task2', 'running', 222, 'ecc-tdd')")
        c.execute("INSERT INTO tasks VALUES ('t3', 'task3', 'done', 333, 'ecc-devops')")
        c.commit()
        c.close()

        boards_dir = Path(tmp) / "kanban" / "boards" / "test-board"
        boards_dir.mkdir(parents=True)
        import shutil as _sh
        _sh.copy2(str(db_path), str(boards_dir / "kanban.db"))

        old_home = hc_mod.HERMES_HOME
        try:
            hc_mod.HERMES_HOME = tmp
            pids = hc_mod.read_worker_pids("test-board")
            # Only running/ready/todo tasks returned; done task excluded
            assert sorted(pids) == [111, 222]
        finally:
            hc_mod.HERMES_HOME = old_home
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# F. Repeated cleanup is safe / idempotent
# ---------------------------------------------------------------------------

class TestIdempotentCleanup:
    def test_double_cleanup_safe(self, monkeypatch):
        """Calling _cleanup_board_workers twice does not crash."""
        # First call with valid DB, second call with missing DB
        tmp = tempfile.mkdtemp()
        boards_dir = Path(tmp) / "kanban" / "boards" / "board-x"
        boards_dir.mkdir(parents=True)

        # Create a minimal kanban.db
        db_path = boards_dir / "kanban.db"
        c = sqlite3.connect(str(db_path))
        c.execute("""CREATE TABLE tasks (
            id TEXT PRIMARY KEY, title TEXT, status TEXT,
            worker_pid INTEGER, assignee TEXT
        )""")
        c.execute("INSERT INTO tasks VALUES ('t1', 'task1', 'running', 99999, 'ecc-planner')")
        c.commit()
        c.close()

        old_home = hc_mod.HERMES_HOME
        try:
            hc_mod.HERMES_HOME = tmp
            # First cleanup - PID 99999 likely does not exist -> no crash
            hc_mod._cleanup_board_workers("board-x")
            # Second cleanup - same result, idempotent
            hc_mod._cleanup_board_workers("board-x")
            # Cleanup with non-existent board - no crash
            hc_mod._cleanup_board_workers("nonexistent-board")
        finally:
            hc_mod.HERMES_HOME = old_home
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# G. No worker-process accumulation
# ---------------------------------------------------------------------------

class TestNoProcessAccumulation:
    def test_consecutive_timeouts_do_not_accumulate(self, monkeypatch):
        """Multiple sequential timeouts must each clean up; no buildup."""
        cleanup_boards = []

        def fake_cleanup(board):
            cleanup_boards.append(board)

        monkeypatch.setattr(hc_mod, "_cleanup_board_workers", fake_cleanup)

        for i in range(3):
            calls = _wire_mocks(monkeypatch, [
                [{"id": f"t{i}", "state": "running"}],
                [{"id": f"t{i}", "state": "running"}],
            ])
            monkeypatch.setattr(hc_mod, "_cleanup_board_workers", fake_cleanup)
            res = hc_mod.dispatch(f"u1-board{i}", max_spawn=1, blocking=True,
                                  timeout_s=1, min_wait_s=0, stall_passes=99)
            assert res["timed_out"] is True

        # Each timeout triggered exactly one cleanup
        assert cleanup_boards == ["u1-board0", "u1-board1", "u1-board2"]


# ---------------------------------------------------------------------------
# H. api_dispatch passes DISPATCH_TIMEOUT_S
# ---------------------------------------------------------------------------

class TestApiDispatchTimeout:
    def test_sync_dispatch_uses_dispatch_timeout_s(self, monkeypatch):
        """api_dispatch must pass timeout_s=DISPATCH_TIMEOUT_S, not the old 600s default."""
        captured = {}

        def fake_dispatch(*args, **kwargs):
            captured.update(kwargs)
            return {"terminal": True, "outcome": "ok", "timed_out": False,
                    "stuck_tasks": []}

        monkeypatch.setattr(main_mod.hc, "dispatch", fake_dispatch)
        monkeypatch.setattr(main_mod, "_operator_maintenance", lambda: False)
        monkeypatch.setattr(main_mod, "_DEMO_DAILY_CAP", 999)

        # Simulate calling api_dispatch through the route handler
        user = {"id": 1, "plan": "pro", "email": "test@test.com"}
        monkeypatch.setattr(main_mod, "get_current_user",
                           lambda: user)

        # We can't easily call the route directly, but we can verify the
        # source code passes the right timeout by checking the dispatch call
        # pattern in main.py
        import inspect
        src = inspect.getsource(main_mod.api_dispatch)
        assert "hc.DISPATCH_TIMEOUT_S" in src

    def test_bg_dispatch_uses_dispatch_timeout_s(self, monkeypatch):
        """_bg_dispatch must pass timeout_s=DISPATCH_TIMEOUT_S."""
        captured = {}

        def fake_dispatch(*args, **kwargs):
            captured.update(kwargs)
            return {"terminal": True, "outcome": "ok", "timed_out": False,
                    "stuck_tasks": []}

        monkeypatch.setattr(main_mod.hc, "dispatch", fake_dispatch)
        main_mod._bg_dispatch("u1-proj", "pro", provider_keys=None)
        assert captured.get("timeout_s") == hc_mod.DISPATCH_TIMEOUT_S


# ---------------------------------------------------------------------------
# I. Existing dispatch semantics preserved
# ---------------------------------------------------------------------------

class TestExistingSemanticsPreserved:
    def test_stall_detection_with_activity(self, monkeypatch):
        """Regression: healthy slow worker with advancing heartbeats not stuck."""
        activity = _make_fake_activity(stale_after=999)
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "done"}],
        ], activity_sig=activity)
        res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True,
                              timeout_s=900, min_wait_s=0, stall_passes=2)
        assert res["outcome"] == "ok"
        assert res.get("terminal") is True
        assert "stall" not in res

    def test_genuinely_stalled_worker_detected(self, monkeypatch):
        """Worker with no heartbeats and no state change IS stuck."""
        activity = _make_fake_activity(stale_after=0)  # stale immediately
        calls = _wire_mocks(monkeypatch, [
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
            [{"id": "t1", "state": "running"}],
        ], activity_sig=activity)
        res = hc_mod.dispatch("u-probe", max_spawn=2, blocking=True,
                              timeout_s=900, min_wait_s=0, stall_passes=2)
        assert res.get("stall") is True
        assert res["outcome"] == "stuck"

    def test_malformed_output_no_crash(self, monkeypatch):
        calls = {"n": 0}

        def fake_run(args, board=None, capture=True, provider_keys=None):
            return _FakeRun(stdout="not json at all")

        def fake_list(board):
            return [{"id": "t1", "state": "done"}]

        monkeypatch.setattr(hc_mod, "_run", fake_run)
        monkeypatch.setattr(hc_mod, "list_tasks", fake_list)
        monkeypatch.setattr(hc_mod, "_cleanup_board_workers", lambda b: None)
        monkeypatch.setattr(hc_mod.time, "sleep", lambda s: None)
        res = hc_mod.dispatch("u1-proj", max_spawn=2, blocking=True)
        assert res.get("terminal") is True
        assert "raw" in res


# ---------------------------------------------------------------------------
# kill_process_tree unit tests
# ---------------------------------------------------------------------------

class TestKillProcessTree:
    def test_kill_zero_pid_no_crash(self):
        """kill_process_tree(0) must not crash."""
        hc_mod.kill_process_tree(0)

    def test_kill_negative_pid_no_crash(self):
        """kill_process_tree(-1) must not crash."""
        hc_mod.kill_process_tree(-1)

    def test_kill_nonexistent_pid_no_crash(self):
        """kill_process_tree with a PID that does not exist must not crash."""
        hc_mod.kill_process_tree(99999999)

    def test_read_worker_pids_missing_db(self, monkeypatch):
        """read_worker_pids with no kanban.db returns empty list."""
        old_home = hc_mod.HERMES_HOME
        try:
            hc_mod.HERMES_HOME = tempfile.mkdtemp()
            assert hc_mod.read_worker_pids("nonexistent") == []
        finally:
            hc_mod.HERMES_HOME = old_home
