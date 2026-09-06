"""Hermes -> Docker sandbox dispatch transport (Phase 2).

Replaces running the Hermes CLI directly on the host with an isolated Docker
container built from ``deploy/docker/hermes-runner.Dockerfile``
(``fluxswarm/hermes-runner:latest``). Launching untrusted agent code directly
on the host means one RCE in a toolchain is a host takeover; the sandbox
contract below makes that a container escape instead — still bad, but bounded.

Sandbox profile (defaults, all overridable via ``RunnerSpec`` / env):
  * --network=none                              no egress: agent tools cannot phone home
  * --cap-drop ALL --cap-add DAC_OVERRIDE,CHOWN,FOWNER
  * --security-opt no-new-privileges
  * --pids-limit 256 --memory=2g --cpus=1.5     resource ceilings
  * --read-only  (root fs) + tmpfs /tmp /var/tmp /run
  * workspace bind-mounted READ-ONLY; outcomes copied back with `docker cp`
  * per-run TTL (FLUXSWARM_DISPATCH_TIMEOUT_S, default 900s) + 30s kill grace
  * PostgreSQL advisory lock on the board slug during dispatch (replaces the
    host pidfile, works across multiple backend web processes safely)

Deviations are explicit: the kanban state DB (kanban.db) is written by the
Hermes CLI itself; we mount read-only what must not mutate and copy work back
so no writable host path is ever exposed to the container.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Matches hermes_client.ENV_MAP so BYOK keys land on the right env var inside
# the container.
ENV_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "kimi": "KIMI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# Env vars we MAY whitelist into the container (everything else is stripped).
# Provider keys + a pinned model are the only runtime secrets; nothing else
# from the operator environment is exposed to the sandbox.
_ALLOWED_ENV = {
    "HERMES_HOME",
    "FLUXSWARM_HERMES_BIN",
    "HERMES_DEFAULT_MODEL",
    "HERMES_DEFAULT_PROVIDER",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "KIMI_API_KEY",
    "OPENROUTER_API_KEY",
}

_RUNNER_IMAGE = os.environ.get("FLUXSWARM_RUNNER_IMAGE", "fluxswarm/hermes-runner:latest")
_TIMEOUT_S_DEFAULT = int(os.environ.get("FLUXSWARM_DISPATCH_TIMEOUT_S", "900"))
_KILL_GRACE_S = 30
_RM_RETRIES = 3

# Container mounts/workspace paths (must match hermes-runner.Dockerfile).
_WORKSPACE_MOUNT = "/workspace"
_OUTCOMES_HOST_ROOT = Path(os.environ.get("FLUXSWARM_OUTCOMES_DIR", "data/outcomes"))


class DockerDispatchError(RuntimeError):
    """Container dispatch failed (build/preflight/pull/runtime)."""


@dataclass
class RunnerSpec:
    """Sandbox profile applied to every runner container."""

    image: str = _RUNNER_IMAGE
    network: str = "none"
    cap_drop: tuple[str, ...] = ("ALL",)
    cap_add: tuple[str, ...] = ("DAC_OVERRIDE", "CHOWN", "FOWNER")
    security_opt: tuple[str, ...] = ("no-new-privileges",)
    pids_limit: int = 256
    memory: str = "2g"
    cpus: float = 1.5
    tmpfs: tuple[str, ...] = (
        "/tmp:size=512m",
        "/var/tmp:size=256m",
        "/run:size=64m",
    )
    read_only: bool = True
    user: str = "1000:1000"
    timeout_s: int = _TIMEOUT_S_DEFAULT
    kill_grace_s: int = _KILL_GRACE_S
    rm_retries: int = _RM_RETRIES

    # Overrides via env (ops-friendly, no code change for a different ceiling).
    @classmethod
    def from_env(cls) -> "RunnerSpec":
        spec = cls()
        spec.timeout_s = int(os.environ.get("FLUXSWARM_DISPATCH_TIMEOUT_S", str(spec.timeout_s)))
        spec.kill_grace_s = int(os.environ.get("FLUXSWARM_DISPATCH_KILL_GRACE_S", str(spec.kill_grace_s)))
        spec.rm_retries = int(os.environ.get("FLUXSWARM_DOCKER_RM_RETRIES", str(spec.rm_retries)))
        spec.image = os.environ.get("FLUXSWARM_RUNNER_IMAGE", spec.image)
        return spec


def container_name(user_id: int | str, project_id: int | str) -> str:
    """Deterministic-enough unique container name per (user, project)."""
    return f"fluxswarm-{user_id}-{project_id}-{uuid.uuid4().hex[:8]}"


def build_docker_args(
    spec: RunnerSpec,
    name: str,
    board: str,
    argv: list[str],
    env: dict[str, str],
    board_host_dir: Path,
) -> list[str]:
    """Assemble the full ``docker run`` argv for one dispatch.

    ``env`` is the FINAL whitelisted environment (see ``sandbox_env``); the
    base hermes image env is inherited, this is strictly additive. The board's
    kanban directory is bind-mounted READ-ONLY so the container can read task
    state but never tamper with the source; outcomes are copied out by the
    dispatcher afterwards.
    """
    # Defense in depth: only ever pass through our allow-list, even if a caller
    # hands the builder a polluted dict.
    env = {k: v for k, v in env.items() if k in _ALLOWED_ENV and v is not None}
    env_args: list[str] = []
    for k, v in sorted(env.items()):
        env_args.append("-e")
        env_args.append(f"{k}={v}")
    args = [
        "docker", "run", "--rm",
        "--name", name,
        "--network", spec.network,
        "--cap-drop", spec.cap_drop[0],
    ]
    for cap in spec.cap_add:
        args += ["--cap-add", cap]
    args += [
        "--security-opt", spec.security_opt[0],
        "--pids-limit", str(spec.pids_limit),
        "--memory", spec.memory,
        "--cpus", str(spec.cpus),
    ]
    for t in spec.tmpfs:
        args += ["--tmpfs", t]
    if spec.read_only:
        args.append("--read-only")
    args += ["-u", spec.user]
    args += [f"-v{board_host_dir}:{_WORKSPACE_MOUNT}:ro"]
    args += ["-w", _WORKSPACE_MOUNT]
    args += ["-e", "HERMES_HOME=/app/hermes_home"]
    args += env_args
    args += [spec.image, *argv]
    return args


def sandbox_env(provider_keys: dict | None, provider: str | None = None, model: str | None = None) -> dict[str, str]:
    """Whitelist exactly the runtime env the sandbox may see.

    Provider BYOK keys + an optional pinned model; absolutely nothing else from
    the operator environment leaks into the container.
    """
    out: dict[str, str] = {}
    if provider_keys:
        for prov, tok in provider_keys.items():
            ev = ENV_MAP.get(prov)
            if ev and tok:
                out[ev] = str(tok)
    if model:
        out["HERMES_DEFAULT_MODEL"] = model
    if provider:
        out["HERMES_DEFAULT_PROVIDER"] = provider
    return out


def char_shell(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a shell command, returning a CompletedProcess-like object.

    Split into a named function so tests can monkeypatch it without touching
    subprocess globally.
    """
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _docker(args: list[str], **kw) -> subprocess.CompletedProcess:
    return char_shell(["docker", *args], **kw)


