"""
FluxSwarm -> Hermes bridge.

Runs the `hermes kanban` CLI as a subprocess to create per-project boards and
launch the full squad (swarm). Each FluxSwarm project maps 1:1 to a Hermes
kanban board (--board <slug>), which isolates its swarm from all others.

Key fixes (v0.5):
- Squad agents are shown WITHOUT the `ecc-` prefix to end users (display names).
- BYOK keys are injected into each agent PROFILE's .env so the squad can
  actually run (otherwise planner/architect block with no model key).
- A continuous dispatcher loop is run until the swarm reaches a terminal state,
  and generated outputs are read back from each task's workspace for review.
"""
from __future__ import annotations

import json
import os
import re
import sys
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# HERMES_BIN is overridable via FLUXSWARM_HERMES_BIN so the same code runs on
# Linux/Docker (e.g. /app/hermes/bin/hermes) as well as the dev Windows host.
# Falls back to the host's installed path when the env var is unset.
_HERMES_BIN_ENV = os.environ.get("FLUXSWARM_HERMES_BIN")
HERMES_BIN = Path(_HERMES_BIN_ENV) if _HERMES_BIN_ENV else Path("C:/Users/DELL/AppData/Local/hermes/bin/hermes.exe")
HERMES_HOME = os.environ.get("HERMES_HOME", "C:/Users/DELL/AppData/Local/hermes")
PROFILES_DIR = Path(HERMES_HOME) / "profiles"

# Safe board-slug charset. Slugs are server-generated (u{uid}-{time}-{rand},
# u{uid}-tg-…, flux-demo-…, tg-{chat}-…), but delete_boards validates every
# incoming name against this before touching the filesystem, so a corrupted DB
# row can never turn into an arbitrary path delete.
_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")

# Agent profiles (internal Hermes profile names) + display names (no ecc- prefix).
# Skill names MUST match real ECC skills under skills/ecc/skills (verified present).
SQUAD = [
    ("ecc-planner", "Planner", "Plan the feature breakdown", "plan-orchestrate"),
    ("ecc-architect", "Architect", "Design system architecture", "api-design,fastapi-patterns"),
    ("ecc-devops", "DevOps", "Set up CI/CD and containerization", "docker-patterns,deployment-patterns"),
    ("ecc-tdd", "TDD", "Write the test suite", "tdd-workflow"),
]
VERIFIER = ("ecc-reviewer", "Reviewer", "Review code quality", "agent-self-evaluation,verification-loop")
SYNTHESIZER = ("ecc-build-fixer", "Builder", "Assemble and make the build green", "orch-build-mvp")

# Map a marketplace template's DISPLAY names -> (internal profile, skills, role).
# Lets a bought squad launch with the real agents behind the friendly names.
AGENT_REGISTRY = {
    "Planner":   ("ecc-planner",   "plan-orchestrate", "worker"),
    "Architect": ("ecc-architect", "api-design,fastapi-patterns", "worker"),
    "DevOps":    ("ecc-devops",    "docker-patterns,deployment-patterns", "worker"),
    "TDD":       ("ecc-tdd",       "tdd-workflow", "worker"),
    "Reviewer":  ("ecc-reviewer",  "agent-self-evaluation,verification-loop", "verifier"),
    "Builder":   ("ecc-build-fixer", "orch-build-mvp", "synthesizer"),
}

# Map our BYOK provider names -> the env var each Hermes agent profile expects.
ENV_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "kimi": "KIMI_API_KEY",
}

# Paid BYOK providers, in precedence order (see _resolve_runtime).
_PAID_PROVIDERS = ("anthropic", "openai", "gemini", "kimi")

# Free, no-key provider that Hermes already has configured (OpenCode Free).
# It is NOT a production default: it may run only when the operator explicitly
# opts in (FLUXSWARM_DEMO_MODE=1) or when a user explicitly selects it in the
# BYOK UI. An unconfigured production launch fails fast instead of silently
# running on the free tier.
FREE_PROVIDER = "opencode-free"
FREE_MODEL = "nemotron-3-ultra-free"


class ProviderConfigError(RuntimeError):
    """Raised when no runtime is deliberately configured (never silently)."""


_DEMO_FLAGS = ("1", "true", "yes")


def _is_demo_mode() -> bool:
    return os.environ.get("FLUXSWARM_DEMO_MODE", "").strip().lower() in _DEMO_FLAGS

