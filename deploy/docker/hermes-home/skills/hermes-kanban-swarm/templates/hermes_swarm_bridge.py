"""
Hermes kanban swarm bridge — drop-in backend helper.

Drives `hermes kanban` as a subprocess so an external app (SaaS/web UI) can
launch and monitor a multi-agent squad. Verified working on Windows with the
Hermes agent venv interpreter (`python`, not `python3`).

Usage:
    from hermes_swarm_bridge import launch_project, list_tasks
    s = launch_project("flux-todo-store", "Build a FastAPI app with tests + CI")
    print(s.worker_ids)
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

HERMES_BIN = os.environ.get(
    "HERMES_BIN", "C:/Users/DELL/AppData/Local/hermes/bin/hermes.exe"
)
HERMES_HOME = os.environ.get(
    "HERMES_HOME", "C:/Users/DELL/AppData/Local/hermes"
)

# Default squad: 4 parallel workers -> verifier -> synthesizer.
SQUAD = [
    ("ecc-planner", "Plan the feature breakdown", "planning"),
    ("ecc-architect", "Design service architecture", "api-design"),
    ("ecc-devops", "Set up CI/CD and containerization", "docker-patterns,deployment-patterns"),
    ("ecc-tdd", "Write the test suite", "tdd-guide"),
]
VERIFIER = ("ecc-reviewer", "Review code quality", "code-review")
SYNTHESIZER = ("ecc-build-fixer", "Assemble and make it green", "orch-build-mvp")


@dataclass
class SwarmResult:
    root_id: str
    worker_ids: list
    verifier_id: str
    synthesizer_id: str


def _run(args, board=None, timeout=120):
    cmd = [HERMES_BIN, "kanban"]
    if board:
        cmd += ["--board", board]
    cmd += args
    env = dict(os.environ)
    env["HERMES_HOME"] = HERMES_HOME
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def ensure_board(slug: str) -> None:
    _run(["boards", "create", slug, "--description", f"FluxSwarm project {slug}"])


def launch_project(board: str, goal: str) -> SwarmResult:
    ensure_board(board)
    worker_args = []
    for prof, title, skills in SQUAD:
        worker_args += ["--worker", f"{prof}:{title}:{skills}"]
    cmd = ["swarm", goal] + worker_args + [
        "--verifier", f"{VERIFIER[0]}:{VERIFIER[1]}:{VERIFIER[2]}",
        "--synthesizer", f"{SYNTHESIZER[0]}:{SYNTHESIZER[1]}:{SYNTHESIZER[2]}",
        "--created-by", "fluxswarm", "--json",
    ]
    r = _run(cmd, board=board)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    d = json.loads(r.stdout)
    return SwarmResult(d["root_id"], d.get("worker_ids", []), d["verifier_id"], d["synthesizer_id"])


def dispatch(board: str, max_spawn: int = 8, dry_run: bool = False) -> dict:
    args = ["dispatch"]
    if dry_run:
        args.append("--dry-run")
    args += ["--max", str(max_spawn)]
    r = _run(args, board=board)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"raw": r.stdout.strip()}


def list_tasks(board: str) -> list:
    r = _run(["list", "--json"], board=board)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    norm = {"done": "done", "running": "running", "ready": "queued", "todo": "queued", "blocked": "blocked"}
    for t in data:
        t["state"] = norm.get(t.get("status", ""), t.get("status", "unknown"))
    return data
