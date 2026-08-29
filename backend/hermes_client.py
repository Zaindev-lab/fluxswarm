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
import shutil
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

# Free, no-key provider that Hermes already has configured (OpenCode Free).
# Used as the default so the squad runs end-to-end without the user supplying a key.
FREE_PROVIDER = "opencode-free"
FREE_MODEL = "hy3-free"

# When the user explicitly opts into the free provider via BYOK UI, we record it
# under this provider key so _resolve_runtime() can pick the right model.
PROVIDER_OPENCODE_FREE = "opencode-free"


def _resolve_runtime(provider_keys: Optional[dict]):
    """Return (model, provider) to pin every squad task to.

    Priority:
      1. User BYOK key -> use that provider's model (Claude/GPT/...).
      2. User selected 'opencode-free' in the BYOK UI -> free hosted model.
      3. Nothing supplied -> free hosted model (default, no key needed).
    """
    if provider_keys:
        # Prefer the first BYOK provider we have a key for.
        for prov in ("anthropic", "openai", "gemini", "kimi", "opencode-free"):
            if provider_keys.get(prov):
                if prov == "opencode-free":
                    return FREE_MODEL, FREE_PROVIDER
                return None, prov  # model chosen by Hermes for that provider
    return FREE_MODEL, FREE_PROVIDER

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
    # Also inject at process level as a fallback (free default or BYOK).
    if provider_keys:
        for prov, tok in provider_keys.items():
            ev = ENV_MAP.get(prov)
            if ev and tok:
                env[ev] = tok
    else:
        env.setdefault("HERMES_DEFAULT_PROVIDER", FREE_PROVIDER)
        env.setdefault("HERMES_DEFAULT_MODEL", FREE_MODEL)
    return subprocess.run(cmd, capture_output=capture, text=True, env=env, timeout=300)


def ensure_board(slug: str) -> bool:
    r = _run(["boards", "create", slug, "--description", f"FluxSwarm project {slug}"])
    r2 = _run(["boards", "ls"])
    return slug in r2.stdout


def launch_swarm(board: str, goal: str, provider_keys: Optional[dict] = None) -> SwarmResult:
    # Keys are passed via subprocess environment only (never written to disk).
    cleanup_profile_keys()
    worker_args = []
    for prof, disp, title, skills in SQUAD:
        worker_args += ["--worker", f"{prof}:{title}:{skills}"]
    cmd = ["swarm", goal] + worker_args + [
        "--verifier", f"{VERIFIER[0]}:{VERIFIER[1]}:{VERIFIER[2]}",
        "--synthesizer", f"{SYNTHESIZER[0]}:{SYNTHESIZER[1]}:{SYNTHESIZER[2]}",
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

    This is what fixes the 'blocked' issue: the dispatcher spawns workers using
    the profile's default model, which (without a key) fails and marks the card
    blocked. Pinning an explicit free model makes the worker run end-to-end.
    """
    model, provider = _resolve_runtime(provider_keys)
    tasks = list_tasks(board)
    for t in tasks:
        tid = t.get("id")
        if not tid:
            continue
        args = ["set-model", tid]
        if model:
            args.append(model)
        args += ["--provider", provider]
        _run(args, board=board, provider_keys=provider_keys, capture=True)


def launch_from_template(board: str, goal: str, agents: list[str],
                          provider_keys: Optional[dict] = None) -> SwarmResult:
    """Launch a squad built from a marketplace template's display-name agents.

    Each name in `agents` is resolved via AGENT_REGISTRY to (profile, skills, role).
    """
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

    cleanup_profile_keys()
    worker_args = []
    for prof, disp, title, skills in workers:
        worker_args += ["--worker", f"{prof}:{title}:{skills}"]
    cmd = ["swarm", goal] + worker_args + [
        "--verifier", f"{verifier[0]}:{verifier[1]}:{verifier[2]}",
        "--synthesizer", f"{synthesizer[0]}:{synthesizer[1]}:{synthesizer[2]}",
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


def dispatch(board: str, max_spawn: int = 8, dry_run: bool = False,
             provider_keys: Optional[dict] = None, blocking: bool = True,
             timeout_s: int = 600) -> dict:
    """Run the dispatcher. If blocking, poll until terminal state or timeout."""
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

    if blocking:
        # Keep dispatching in passes until everything is done/blocked or timeout.
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            tasks = list_tasks(board)
            states = [t.get("state") for t in tasks]
            if not states or all(s in ("done", "blocked") for s in states):
                break
            time.sleep(8)
            rr = _run(["dispatch", "--max", str(max_spawn)], board=board,
                      provider_keys=provider_keys)
        first["terminal"] = True
    return first


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