# Driver-loop ceiling for a swarm launch (seconds). This is the HARD upper
# bound the background dispatcher waits on a launch before it reports the true
# outcome; it is NOT the primary stuck-detection mechanism — that is the
# no-progress stall detector in `dispatch()` which stops much earlier when a
# provider outage stalls the swarm. 900s allows a healthy multi-wave swarm
# (workers -> verifier -> synthesizer) to finish while bounding the total wait
# so an upstream outage can never hold the driver for many hours. Overridable
# via FLUXSWARM_DISPATCH_TIMEOUT_S for ops.
DISPATCH_TIMEOUT_S = int(os.environ.get("FLUXSWARM_DISPATCH_TIMEOUT_S", "900"))

# When the user explicitly opts into the free provider via BYOK UI, we record it
# under this provider key so _resolve_runtime() can pick the right model.
PROVIDER_OPENCODE_FREE = "opencode-free"


def _resolve_runtime(provider_keys: Optional[dict]):
    """Return (model, provider) to pin every squad task to.

    Priority:
      1. User explicitly selected 'opencode-free' in the BYOK UI -> free hosted
         model. This WINS over any other BYOK key so a stray/paid key (e.g. a
         stale OpenAI key) can never hijack an explicitly-chosen free runtime.
      2. User BYOK key -> use that provider's model (Claude/GPT/...). The model
         itself is resolved at pin time from the operator's env overrides.
      3. Nothing supplied -> the DEPLOYMENT default: operator-configured
         provider/model (FLUXSWARM_DEFAULT_PROVIDER / FLUXSWARM_DEFAULT_MODEL),
         or the free hosted model only in Demo/dev (FLUXSWARM_DEMO_MODE=1).
         An unconfigured production raises ProviderConfigError — there is NO
         silent fallback to opencode-free.
    """
    if provider_keys:
        # Explicitly-enabled free tier takes priority over any paid BYOK key.
        if provider_keys.get("opencode-free"):
            return FREE_MODEL, FREE_PROVIDER
        # Prefer the first paid BYOK provider we have a key for.
        for prov in _PAID_PROVIDERS:
            if provider_keys.get(prov):
                return None, prov  # model resolved/bound at pin time
    return _default_runtime()


def _default_runtime() -> tuple[Optional[str], str]:
    """Production runtime configured by the operator; free only for Demo/dev.

    - FLUXSWARM_DEFAULT_PROVIDER set -> that provider, with a model from
      FLUXSWARM_DEFAULT_MODEL or FLUXSWARM_MODEL_<PROVIDER>. A provider without
      a model raises (a provider pin without a model cannot be persisted).
    - FLUXSWARM_DEMO_MODE=1 -> the free hosted runtime (explicit Demo opt-in).
    - Otherwise -> ProviderConfigError: a production deployment without a
      deliberate default must not silently send squad work to the free tier.
    """
    prov = os.environ.get("FLUXSWARM_DEFAULT_PROVIDER", "").strip()
    if prov:
        model = _operator_model_for(prov)
        if not model:
            raise ProviderConfigError(
                f"FLUXSWARM_DEFAULT_PROVIDER={prov!r} is set without a model: "
                "set FLUXSWARM_DEFAULT_MODEL or "
                f"FLUXSWARM_MODEL_{prov.upper().replace('-', '_')} "
                "(no silent fallback)."
            )
        return model, prov
    if _is_demo_mode():
        return FREE_MODEL, FREE_PROVIDER
    raise ProviderConfigError(
        "no production runtime configured: set FLUXSWARM_DEFAULT_PROVIDER (with "
        "FLUXSWARM_DEFAULT_MODEL), enable FLUXSWARM_DEMO_MODE=1 only for "
        "Demo/dev, or provide BYOK keys. Refusing to silently default to "
        "opencode-free in production."
    )


def _operator_model_for(provider: str) -> Optional[str]:
    """Optional operator-configured model override for a provider.

    Tries the per-provider override (FLUXSWARM_MODEL_<PROVIDER>) first, then the
    shared production default (FLUXSWARM_DEFAULT_MODEL). Returns None when no
    model is declared — the caller must then fail loudly rather than guess.
    """
    per_provider = os.environ.get(
        "FLUXSWARM_MODEL_" + provider.upper().replace("-", "_"), "").strip()
    if per_provider:
        return per_provider
    shared = os.environ.get("FLUXSWARM_DEFAULT_MODEL", "").strip()
    return shared or None


def _resolve_launch_runtime(provider_keys: Optional[dict]) -> tuple[str, str]:
    """Resolve a concrete, pinnable (model, provider) for a launch.

    BYOK providers get their model from the operator's env overrides; a launch
    whose runtime cannot be pinned fails fast BEFORE any board/worker exists.
    """
    model, provider = _resolve_runtime(provider_keys)
    if not model:
        model = _operator_model_for(provider)
    if not model:
        raise ProviderConfigError(
            f"cannot resolve a model for provider={provider!r}: set "
            "FLUXSWARM_MODEL_<PROVIDER> or FLUXSWARM_DEFAULT_MODEL. "
            "Refusing to fall back silently (free or paid)."
        )
    return model, provider

