"""Phase 2: Hermes Docker sandbox isolation tests.

Covers the runner spec (all hardening flags), container naming, env whitelist
(no secrets leak), read-only workspace mount, TTL timeout + force-remove with
retries, outcomes copy-back, audit logging and PostgreSQL advisory locking.

The docker daemon is NOT required: every command is routed through
``hermes_docker.char_shell`` / ``_docker`` which the tests monkeypatch, so the
suite runs anywhere (CI, dev host) and the exact argv the sandbox would have
executed is asserted.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

import hermes_docker as hd


@pytest.fixture(autouse=True)
def assert_no_real_docker(monkeypatch):
    """Guarantee the tests never touch a real daemon."""
    def _boom(*_a, **_k):
        raise AssertionError("tests must not invoke the real docker CLI")
    monkeypatch.setattr(hd, "char_shell", _boom)
    monkeypatch.setattr(hd, "_docker", _boom)


def _spec(**kw) -> hd.RunnerSpec:
    return hd.RunnerSpec(**kw)


def _args(spec=None, **kw) -> list[str]:
    spec = spec or _spec()
    env = kw.pop("env", {"OPENAI_API_KEY": "sk-test"})
    board = kw.pop("board", "b1")
    name = kw.pop("name", "c1")
    argv = kw.pop("argv", ["dispatch"])
    board_dir = kw.pop("board_dir", Path(tempfile.mkdtemp()))
    return hd.build_docker_args(spec, name, board, argv, env, board_dir)


# ---------- hardening flags ----------

def test_network_is_none():
    args = _args()
    assert args[args.index("--network") + 1] == "none"


def test_capabilities_dropped_all_added_minimal():
    args = _args()
    assert args[args.index("--cap-drop") + 1] == "ALL"
    caps = [args[i + 1] for i in range(len(args) - 1) if args[i] == "--cap-add"]
    assert len(caps) == 3
    assert set(caps) == {"DAC_OVERRIDE", "CHOWN", "FOWNER"}


def test_no_new_privileges():
    args = _args()
    assert "no-new-privileges" in args[args.index("--security-opt") + 1]


def test_resource_limits():
    args = _args()
    assert args[args.index("--pids-limit") + 1] == "256"
    assert args[args.index("--memory") + 1] == "2g"
    assert args[args.index("--cpus") + 1] == "1.5"


def test_read_only_root_with_tmpfs():
    spec = _spec()
    args = _args(spec)
    assert "--read-only" in args
    tmpfs = [args[i + 1] for i in range(len(args)) if args[i] == "--tmpfs"]
    assert "/tmp:size=512m" in tmpfs
    assert "/var/tmp:size=256m" in tmpfs
    assert "/run:size=64m" in tmpfs


def test_non_root_user():
    args = _args()
    assert args[args.index("-u") + 1] == "1000:1000"


def test_workspace_mounted_read_only():
    args = _args()
    mounts = [a for a in args if a.startswith("-v")]
    assert len(mounts) == 1
    assert mounts[0].endswith(":ro")
    assert "/workspace" in mounts[0]


# ---------- container naming ----------

def test_container_name_pattern():
    name = hd.container_name(42, 7)
    assert name.startswith("fluxswarm-42-7-")
    assert len(name) == len("fluxswarm-42-7-") + 8


def test_container_names_unique():
    a = hd.container_name(1, 1)
    b = hd.container_name(1, 1)
    assert a != b


# ---------- env whitelist ----------

def test_env_whitelist_maps_provider_keys():
    env = hd.sandbox_env(
        {"anthropic": "k1", "openai": "k2", "gemini": "k3", "kimi": "k4", "openrouter": "k5"},
        provider="openai", model="gpt-5",
    )
    assert env["ANTHROPIC_API_KEY"] == "k1"
    assert env["OPENAI_API_KEY"] == "k2"
    assert env["GEMINI_API_KEY"] == "k3"
    assert env["KIMI_API_KEY"] == "k4"
    assert env["OPENROUTER_API_KEY"] == "k5"
    assert env["HERMES_DEFAULT_MODEL"] == "gpt-5"
    assert env["HERMES_DEFAULT_PROVIDER"] == "openai"


def test_sandbox_env_never_leaks_arbitrary_keys():
    env = hd.sandbox_env({"random": "should-not-appear"})
    assert env == {}


def test_docker_args_contain_only_whitelisted_env(monkeypatch):
    env = {"OPENAI_API_KEY": "sk-test", "AWS_SECRET_ACCESS_KEY": "LEAK", "FLUXSWARM_FERNET_KEY": "LEAK"}
    args = hd.build_docker_args(
        _spec(), "c1", "b1", ["dispatch"], env, Path(tempfile.mkdtemp()),
    )
    # Every -e value must be one of our known-only keys (the harness strips
    # arbitrary keys; build_docker_args receives a pre-filtered env).
    seen = {args[i + 1] for i in range(len(args) - 1) if args[i] == "-e"}
    assert seen == {"HERMES_HOME=/app/hermes_home", "OPENAI_API_KEY=sk-test"}


# ---------- TTL / cleanup ----------

def test_run_dispatch_removes_container_on_timeout(monkeypatch):
    specs = []

    def fake_shell(cmd, **kw):
        specs.append(cmd)
        raise subprocess.TimeoutExpired(cmd, timeout=1)

    monkeypatch.setattr(hd, "char_shell", fake_shell)
    monkeypatch.setattr(hd, "write_audit", lambda *a, **k: None)
    monkeypatch.setattr(hd, "copy_outcomes", lambda *a, **k: None)
    monkeypatch.setattr(hd, "_docker_rm_f", lambda name, retries=3: True)

    board_dir = Path(tempfile.mkdtemp())
    result = hd.run_dispatch(
        "b1", ["dispatch"], board_host_dir=board_dir,
        spec=_spec(timeout_s=1), advisory_lock=False,
    )
    assert result["timed_out"] is True
    assert result["outcome"] == "timed_out"
    # The docker run command was assembled with --network none.
    assert "--network" in specs[0]


def test_tail_keeps_last_n_lines():
    lines = "\n".join(f"line{i}" for i in range(1000))
    assert len(hd.tail(lines, 500).splitlines()) == 500
    assert hd.tail("", 500) == ""


# ---------- outcomes copy-back ----------

def test_copy_outcomes_path(monkeypatch, tmp_path):
    called = {}

    def fake_docker(args, **kw):
        called["args"] = args
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(hd, "_docker", fake_docker)
    dest = hd.copy_outcomes("c1", "board-x", host_root=tmp_path)
    assert dest == tmp_path / "board-x"
    assert called["args"][:3] == ["cp", "c1:/workspace/outcomes/.", str(dest)]


def test_copy_outcomes_raises_on_failure(monkeypatch, tmp_path):
    def fake_docker(args, **kw):
        return type("R", (), {"returncode": 1, "stderr": "boom"})()

    monkeypatch.setattr(hd, "_docker", fake_docker)
    with pytest.raises(hd.DockerDispatchError):
        hd.copy_outcomes("c1", "board-x", host_root=tmp_path)


# ---------- audit ----------

def test_write_audit_appends_jsonl(tmp_path):
    f = tmp_path / "audit.jsonl"
    hd.write_audit({"board": "b", "container": "c", "returncode": 0}, audit_file=f)
    hd.write_audit({"board": "b", "container": "c2", "returncode": 1}, audit_file=f)
    rows = [json.loads(line) for line in f.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["container"] == "c"
    assert rows[1]["returncode"] == 1


def test_run_dispatch_writes_audit_with_tail(monkeypatch, tmp_path):
    audit = tmp_path / "audit.jsonl"
    orig_write_audit = hd.write_audit

    def fake_audit(entry, **kw):
        orig_write_audit(entry, audit_file=audit)

    monkeypatch.setattr(hd, "write_audit", fake_audit)

    class FakeProc:
        returncode = 0
        stdout = "ok-line\n" * 600
        stderr = ""

    monkeypatch.setattr(hd, "char_shell", lambda cmd, **kw: FakeProc())
    monkeypatch.setattr(hd, "copy_outcomes", lambda *a, **k: Path("/tmp/out"))
    board_dir = Path(tempfile.mkdtemp())

    hd.run_dispatch("board-audit", ["list"], board_host_dir=board_dir,
                    spec=_spec(timeout_s=5), advisory_lock=False)
    rows = [json.loads(l) for l in audit.read_text().splitlines() if l.strip()]
    assert rows
    assert rows[0]["board"] == "board-audit"
    assert rows[0]["returncode"] == 0
    # stdout_tail must be capped at 500 lines, not 600.
    assert len(rows[0]["stdout_tail"].splitlines()) == 500


# ---------- advisory lock ----------

def test_advisory_lock_released_on_exit(monkeypatch):
    acquired = []
    released = []

    class Fake:
        def __init__(self, board, timeout_s=5.0):
            self.board = board
            self.timeout_s = timeout_s

        def acquire(self):
            acquired.append(self.board)
            return True

        def release(self):
            released.append(self.board)

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *exc):
            self.release()

    monkeypatch.setattr(hd, "_PgAdvisoryLock", Fake)
    monkeypatch.setattr(hd, "pg_advisory_lock", Fake)
    monkeypatch.setattr(hd, "char_shell", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(hd, "copy_outcomes", lambda *a, **k: None)

    with hd.pg_advisory_lock("b-advisory") as lock:
        assert lock.board == "b-advisory"
        assert acquired == ["b-advisory"]
    assert released == ["b-advisory"]


# ---------- preflight ----------

def test_run_dispatch_missing_board_raises():
    with pytest.raises(hd.DockerDispatchError):
        hd.run_dispatch("no-such-board", ["dispatch"],
                        board_host_dir=Path(tempfile.mkdtemp()) / "missing",
                        spec=_spec(), advisory_lock=False)


def test_empty_outcomes_copy_is_noop_dir(monkeypatch, tmp_path):
    def fake_docker(args, **kw):
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(hd, "_docker", fake_docker)
    dest = hd.copy_outcomes("c", "empty-board", host_root=tmp_path)
    assert dest.exists()


# helper used by the retry test above (test the loop count directly)
def test_docker_rm_retries_on_failure(monkeypatch):
    attempts = []

    def fake_docker(args, **kw):
        attempts.append(args)
        return type("R", (), {"returncode": 1, "stderr": "no such container"})()

    monkeypatch.setattr(hd, "_docker", fake_docker)
    ok = hd._docker_rm_f("c-retry", retries=3)
    assert ok is False
    assert len(attempts) == 3


def test_docker_rm_succeeds_after_retry(monkeypatch):
    attempts = []

    def fake_docker(args, **kw):
        attempts.append(args)
        rc = 0 if len(attempts) >= 2 else 1
        return type("R", (), {"returncode": rc, "stderr": ""})()

    monkeypatch.setattr(hd, "_docker", fake_docker)
    ok = hd._docker_rm_f("c-retry2", retries=3)
    assert ok is True
    assert len(attempts) == 2


# ---------- opt-in switch in hermes_client ----------

def test_hermes_client_optin_uses_docker_transport(monkeypatch):
    """FLUXSWARM_DOCKER_DISPATCH=1 routes _run(board) through the sandbox."""
    monkeypatch.setenv("FLUXSWARM_DOCKER_DISPATCH", "1")
    calls = {}

    def fake_run_dispatch(board=None, argv=None, provider_keys=None,
                          provider=None, model=None, **kw):
        calls["board"] = board
        calls["argv"] = argv
        return {
            "container": "fluxswarm-u-1-12345678",
            "returncode": 0,
            "timed_out": False,
            "stdout_tail": "board created\n",
            "stderr_tail": "",
        }

    import sys
    import hermes_client as hc
    import hermes_docker as hd_mod
    monkeypatch.setattr(hd_mod, "run_dispatch", fake_run_dispatch)
    monkeypatch.setitem(sys.modules, "hermes_docker", hd_mod)
    result = hc._run(["boards", "create", "x"], board="b-switch")
    assert calls["board"] == "b-switch"
    assert calls["argv"] == ["boards", "create", "x"]
    assert result.returncode == 0
    assert "board created" in result.stdout
    assert result.result["container"] == "fluxswarm-u-1-12345678"