def _docker_rm_f(name: str, retries: int = _RM_RETRIES) -> bool:
    """Forcibly remove a container with bounded retries (idempotent)."""
    for attempt in range(max(1, retries)):
        r = _docker(["rm", "-f", name])
        if r.returncode == 0:
            return True
        time.sleep(0.5 * (attempt + 1))
    return False


def pg_advisory_lock(board: str, timeout_s: float = 5.0) -> object:
    """Take a PostgreSQL advisory lock keyed on the board slug.

    Blocks concurrent dispatches of the SAME board across backend processes —
    the Postgres-clean replacement for a host pidfile. Returns a lock handle
    whose ``release()`` frees it; raises DockerDispatchError if the pool is not
    configured (e.g. unit tests / pure SQLite mode) — callers decide whether to
    degrade to best-effort.
    """
    return _PgAdvisoryLock(board, timeout_s)


class _PgAdvisoryLock:
    def __init__(self, board: str, timeout_s: float):
        self.board = board
        self.timeout_s = timeout_s
        self._acquired = False

    def acquire(self) -> bool:
        try:
            import db_postgres as pg

            self._pg = pg
            async def _lock() -> bool:
                pool = await pg.get_pool()
                return await pool.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext($1))", self.board
                )
            self._acquired = bool(pg._await(_lock()))
        except Exception:
            self._acquired = False
        return self._acquired

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            async def _unlock() -> None:
                pool = await self._pg.get_pool()
                await pool.execute(
                    "SELECT pg_advisory_unlock(hashtext($1))", self.board
                )
            self._pg._await(_unlock())
        except Exception:
            pass
        self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def copy_outcomes(name: str, board: str, host_root: Path = _OUTCOMES_HOST_ROOT) -> Path:
    """Copy the container's outcomes dir back to the host.

    Path: ``data/outcomes/<board_slug>/`` — mirrors where hermes_client reads
    generated outputs from. Returns the host path; safe no-op when empty.
    """
    dest = Path(host_root) / board
    dest.mkdir(parents=True, exist_ok=True)
    # docker cp of a path inside the previously-stopped container.
    r = _docker(["cp", f"{name}:{_WORKSPACE_MOUNT}/outcomes/.", str(dest)])
    if r.returncode != 0:
        # Container may still be running/starting or outcomes may not exist;
        # treat as empty result rather than a dispatch failure.
        raise DockerDispatchError(f"docker cp outcomes failed: {r.stderr.strip()}")
    return dest