# Which env vars we should NEVER inject into shared profile .env files.
# Provider keys stay in the subprocess environment only (never on disk).
_PROFILE_KEY_NAMES = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "KIMI_API_KEY"}


@dataclass
class SwarmResult:
    root_id: str
    worker_ids: list[str]
    verifier_id: str
    synthesizer_id: str


def _squad_profiles() -> list[str]:
    return [p for p, *_ in SQUAD] + [VERIFIER[0], SYNTHESIZER[0]]


def cleanup_profile_keys():
    """One-time de-fang: remove provider API keys that older versions wrote
    in PLAINTEXT into the shared, cross-user profile .env files.

    Provider keys are now injected only into each subprocess environment
    (see `_run`), never persisted on disk, so they cannot leak between users.
    Idempotent and cheap; called on every swarm launch.
    """
    for prof in _squad_profiles():
        env_path = PROFILES_DIR / prof / ".env"
        if not env_path.exists():
            continue
        try:
            lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        kept = []
        changed = False
        for line in lines:
            if "=" in line and not line.startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k in _PROFILE_KEY_NAMES:
                    changed = True
                    continue
            kept.append(line)
        if changed:
            try:
                env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            except OSError:
                pass


def _run(args: list[str], board: Optional[str] = None, capture=True,
         provider_keys: Optional[dict] = None) -> subprocess.CompletedProcess:
    # IMPORTANT: --board is an option on `hermes kanban`, BEFORE the subcommand.
    cmd = [str(HERMES_BIN), "kanban"]
    if board:
        cmd += ["--board", board]
    cmd += args
    env = dict(os.environ)
    env["HERMES_HOME"] = HERMES_HOME
    # Also inject at process level as a fallback. The declared default comes
    # from the operator (or Demo/dev); an unconfigured production gets NO
    # provider env here — launch paths fail fast in _resolve_launch_runtime.
    if provider_keys:
        for prov, tok in provider_keys.items():
            ev = ENV_MAP.get(prov)
            if ev and tok:
                env[ev] = tok
    else:
        try:
            model, provider = _default_runtime()
        except ProviderConfigError:
            pass  # non-launch command, unconfigured prod: no provider env at all
        else:
            if model:
                env.setdefault("HERMES_DEFAULT_MODEL", model)
            env.setdefault("HERMES_DEFAULT_PROVIDER", provider)
    return subprocess.run(cmd, capture_output=capture, text=True, env=env, timeout=300)


def ensure_board(slug: str) -> bool:
    _raise_preflight()
    r = _run(["boards", "create", slug, "--description", f"FluxSwarm project {slug}"])
    r2 = _run(["boards", "ls"])
    return slug in r2.stdout


def preflight() -> list[str]:
    """Fail-fast diagnostics for the Hermes runtime.

    Returns a list of unmet requirements (empty list == everything present).
    Only filesystem stat()s — cheap, safe to call on every launch. A clear
    message beats the raw FileNotFoundError/KeyError a missing install would
    otherwise surface to the operator.
    """
    problems = []
    if not HERMES_BIN.exists():
        problems.append(
            f"Hermes binary not found at {HERMES_BIN} "
            "(install Hermes or point FLUXSWARM_HERMES_BIN at it)")
    if not PROFILES_DIR.is_dir():
        problems.append(f"Hermes profiles dir not found at {PROFILES_DIR} (check HERMES_HOME)")
    for prof in _squad_profiles():
        if not (PROFILES_DIR / prof).is_dir():
            problems.append(f"squad profile missing: {PROFILES_DIR / prof}")
    return problems


def _raise_preflight():
    """Raise RuntimeError with an actionable message when the Hermes runtime is
    not usable. Fail fast BEFORE the caller pays for a broken board."""
    problems = preflight()
    if problems:
        raise RuntimeError("Hermes runtime not ready: " + "; ".join(problems))


# The Kanban Swarm CLI (`hermes kanban swarm`) hard-codes this skill name on the
# verifier task (`hermes_cli/kanban_swarm.py`) and offers no way to override it.
# The skill ships inside the bundled ``software-development`` collection, which
# is NOT visible to the ECC profiles (each scans only ``skills/ecc/skills`` +
# its own profile-local skills). A dispatcher-owned reviewer worker therefore
# receives ``--skills requesting-code-review`` and dies at startup with
# "Unknown skill(s): requesting-code-review". Candidate C fixes this by
# provisioning the REAL bundled skill into ``skills/ecc/skills/`` (byte-for-byte)
# BEFORE the swarm is created, so the verifier task resolves it normally.
_VERIFIER_SKILL_NAME = "requesting-code-review"
_ECC_SKILLS_DIR_NAME = "ecc/skills"


