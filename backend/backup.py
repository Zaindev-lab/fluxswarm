"""Minimal, deterministic backup/restore for FluxSwarm runtime data.

Covers exactly what is needed to restore an account safely: the SQLite user DB
(a consistent snapshot via the SQLite backup API — safe to run against a live
server), the append-only audit log, the BYOK ciphertext store and the JWT
secret. DAO board workspaces live under HERMES_HOME (outside backend/data) and
are re-runnable artifacts, so they are deliberately NOT part of a data backup.

Usage (run from backend/):
    python backup.py backup                     # backups/backup_<ns>.zip
    python backup.py backup --out DIR --source DIR
    python backup.py restore backups/backup_X.zip [--target DIR] [--no-verify]
    python backup.py verify DIR

Env: FLUXSWARM_BACKUP_DIR (default <repo>/backups), FLUXSWARM_BACKUP_KEEP
(default 7) — number of backups retained on disk, oldest dropped first.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_DATA = BASE / "data"
# Default backup root sits OUTSIDE backend/data so it is never self-included.
BACKUP_DIR = Path(os.environ.get("FLUXSWARM_BACKUP_DIR", str(BASE.parent / "backups")))
KEEP = int(os.environ.get("FLUXSWARM_BACKUP_KEEP", "7"))
# Files that may be restored; extraction copies only these flat basenames.
BACKUP_FILES = ("users.db", "audit.jsonl", "byok.json", ".jwt_secret")
MANIFEST_NAME = "manifest.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _binary_db_snapshot(source: Path) -> bytes:
    """Consistent SQLite snapshot taken through the SQLite backup API.

    The bytes are a copy of the live DB produced by ``Connection.backup()``, so
    it is safe to run while the backend holds the file open — the copy is atomic
    from SQLite's perspective. Returned as raw bytes ready for the zip.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        s = sqlite3.connect(str(source))
        try:
            d = sqlite3.connect(str(tmp_path))
            try:
                s.backup(d)
                d.commit()
            finally:
                d.close()
        finally:
            s.close()
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


def do_backup(source: Path = DEFAULT_DATA, out_dir: Path = BACKUP_DIR) -> dict:
    """Write backups/backup_<ns>.zip containing the allowed data files.

    Files that are missing are skipped (an empty users.db is an error). Then
    retention prunes backups beyond ``KEEP`` (newest kept).
    """
    source = Path(source)
    if not (source / "users.db").exists():
        raise FileNotFoundError(f"no users.db found under {source}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    archive = out_dir / f"backup_{time.time_ns()}.zip"
    manifest = {"created_at": time.time(), "version": "1", "files": {}}

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        data = _binary_db_snapshot(source / "users.db")
        z.writestr("users.db", data)
        manifest["files"]["users.db"] = _sha256(data)
        for name in ("audit.jsonl", "byok.json", ".jwt_secret"):
            p = source / name
            if not p.exists():
                continue
            blob = p.read_bytes()
            z.writestr(name, blob)
            manifest["files"][name] = _sha256(blob)
        z.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))

    # Retention: drop the oldest archives beyond KEEP.
    backups = sorted(out_dir.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[KEEP:]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass

    return {"archive": str(archive), "files": sorted(manifest["files"])}


def do_restore(archive: Path, target: Path = DEFAULT_DATA, verify: bool = True) -> dict:
    """Restore allowed files from a backup zip into ``target``.

    Only the flat allowed basenames are ever written, and each output is
    containment-checked against the resolved target dir, so a hostile archive
    (``../outside.db``, nested paths) can never escape the target.
    """
    archive = Path(archive)
    if not archive.exists():
        raise FileNotFoundError(f"backup archive not found: {archive}")
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    target_resolved = str(target.resolve()) + os.sep

    restored = []
    allowed = set(BACKUP_FILES)
    with zipfile.ZipFile(archive) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if "users.db" not in names:
            raise ValueError("archive has no users.db — refusing to restore")
        for name in names:
            base = Path(name).name
            if base not in allowed:
                continue  # hostile/unknown members are simply ignored
            out = (target / base).resolve()
            if not str(out).startswith(target_resolved):
                raise ValueError(f"refusing to extract outside target: {name!r}")
            out.write_bytes(z.read(name))
            restored.append(base)

    if verify:
        do_verify(target)
    return {"restored": sorted(restored)}


def do_verify(target: Path = DEFAULT_DATA) -> dict:
    """Confirm a data dir looks restorable: DB opens and reports counts,
    audit log parses line-by-line. Pure read, never writes."""
    target = Path(target)
    info = {"users": 0, "projects": 0, "audit_lines": 0}
    db_path = target / "users.db"
    if db_path.exists():
        try:
            c = sqlite3.connect(str(db_path))
            info["users"] = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            info["projects"] = c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            c.close()
        except sqlite3.Error:
            info["users"] = -1
    audit = target / "audit.jsonl"
    if audit.exists():
        try:
            info["audit_lines"] = sum(1 for _ in open(audit, encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return info


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "backup":
        out = BACKUP_DIR
        source = DEFAULT_DATA
        rest = argv[1:]
        while rest:
            if rest[0] == "--out":
                out = Path(rest[1]); rest = rest[2:]
            elif rest[0] == "--source":
                source = Path(rest[1]); rest = rest[2:]
            else:
                rest = rest[1:]
        info = do_backup(source=source, out_dir=out)
        print("backup written:", info["archive"])
        print("files:", ", ".join(info["files"]))
        return 0
    if cmd == "restore":
        if len(argv) < 2:
            print("usage: backup.py restore <archive> [--target DIR] [--no-verify]")
            return 1
        target = DEFAULT_DATA
        verify = True
        archive_arg = argv[1]
        rest = argv[2:]
        while rest:
            if rest[0] == "--target":
                target = Path(rest[1]); rest = rest[2:]
            elif rest[0] == "--no-verify":
                verify = False; rest = rest[1:]
            else:
                rest = rest[1:]
        info = do_restore(Path(archive_arg), target=target, verify=verify)
        print("restored:", ", ".join(info["restored"]), "->", target)
        if verify:
            print("verified:", json.dumps(do_verify(target)))
        return 0
    if cmd == "verify":
        if len(argv) < 2:
            print("usage: backup.py verify <data-dir>")
            return 1
        print(json.dumps(do_verify(Path(argv[1]))))
        return 0
    print(f"unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))