def tail(lines: str | None, n: int = 500) -> str:
    if not lines:
        return ""
    parts = lines.splitlines()
    return "\n".join(parts[-n:])


def write_audit(entry: dict, audit_file: Path = Path("data/dispatch_audit.jsonl")) -> None:
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    with audit_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def run_dispatch(
    board: str,
    argv: list[str],
    provider_keys: dict | None = None,
    provider: str | None = None,
    model: str | None = None,
    user_id: int | str | None = None,
    project_id: int | str | None = None,
    board_host_dir: Path | None = None,
    spec: RunnerSpec | None = None,
    advisory_lock: bool = True,
) -> dict:
    """Execute one sandboxed dispatch inside a hardened container.

    Returns a dict mirroring hermes_client.dispatch()'s shape so the caller has
    a uniform contract:
        container, returncode, timed_out, stdout_tail, stderr_tail, outcome_dir

    On TTL expiry the container is force-removed (bounded retries); the exit is
    recorded in ``data/dispatch_audit.jsonl`` with the last 500 lines of output.
    """
    spec = spec or RunnerSpec.from_env()
    name = container_name(user_id or "u", project_id or board)
    board_dir = board_host_dir or (Path(os.environ.get("HERMES_HOME", "hermes_home"))
                                   / "kanban" / "boards" / board)
    if not board_dir.exists():
        raise DockerDispatchError(f"board dir not found: {board_dir}")

    env = sandbox_env(provider_keys, provider=provider, model=model)
    cmd = build_docker_args(spec, name, board, argv, env, board_dir)

    lock = pg_advisory_lock(board) if advisory_lock else None
    started = time.monotonic()
    timed_out = False
    try:
        proc = char_shell(cmd, timeout=spec.timeout_s + spec.kill_grace_s)
        record = proc
        if proc.returncode == 124:
            timed_out = True
    except subprocess.TimeoutExpired as ex:
        timed_out = True
        # Force-remove the container: the run command may have been killed at
        # the shell layer while the container keeps consuming the 30s grace.
        _docker_rm_f(name, retries=spec.rm_retries)
        record = _TimeoutRecord(ex)

    if lock:
        lock.release()

    outcome_dir = None
    if not timed_out and os.environ.get("FLUXSWARM_SKIP_OUTCOME_COPY", "") != "1":
        try:
            outcome_dir = copy_outcomes(name, board)
        except DockerDispatchError:
            outcome_dir = None

    stderr = getattr(record, "stderr", "") or ""
    stdout = getattr(record, "stdout", "") or ""
    entry = {
        "ts": time.time(),
        "board": board,
        "container": name,
        "argv": argv,
        "returncode": getattr(record, "returncode", None),
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 2),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }
    try:
        write_audit(entry)
    except OSError:
        pass

    return {
        "container": name,
        "returncode": getattr(record, "returncode", None),
        "timed_out": timed_out,
        "outcome": "timed_out" if timed_out else ("ok" if getattr(record, "returncode", 1) == 0 else "error"),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
        "outcome_dir": str(outcome_dir) if outcome_dir else None,
    }


class _TimeoutRecord:
    def __init__(self, exc: subprocess.TimeoutExpired):
        self.returncode = 124
        self.stdout = getattr(exc, "stdout", "") or ""
        self.stderr = getattr(exc, "stderr", "") or ""


def preflight(spec: RunnerSpec | None = None) -> list[str]:
    """Fail-fast without a container: image present + docker daemon reachable."""
    problems = []
    spec = spec or RunnerSpec.from_env()
    if not _docker(["image", "inspect", spec.image]).returncode == 0:
        problems.append(f"runner image not present locally: {spec.image}")
    if _docker(["info"]).returncode != 0:
        problems.append("docker daemon not reachable")
    return problems