def _bundled_verifier_skill_dir() -> Path:
    """Locate the REAL bundled ``requesting-code-review`` skill directory.

    Deterministic repository-relative discovery first (the Hermes bundled
    ``software-development`` collection under HERMES_HOME/skills). Falls back to
    Hermes' own skill-directory discovery mechanism when present.
    """
    primary = Path(HERMES_HOME) / "skills" / "software-development" / _VERIFIER_SKILL_NAME
    if primary.is_dir():
        return primary
    try:
        from agent.skill_utils import get_all_skills_dirs
        for skills_dir in get_all_skills_dirs():
            cand = Path(skills_dir) / "software-development" / _VERIFIER_SKILL_NAME
            if cand.is_dir():
                return cand
            cand2 = Path(skills_dir) / _VERIFIER_SKILL_NAME
            if cand2.is_dir() and (cand2 / "SKILL.md").exists():
                return cand2
    except Exception:
        pass
    return primary  # caller reports it cleanly when absent


def _ensure_verifier_skill() -> Path:
    """Provision the real bundled ``requesting-code-review`` skill for the
    ``ecc-reviewer`` profile. Idempotent and non-destructive.

    - Target ``skills/ecc/skills/requesting-code-review/SKILL.md`` present ->
      no-op (NEVER overwrite, even a foreign/operator-tuned copy).
    - Target missing OR an EMPTY stale dir (e.g. left by an interrupted earlier
      provisioning) -> copy the REAL bundled skill from the
      ``software-development`` collection BYTE-FOR-BYTE. An empty dir must not
      silently re-create the MISSING-skill worker death.
    - Target EXISTS with content but no ``SKILL.md`` -> fail loudly and refuse to
      delete what may be a foreign directory.
    - Bundled source absent -> fail safely with a clear diagnostic; do NOT
      fabricate a skill or try to fake the review behavior.

    Returns the target skill directory (for callers/tests).
    """
    target = Path(HERMES_HOME) / "skills" / _ECC_SKILLS_DIR_NAME / _VERIFIER_SKILL_NAME
    if (target / "SKILL.md").exists():
        return target
    source = _bundled_verifier_skill_dir()
    if not source.is_dir() or not (source / "SKILL.md").exists():
        raise RuntimeError(
            f"cannot provision verifier skill '{_VERIFIER_SKILL_NAME}': bundled "
            f"source not found under {Path(HERMES_HOME) / 'skills'} "
            "(expected in the 'software-development' collection). Refusing to "
            "fabricate a replacement skill."
        )
    if target.exists():
        if any(target.iterdir()):
            raise RuntimeError(
                f"cannot provision verifier skill '{_VERIFIER_SKILL_NAME}': target "
                f"{target} exists with content but no SKILL.md; refusing to "
                "overwrite a possibly-foreign directory. Remove it manually or "
                "point the profile's skills.external_dirs elsewhere."
            )
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def launch_swarm(board: str, goal: str, provider_keys: Optional[dict] = None) -> SwarmResult:
    # Keys are passed via subprocess environment only (never written to disk).
    # Fail fast BEFORE any board/worker exists when no runtime is configured.
    _resolve_launch_runtime(provider_keys)
    _raise_preflight()
    cleanup_profile_keys()
    # The swarm CLI hard-codes the verifier skill; make sure it resolves under
    # the ecc-reviewer profile BEFORE the swarm/board tasks are created.
    _ensure_verifier_skill()
    worker_args = []
    for prof, disp, title, skills in SQUAD:
        worker_args += ["--worker", f"{prof}:{title}:{skills}"]
    cmd = ["swarm", goal] + worker_args + [
        "--verifier", VERIFIER[0],
        "--synthesizer", SYNTHESIZER[0],
        "--created-by", "fluxswarm",
        "--json",
    ]
    r = _run(cmd, board=board, provider_keys=provider_keys)
    if r.returncode != 0:
        raise RuntimeError(f"swarm launch failed: {r.stderr}")
    data = json.loads(r.stdout)
    # Pin a concrete model/provider on every task so the dispatcher never
    # falls back to a keyless default that would mark the card blocked.
    _pin_runtime(board, provider_keys)
    return SwarmResult(
        root_id=data["root_id"],
        worker_ids=data.get("worker_ids", []),
        verifier_id=data["verifier_id"],
        synthesizer_id=data["synthesizer_id"],
    )


