"""Gate-4 test E2E driver — Candidate C validation on an ISOLATED Hermes home.

Drives the SAME control flow production uses (backend/main.py._bg_dispatch):
  ensure_board(slug) -> launch_swarm(slug, goal) -> dispatch(slug, blocking=True)

Isolation guarantees:
  * HERMES_HOME points at a throwaway TEMP root (never the production home), so
    every board/board-DB/workspace/log created lands under TEMP.
  * HERMES_BIN stays at the real hermes.exe (read-only; provider plumbing is
    global in the agent install, so opencode-free keeps working).
  * Profile configs in TEMP have skills.external_dirs rewritten to TEMP paths.
  * Worker spawn argv is captured LIVE (WMI) during dispatch for the "worker
    command contains -m nemotron-3-ultra-free --provider opencode-free" check.

Usage:
  python gate4_e2e.py <e2e_index 1|2|3> <temp_home> <evidence_dir>
Exit code 0 == SUCCESS E2E; evidence JSON written to <evidence_dir>/e2e<N>.json.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

E2E_GOALS = {
    1: "Create a Python CLI todo app (add/list/done subcommands) with pytest unit tests and a README.md.",
    2: "Build a minimal Flask app exposing GET /hello returning JSON, with pytest tests and a Dockerfile.",
    3: "Write a Python package 'texttools' with two string utility functions, pytest tests, and a GitHub Actions CI workflow.",
}

FREE_MODEL = "nemotron-3-ultra-free"
FREE_PROVIDER = "opencode-free"
FORBIDDEN = ("openrouter", "z-ai", "glm-5.2", "anthropic", "openai", "gemini", "kimi")
REAL_HOME = Path(os.environ.get("REAL_HERMES_HOME", r"C:\Users\DELL\AppData\Local\hermes"))
REAL_BIN = Path(os.environ.get("REAL_HERMES_BIN", r"C:\Users\DELL\AppData\Local\hermes\bin\hermes.exe"))
BACKEND = Path(os.environ.get("FLUXSWARM_BACKEND", r"C:\Users\DELL\fluxswarm\backend"))
VERIFIER_SKILL = "requesting-code-review"


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


def install_path_guard(temp_home: Path) -> Path:
    """Return a PATH dir that SHADOWS 'pip'/'uv' so worker tool actions cannot
    write into the shared hermes-agent venv. pip is forwarded to the real venv
    python but PIP_TARGET (set in main) redirects any install to a TEMP target;
    uv/pipx are blocked (their installs would target the shared venv)."""
    guard = temp_home.parent / ("pc_guard_" + temp_home.name)
    guard.mkdir(parents=True, exist_ok=True)
    venv_python = REAL_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    shims = {
        "pip.cmd": f"@echo off\r\n@{venv_python} -m pip %*\r\n",
        "pip3.cmd": f"@echo off\r\n@{venv_python} -m pip %*\r\n",
        "uv.cmd": "@echo off\r\n@echo [e2e-guard] uv is disabled during E2E to protect the shared venv; use pip. >&2\r\n",
        "pipx.cmd": "@echo off\r\n@echo [e2e-guard] pipx is disabled during E2E to protect the shared venv. >&2\r\n",
    }
    for name, content in shims.items():
        p = guard / name
        if not p.exists():
            try:
                p.write_text(content, encoding="utf-8")
            except OSError:
                log(f"warning: could not write shim {name}")
    return guard


def site_packages_dir() -> Path:
    return REAL_HOME / "hermes-agent" / "venv" / "Lib" / "site-packages"


def force_rmtree(root: Path) -> None:
    """Remove a tree even when a worker created reserved-name files (e.g. a
    literal 'nul'), which plain shutil.rmtree cannot delete on Windows."""
    if not root.exists():
        return
    try:
        shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass
    if not root.exists():
        return
    # Windows extended-length-namespace fallback handles reserved device names.
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Remove-Item -LiteralPath \"\\\\?\\{root}\" -Recurse -Force"],
            capture_output=True, timeout=120)
    except Exception as exc:
        log(f"warning: force-rmtree failed for {root}: {exc}")
    if root.exists():
        # Last resort: an uncorrectable ignore_errors shutil pass.
        try:
            shutil.rmtree(root, ignore_errors=True)
        except Exception as exc:
            log(f"warning: rmtree retry failed for {root}: {exc}")


def venv_baseline() -> dict[str, tuple[int, int]]:
    """relpath -> (mtime_ms, size) for every file under the shared venv so a
    post-run diff can prove the E2E wrote nothing into it."""
    base: dict[str, tuple[int, int]] = {}
    sp = site_packages_dir()
    for p in sp.rglob("*"):
        if p.is_file():
            try:
                st = p.stat()
                base[str(p.relative_to(sp)).replace("\\", "/")] = (
                    int(st.st_mtime * 1000), st.st_size)
            except OSError:
                pass
    return base


def venv_delta(base: dict[str, tuple[int, int]], start_epoch: float, limit: int = 200):
    """Files under the shared venv that were created/rewritten during the run."""
    sp = site_packages_dir()
    touched_nonpyc: list[str] = []
    pyc_count = 0
    for p in sp.rglob("*"):
        if not p.is_file():
            continue
        try:
            st = p.stat()
            if not st.st_mtime >= start_epoch:
                continue
        except OSError:
            continue
        rel = str(p.relative_to(sp)).replace("\\", "/")
        prev = base.get(rel)
        changed = prev is None or (int(st.st_mtime * 1000), st.st_size) != prev
        if not changed:
            continue
        if rel.endswith(".pyc") or "__pycache__" in rel:
            pyc_count += 1
        elif len(touched_nonpyc) < limit:
            touched_nonpyc.append(rel)
    return {"non_pyc": touched_nonpyc, "pyc_count": pyc_count}


def tree_digest(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def setup_isolated_home(temp_home: Path) -> None:
    """Assemble a faithful-but-isolated Hermes data home."""
    if (temp_home / ".isolated_ready").exists():
        log(f"isolated home ready at {temp_home}")
        return
    log(f"assembling isolated home at {temp_home}")
    profiles = REAL_HOME / "profiles"
    skills = REAL_HOME / "skills"

    # --- select profile files to copy (config/skills/behavior only) ----------
    profile_files = ("config.yaml", "profile.yaml", ".env", ".no-bundled-skills",
                     "SOUL.md", "auth.json", "auth.lock", "models_dev_cache.json",
                     "models_dev_cache.etag")
    kept = 0
    for prof_dir in sorted(profiles.iterdir()):
        if not prof_dir.is_dir():
            continue
        dst = temp_home / "profiles" / prof_dir.name
        dst.mkdir(parents=True, exist_ok=True)
        for fn in profile_files:
            src = prof_dir / fn
            if src.exists():
                shutil.copy2(src, dst / fn)
        local = prof_dir / "skills"
        if local.is_dir():
            shutil.copytree(local, dst / "skills", dirs_exist_ok=True)
        kept += 1
    log(f"copied {kept} profiles")

    # --- skills collection (bundled software-development + ecc external) -----
    if skills.is_dir():
        shutil.copytree(skills, temp_home / "skills", dirs_exist_ok=True)

    # --- root config + caches ------------------------------------------------
    for fn in ("config.yaml", "SOUL.md", ".env",
               "context_length_cache.yaml", "models_dev_cache.json",
               "provider_models_cache.json", "models_dev_cache.etag"):
        src = REAL_HOME / fn
        if src.exists():
            shutil.copy2(src, temp_home / fn)

    # --- rewrite profile skills.external_dirs to the isolated home -----------
    prod_ext_prefix = REAL_HOME.as_posix()
    patched = 0
    for yaml_path in (temp_home / "profiles").rglob("config.yaml"):
        try:
            txt = yaml_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if prod_ext_prefix in txt:
            new = txt.replace(prod_ext_prefix, temp_home.as_posix())
            yaml_path.write_text(new, encoding="utf-8")
            patched += 1
    log(f"rewrote external_dirs on {patched} profile(s)")
    (temp_home / ".isolated_ready").write_text(time.ctime(), encoding="utf-8")
    log("isolated home complete")


class ArgvCapture:
    """Live capture of worker spawn argv during dispatch (Windows WMI)."""

    def __init__(self, match_hints: tuple[str, ...]):
        self._hints = match_hints
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.captured: list[dict] = []

    def _poll_once(self) -> list[str]:
        ps = ("$c = Get-CimInstance Win32_Process -Filter \"Name='hermes.exe'\" "
              "| Where-Object { $_.CommandLine }; "
              "if ($c) { $c | ForEach-Object { $_.CommandLine } } else { '' }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=20)
            lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
            return [ln for ln in lines if any(h in ln for h in self._hints)]
        except Exception:
            return []

    def _run(self) -> None:
        n = 0
        while not self._stop.is_set():
            try:
                for ln in self._poll_once():
                    self.captured.append({"at": round(time.time(), 3), "argv": ln})
            except Exception:
                pass
            time.sleep(4)
            n += 1
            if n % 30 == 0:
                log(f"dispatch still running; spawn argv captured so far: {len(self.captured)}")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)


def db_rows(board: str, temp_home: Path) -> list[dict]:
    db = temp_home / "kanban" / "boards" / board / "kanban.db"
    c = sqlite3.connect(str(db))
    try:
        cur = c.execute(
            "SELECT id,title,assignee,status,model_override,provider_override,skills,"
            "worker_pid,current_run_id,last_failure_error FROM tasks"
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        c.close()


def main() -> int:
    index = int(sys.argv[1])
    temp_home = Path(sys.argv[2])
    evidence_dir = Path(sys.argv[3])
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # HERMES_HOME is set at user level to the production home; the driver MUST
    # force the isolated temp home so no production data can be touched.
    os.environ["HERMES_HOME"] = str(temp_home)
    os.environ["FLUXSWARM_HERMES_BIN"] = str(REAL_BIN)

    # Isolate worker tool installs (pip/uv) from the SHARED hermes-agent venv:
    # shadow pip/uv on PATH and redirect installs into a TEMP target that is
    # also importable (PYTHONPATH), so the E2E cannot pollute the venv again.
    guard = install_path_guard(temp_home)
    pip_target = temp_home.parent / ("pip_target_" + temp_home.name)
    pip_target.mkdir(parents=True, exist_ok=True)
    os.environ["PIP_TARGET"] = str(pip_target)
    prev_pp = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(pip_target) + (os.pathsep + prev_pp if prev_pp else "")
    os.environ["PATH"] = str(guard) + os.pathsep + os.environ.get("PATH", "")
    log(f"path-guard={guard} pip-target={pip_target}")

    setup_isolated_home(temp_home)
    if index == 0:
        log(f"setup complete: {temp_home}")
        return 0

    evidence: dict = {"e2e_index": index, "goal": E2E_GOALS[index], "temp_home": str(temp_home)}

    os.environ["FLUXSWARM_ALLOW_MULTI"] = "1"
    sys.path.insert(0, str(BACKEND))

    import hermes_client as hc
    assert str(Path(hc.HERMES_HOME).resolve()) == str(temp_home.resolve()), (
        "driver must run with HERMES_HOME=temp; got " + hc.HERMES_HOME)

    slug = f"u4-gate4-candidatec-e2e{index}"
    evidence["slug"] = slug

    # --- single-launch guard: a stale scratch board MUST NOT double the swarm ---
    # The first E2E run accidentally double-launched on this slug (its rerun the
    # night before had crashed *after* launching), producing 8 concurrent free
    # tier workers. From now on: wipe any residual board and refuse to continue
    # if it cannot be removed.
    board_dir = temp_home / "kanban" / "boards" / slug
    if board_dir.exists():
        log(f"removing stale scratch board {slug}")
        force_rmtree(board_dir)
    assert not board_dir.exists(), f"could not clear scratch board {board_dir}"

    # Kill any residual kanban-task workers that belong to THIS slug only
    # (production boards are live and must never be touched).
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='hermes.exe'\" "
          "| Where-Object { $_.CommandLine -match 'kanban task' -and "
          f"$_.CommandLine -match '{slug}' }}"
          " | ForEach-Object { $_.ProcessId }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=20)
        for pid in [ln.strip() for ln in r.stdout.splitlines() if ln.strip().isdigit()]:
            log(f"killing residual {slug} worker pid={pid}")
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Stop-Process -Id {pid} -Force"],
                           capture_output=True, timeout=15)
    except Exception as exc:
        log(f"warning: residual-worker sweep failed: {exc}")

    evidence["rerun_meta"] = {
        "clean_single_launch": True,
        "path_guard": str(guard),
        "pip_target": str(pip_target),
        "max_spawn": int(os.environ.get("E2E_MAX_SPAWN", "4")),
        "timeout_s": int(os.environ.get("E2E_TIMEOUT_S", "3600")),
    }
    venv_before = venv_baseline()
    start_epoch = time.time() - 30

    # --- provisioning pre-flight proof (bundled -> ecc skill scope) ----------
    prov_log = {"bundled_source": None, "target_exists_after": None}
    src = Path(hc.HERMES_HOME) / "skills" / "software-development" / VERIFIER_SKILL
    prov_log["bundled_source"] = src.is_dir() and (src / "SKILL.md").exists()
    evidence["provision"] = prov_log

    # --- launch (production control flow) ------------------------------------
    start = time.time()
    hc.ensure_board(slug)
    swarm = hc.launch_swarm(slug, E2E_GOALS[index], provider_keys={"opencode-free": "free"})
    evidence["swarm"] = {
        "root": swarm.root_id, "workers": swarm.worker_ids,
        "verifier": swarm.verifier_id, "synthesizer": swarm.synthesizer_id,
    }
    prov_target = (Path(hc.HERMES_HOME) / "skills" / "ecc" / "skills" / VERIFIER_SKILL)
    prov_log["target_exists_after"] = prov_target.is_dir()
    if prov_target.is_dir():
        prov_log["byte_identical_to_bundled"] = (
            tree_digest(prov_target) == tree_digest(src))

    tasks_after_launch = db_rows(slug, temp_home)
    evidence["pinning_after_launch"] = [
        {k: t[k] for k in ("id", "assignee", "model_override", "provider_override")}
        for t in sorted(tasks_after_launch, key=lambda r: r["id"])
    ]

    # --- dispatch + live spawn-argv capture ----------------------------------
    cv = ArgvCapture(("kanban task", "-p ecc-", slug))
    cv.start()
    try:
        res = hc.dispatch(slug, max_spawn=int(os.environ.get("E2E_MAX_SPAWN", "4")),
                          blocking=True,
                          provider_keys={"opencode-free": "free"},
                          timeout_s=int(os.environ.get("E2E_TIMEOUT_S", "3600")))
        evidence["dispatch"] = {
            "outcome": res.get("outcome"), "terminal": res.get("terminal"),
            "timed_out": res.get("timed_out"), "stall": res.get("stall"),
            "stuck_tasks": res.get("stuck_tasks"), "raw": res.get("raw"),
        }
    finally:
        cv.stop()
    evidence["spawn_argv"] = cv.captured
    evidence["dispatch_wall_s"] = round(time.time() - start, 1)
    evidence["shared_venv_writes"] = venv_delta(venv_before, start_epoch)

    with (evidence_dir / f"e2e{index}.json").open("w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2)
    log(json.dumps({"e2e": index, "dispatch": evidence["dispatch"]}))
    return 0 if res.get("outcome") == "ok" and res.get("terminal") else 1


if __name__ == "__main__":
    sys.exit(main())