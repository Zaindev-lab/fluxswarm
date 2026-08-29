"""Single-instance guard for the FluxSwarm backend (Windows & POSIX).

Acquires an exclusive lock file (atomic O_CREAT|O_EXCL) recording the pid.
A stale lock left by a dead process is reclaimed once. A second live instance
exits with an error instead of fighting for the same port.

Bypass only for debugg/dev with FLUXSWARM_ALLOW_MULTI=1.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOCK = BASE / "data" / ".server.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire() -> None:
    if os.environ.get("FLUXSWARM_ALLOW_MULTI", "").strip().lower() in ("1", "true", "yes"):
        return
    LOCK.parent.mkdir(exist_ok=True)
    for attempt in (0, 1):
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            return
        except FileExistsError:
            stale = 0
            try:
                stale = int(LOCK.read_text().strip() or "0")
            except (OSError, ValueError):
                stale = 0
            if attempt == 0 and stale and not _pid_alive(stale):
                try:
                    LOCK.unlink()
                    continue
                except OSError:
                    pass
            print(f"FluxSwarm is already running (pid {stale}) — a second instance refused.",
                  file=sys.stderr)
            sys.exit(1)
    sys.exit(1)


def release() -> None:
    try:
        LOCK.unlink()
    except OSError:
        pass