def _pin_runtime(board: str, provider_keys: Optional[dict]):
    """Set --model/--provider on every squad task via `kanban set-model`.

    Pinning an explicit model/provider is what fixes the 'blocked' issue: the
    dispatcher spawns workers using the profile's default model, which (without
    a key) fails and marks the card blocked. The runtime is resolved deliberately
    (operator default / Demo free / BYOK) — never a silent fallback — and any
    `set-model` failure stops the launch loudly.
    """
    model, provider = _resolve_launch_runtime(provider_keys)
    tasks = list_tasks(board)
    for t in tasks:
        tid = t.get("id")
        if not tid:
            continue
        args = ["set-model", tid, model, "--provider", provider]
        r = _run(args, board=board, provider_keys=provider_keys, capture=True)
        if r.returncode != 0:
            detail = (r.stderr or r.stdout or "").strip()
            raise RuntimeError(
                f"kanban set-model failed (rc={r.returncode}) for task {tid}: {detail}"
            )


def launch_from_template(board: str, goal: str, agents: list[str],
                          provider_keys: Optional[dict] = None) -> SwarmResult:
    """Launch a squad built from a marketplace template's display-name agents.

    Each name in `agents` is resolved via AGENT_REGISTRY to (profile, skills, role).
    """
    # Fail fast BEFORE any board/worker exists when no runtime is configured.
    _resolve_launch_runtime(provider_keys)
    workers, verifier, synthesizer = [], None, None
    for name in agents:
        rec = AGENT_REGISTRY.get(name.strip())
        if not rec:
            continue
        prof, skills, role = rec
        if role == "worker":
            workers.append((prof, name, f"{name} task", skills))
        elif role == "verifier":
            verifier = (prof, name, f"{name} review")
        elif role == "synthesizer":
            synthesizer = (prof, name, f"{name} assemble")
    if not workers:
        raise ValueError("القالب لا يحتوي وكلاء صالحين")
    verifier = verifier or VERIFIER
    synthesizer = synthesizer or SYNTHESIZER

    _raise_preflight()
    cleanup_profile_keys()
    # Same verifier-skill provisioning as launch_swarm: template-launched
    # squads still create a verifier via the swarm CLI (hard-coded skill).
    _ensure_verifier_skill()
    worker_args = []
    for prof, disp, title, skills in workers:
        worker_args += ["--worker", f"{prof}:{title}:{skills}"]
    cmd = ["swarm", goal] + worker_args + [
        "--verifier", verifier[0],
        "--synthesizer", synthesizer[0],
        "--created-by", "fluxswarm",
        "--json",
    ]
    r = _run(cmd, board=board, provider_keys=provider_keys)
    if r.returncode != 0:
        raise RuntimeError(f"swarm launch failed: {r.stderr}")
    data = json.loads(r.stdout)
    _pin_runtime(board, provider_keys)
    return SwarmResult(
        root_id=data["root_id"],
        worker_ids=data.get("worker_ids", []),
        verifier_id=data["verifier_id"],
        synthesizer_id=data["synthesizer_id"],
    )


def _board_activity_sig(board: str) -> tuple:
    """Monotonic worker-activity fingerprint for the board, read from ``kanban.db``.

    A healthy worker keeps calling the LLM and using tools while a task's
    ``state`` may not have changed yet (the classic false-stall: single planner
    doing reasoning + ``kanban_show`` before ever completing). The state-only
    signature used to flag that healthy worker as ``no_progress``. This helper
    adds a real activity signal — the highest worker heartbeat timestamp and the
    task-event stream (count + last created_at) — so the stall detector only
    trips when a worker is BOTH stuck in an unchanged state AND producing no
    heartbeats / task-events.

    Returns () when the board DB is unavailable (synthetic/offline/unit-test
    boards), in which case the caller falls back to the state-only signature and
    the pre-existing stall semantics are preserved.
    """
    db = Path(HERMES_HOME) / "kanban" / "boards" / board / "kanban.db"
    try:
        if not db.exists():
            return ()
        c = sqlite3.connect(str(db))
        try:
            max_hb = c.execute(
                "SELECT MAX(COALESCE(last_heartbeat_at, 0)) FROM tasks"
            ).fetchone()[0] or 0
            evt_count = c.execute(
                "SELECT COUNT(*) FROM task_events"
            ).fetchone()[0] or 0
            evt_max = c.execute(
                "SELECT MAX(COALESCE(created_at, 0)) FROM task_events"
            ).fetchone()[0] or 0
            return (int(max_hb), int(evt_count), int(evt_max))
        finally:
            c.close()
    except Exception:
        return ()


