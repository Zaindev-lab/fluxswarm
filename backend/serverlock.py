"""Single-instance guard for the FluxSwarm backend (Windows & POSIX).

On POSIX an exclusive advisory flock is used: the operating system releases it
automatically when the process dies (crash, kill, exit), so a stale lock can
never wedge the port. On Windows (no fcntl) it falls back to an atomic
O_CREAT|O_EXCL lock file whose pid is reclaimed once when stale.

Bypass only for debug/dev with FLUXSWARM_ALLOW_MULTI=1.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOCK = Path(os.environ.get("FLUXSWARM_LOCK_FILE") or (BASE / "data" / ".server.lock"))

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None

_FLOCK_FD: int | None = None


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


def _acquire_flock() -> None:
    """POSIX flock path: lock auto-releases when the process exits/crashes."""
    global _FLOCK_FD
    fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR)
    try:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError:
            try:
                stale = int(LOCK.read_text().strip() or "0")
            except (OSError, ValueError):
                stale = 0
            os.close(fd)
            print(f"FluxSwarm is already running (pid {stale}) — a second instance refused.",
                  file=sys.stderr)
            sys.exit(1)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        _FLOCK_FD = fd
    except BaseException:
        os.close(fd)
        raise


def acquire() -> None:
    if os.environ.get("FLUXSWARM_ALLOW_MULTI", "").strip().lower() in ("1", "true", "yes"):
        return
    LOCK.parent.mkdir(exist_ok=True)
    if _fcntl is not None:
        _acquire_flock()
        return
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
    global _FLOCK_FD
    if _FLOCK_FD is not None:
        try:
            os.close(_FLOCK_FD)  # closing releases the flock
        except OSError:
            pass
        _FLOCK_FD = None
    try:
        LOCK.unlink()
    except OSError:
        pass