# ---------------------------------------------------------------------------
# Windows process-tree cleanup for orphaned Hermes workers.
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as _wt

    _STILL_ACTIVE = 259
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _PROCESS_QUERY_INFORMATION = 0x0400

    class _PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", _wt.DWORD),
            ("cntUsage", _wt.DWORD),
            ("th32ProcessID", _wt.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", _wt.DWORD),
            ("cntThreads", _wt.DWORD),
            ("th32ParentProcessID", _wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", _wt.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    def _get_child_pids_win32(pid: int) -> list[int]:
        """Return direct child PIDs of *pid* using a Win32 process snapshot."""
        children: list[int] = []
        snap = _kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if snap == _wt.HANDLE(-1).value:
            return children
        try:
            pe = _PROCESSENTRY32()
            pe.dwSize = ctypes.sizeof(_PROCESSENTRY32)
            if _kernel32.Process32First(snap, ctypes.byref(pe)):
                while True:
                    if pe.th32ParentProcessID == pid:
                        children.append(pe.th32ProcessID)
                    if not _kernel32.Process32Next(snap, ctypes.byref(pe)):
                        break
        finally:
            _kernel32.CloseHandle(snap)
        return children

    def _sigterm(pid: int, timeout_s: float = 3.0) -> bool:
        """Best-effort graceful termination; returns True if process exited."""
        try:
            h = _kernel32.OpenProcess(
                _PROCESS_TERMINATE | _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION,
                False, pid,
            )
            if not h:
                return False
            try:
                _kernel32.TerminateProcess(h, 1)
                wait_ms = int(timeout_s * 1000)
                _kernel32.WaitForSingleObject(h, wait_ms)
                ec = _wt.DWORD()
                _kernel32.GetExitCodeProcess(h, ctypes.byref(ec))
                return ec.value != _STILL_ACTIVE
            finally:
                _kernel32.CloseHandle(h)
        except Exception:
            return False

else:
    def _get_child_pids_win32(pid: int) -> list[int]:  # type: ignore[misc]
        return []

    def _sigterm(pid: int, timeout_s: float = 3.0) -> bool:  # type: ignore[misc]
        return False


def kill_process_tree(pid: int, grace_s: float = 3.0) -> None:
    """Terminate a process and its descendants.

    1. Recursively find children via Win32 snapshot (Windows only).
    2. Kill children deepest-first (leaf processes first).
    3. Terminate the root with SIGTERM; escalate to SIGKILL after *grace_s*.
    """
    if pid <= 0:
        return
    try:
        children = _get_child_pids_win32(pid)
    except Exception:
        children = []
    for child in children:
        try:
            kill_process_tree(child, grace_s=grace_s)
        except Exception:
            pass
    try:
        if _sigterm(pid, timeout_s=grace_s):
            return
    except Exception:
        pass
    try:
        h = _kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid) if sys.platform == "win32" else None
        if h:
            try:
                _kernel32.TerminateProcess(h, 1)
            finally:
                _kernel32.CloseHandle(h)
    except Exception:
        pass


def read_worker_pids(board: str) -> list[int]:
    """Read worker process IDs for *board* from kanban.db (non-destructive)."""
    db = Path(HERMES_HOME) / "kanban" / "boards" / board / "kanban.db"
    try:
        if not db.exists():
            return []
        c = sqlite3.connect(str(db))
        try:
            rows = c.execute(
                "SELECT worker_pid FROM tasks "
                "WHERE worker_pid IS NOT NULL AND worker_pid > 0 "
                "AND status IN ('running', 'ready', 'todo')",
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            c.close()
    except Exception:
        return []


def _cleanup_board_workers(board: str) -> None:
    """Terminate Hermes worker processes owned by *board*.

    Called on dispatch timeout / stall / exception to prevent orphaned
    processes from accumulating.  Reads worker PIDs from kanban.db (the
 authoritative source managed by the Hermes CLI) and kills each
 process tree.  Already-dead processes are ignored safely.
    """
    try:
        pids = read_worker_pids(board)
    except Exception:
        return
    for pid in pids:
        try:
            kill_process_tree(pid)
        except Exception:
            pass


def dispatch(board: str, max_spawn: int = 8, dry_run: bool = False,
             provider_keys: Optional[dict] = None, blocking: bool = True,
             timeout_s: int = 600, stall_passes: int = 4,
             min_wait_s: int = 60) -> dict:
    """Run the dispatcher. If blocking, poll until terminal state or timeout.

    Returns a dict that always carries ``outcome`` so the caller can tell a
    launch that converged from one that was cut short by the bounded wall-clock
    window (provider/worker stall). ``timed_out`` is True only when we left the
    loop with non-terminal tasks still pending. ``stuck_tasks`` lists the tasks
    that were still ``running``/``queued`` when we stopped (i.e. what would
    otherwise be left stranded).

    The blocking window is deliberately bounded (``timeout_s``) — a provider
    outage must not let the driver wait for many hours. Hermes's own reclamation
    loop keeps re-dispatching inside this window; when the window expires we
    report the residual state truthfully instead of silently dripping a
    forever-stuck board.

    ``stall_passes`` (default 4) triggers an early no-progress break: if the
    board's task states stop changing across several consecutive passes while
    work is still pending, the swarm is not converging (provider/worker stuck),
    so the driver stops well before ``timeout_s`` instead of waiting many hours.
    ``min_wait_s`` is a floor so a healthy multi-wave swarm that is still
    advancing is never cut short by the stall detector in its opening moments.
    """
    args = ["dispatch"]
    if dry_run:
        args.append("--dry-run")
    if max_spawn:
        args += ["--max", str(max_spawn)]
    r = _run(args, board=board, provider_keys=provider_keys)
    if r.returncode != 0:
        raise RuntimeError(f"dispatch failed: {r.stderr}")
    try:
        first = json.loads(r.stdout)
    except json.JSONDecodeError:
        first = {"raw": r.stdout.strip()}

    # Track whether worker cleanup is needed.  Set to False only on successful
    # convergence; the finally block then skips cleanup.  On stall, timeout, or
    # any exception the flag stays True and cleanup runs.
    _needs_cleanup = True

    try:
        if blocking:
            # Keep dispatching in passes until everything is done/blocked or we
            # conclude the board is stuck (no forward progress).
            deadline = time.time() + timeout_s
            start = time.time()
            last_sig = None
            unchanged = 0
            while time.time() < deadline:
                tasks = list_tasks(board)
                states = [t.get("state") for t in tasks]
                # Robust progress signature: worker ACTIVITY (heartbeat / task events)
                # combined with task state. A healthy worker that is steadily calling
                # the LLM and using tools advances heartbeats/events even while no
                # task state has changed yet, so it is NOT misclassified as stalled.
                # A genuinely stalled worker (provider/worker hang) advances neither,
                # so it is still caught after stall_passes unchanged passes.
                activity = _board_activity_sig(board)
                # Heartbeat freshness is the liveness gate. Heartbeats land roughly
                # every ~60s — far slower than the ~10-15s dispatcher pass cadence —
                # so the raw activity TUPLE (which only changes on a new heartbeat)
                # must NOT be the stall signal by itself: that would falsely stall a
                # healthy long-running worker in the gap between two heartbeats.
                # Instead, a worker with a RECENT heartbeat is demonstrably alive and
                # progressing (mid-LLM/tool work), so we only declare a stall once the
                # board's heartbeat has actually gone STALE (> heartbeat_grace) while
                # task states stay unchanged for stall_passes passes. `()` (DB
                # unavailable) counts as stale so the detector still works without
                # board DB access (and in unit tests).
                heartbeat_grace = 180
                stale = (activity == () or (time.time() - activity[0]) > heartbeat_grace)
                sig = (activity, tuple(sorted(states)))
                if not states or all(s in ("done", "blocked") for s in states):
                    _needs_cleanup = False
                    first["terminal"] = True
                    first["timed_out"] = False
                    first["outcome"] = "ok"
                    first["stuck_tasks"] = []
                    return first
                if sig == last_sig:
                    unchanged += 1
                else:
                    unchanged = 0
                last_sig = sig
                time.sleep(8)
                rr = _run(["dispatch", "--max", str(max_spawn)], board=board,
                          provider_keys=provider_keys)
                # Early no-progress break: the same non-terminal states for several
                # consecutive passes means the swarm is stuck (provider/worker hang),
                # not converging. Give a healthy launch a grace floor so its first
                # waves have time to start before we ever evaluate the stall.
                if (unchanged >= stall_passes
                        and time.time() - start >= min_wait_s
                        and stale):
                    _cleanup_board_workers(board)
                    first["terminal"] = False
                    first["timed_out"] = True
                    first["stall"] = True
                    first["stall_passes"] = unchanged
                    first["outcome"] = "stuck"
                    first["stuck_tasks"] = [t for t in tasks
                                            if t.get("state") in ("running", "queued")]
                    first["stuck_run_count"] = sum(
                        1 for t in tasks if t.get("state") == "running")
                    first["done_count"] = sum(1 for t in tasks if t.get("state") == "done")
                    first["deadline_s"] = timeout_s
                    first["early"] = True
                    _needs_cleanup = False
                    return first
            # Wall-clock window expired with non-terminal work still pending.
            _cleanup_board_workers(board)
            tasks = list_tasks(board)
            stuck = [t for t in tasks if t.get("state") in ("running", "queued")]
            first["terminal"] = False
            first["timed_out"] = True
            first["outcome"] = "stuck"
            first["stuck_tasks"] = stuck
            first["stuck_run_count"] = sum(
                1 for t in stuck if t.get("state") == "running")
            first["done_count"] = sum(1 for t in list_tasks(board) if t.get("state") == "done")
            first["deadline_s"] = timeout_s
            _needs_cleanup = False
        else:
            # Non-blocking single pass: we deliberately do NOT stamp `terminal` —
            # the caller must treat a non-blocking dispatch as an in-flight launch
            # that may still have work pending (see pragmatics/contract pinned by
            # test_dispatch_completion.py). Provide an outcome label for callers
            # that want one, but leave terminality untouched.
            first.setdefault("timed_out", False)
            first.setdefault("outcome", "pending")
            _needs_cleanup = False
        return first
    finally:
        if _needs_cleanup:
            _cleanup_board_workers(board)


def list_tasks(board: str) -> list[dict]:
    r = _run(["list", "--json"], board=board)
    if r.returncode != 0:
        raise RuntimeError(f"list failed: {r.stderr}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    norm = {"done": "done", "running": "running", "ready": "queued",
            "todo": "queued", "blocked": "blocked"}
    for t in data:
        t["state"] = norm.get(t.get("status", ""), t.get("status", "unknown"))
        # Strip the ecc- prefix from assignee for display.
        a = t.get("assignee", "")
        if a.startswith("ecc-"):
            a = a[len("ecc-"):]
        # verifier/synthesizer show as "ecc-reviewer:..." -> keep readable
        if ":" in a:
            pre, post = a.split(":", 1)
            if pre.startswith("ecc-"):
                a = pre[len("ecc-"):] + ":" + post
        t["assignee_display"] = a
    return data


def board_has_completed_work(board: str) -> bool:
    """True when any AGENT task on the board reached ``done`` (meaningful work).

    Used for credit reconciliation: a launch that never completed a real agent
    task (e.g. the provider fails before any agent finishes) produced no work
    and is eligible for a credit refund; one that finished at least one agent
    task consumed real work and must not be refunded.

    The swarm ROOT planning card (assignee ``fluxswarm``) is auto-completed
    immediately as the shared blackboard/anchor — it represents no agent work
    and is deliberately excluded, so a board where every agent failed still
    counts as "no completed work" and qualifies for the refund.
    """
    try:
        tasks = list_tasks(board)
    except Exception:
        return False
    for t in tasks:
        if t.get("state") == "done":
            assignee = (t.get("assignee") or "").strip().lower()
            if assignee and assignee != "fluxswarm":
                return True
    return False


def show_task(board: str, task_id: str) -> dict:
    r = _run(["show", task_id, "--json"], board=board)
    if r.returncode != 0:
        raise RuntimeError(f"show failed: {r.stderr}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


def read_workspace(board: str) -> str:
    """Collect generated files from the board's task workspaces for review."""
    ws_root = Path(HERMES_HOME) / "kanban" / "boards" / board / "workspaces"
    out = []
    try:
        for f in ws_root.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".md", ".txt", ".json", ".yaml", ".yml"):
                out.append(f"--- {f.relative_to(ws_root)} ---\n")
                out.append(f.read_text(encoding="utf-8", errors="ignore")[:3000])
    except Exception:
        pass
    return "\n".join(out)


def list_boards() -> list[dict]:
    r = _run(["boards", "ls"])
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts or parts[0] in ("SLUG", "Board:"):
            continue
        slug = parts[0].strip("● ")
        if slug and slug != "SLUG":
            out.append({"slug": slug})
    return out


def delete_boards(slugs: list[str], boards_root: Path | None = None) -> int:
    """Delete a user's Hermes kanban board directories (account erasure).

    Defensive by construction: every slug must match the safe charset AND its
    resolved path must stay inside the kanban boards root, so a corrupted slug
    can never escalate into an arbitrary filesystem delete. Demo boards
    (``flux-demo-*``) are shared and deliberately never passed here. Returns the
    number of boards actually removed.
    """
    root = Path(boards_root) if boards_root is not None else Path(HERMES_HOME) / "kanban" / "boards"
    root_resolved = str(root.resolve()) + os.sep
    removed = 0
    for raw in slugs or []:
        slug = str(raw or "")
        if not _SAFE_SLUG_RE.match(slug):
            continue
        target = (root / slug).resolve()
        # Containment: resolve() normalises symlinks, so targets must literally
        # live under the boards root. slug has no separators, but the check stays
        # for defence when the boards root itself is redirected.
        if not str(target).startswith(root_resolved):
            continue
        try:
            if target.exists():
                shutil.rmtree(target)
                removed += 1
        except OSError:
            continue
    return removed
