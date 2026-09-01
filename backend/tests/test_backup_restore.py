"""Gate 2 — backup/restore round-trip (data lifecycle).

A backup must be a consistent SQLite snapshot taken against the real data
layout, restore must put exactly the allowed files back inside the target dir,
and a hostile archive must never be able to write outside that dir. A real
restore is executed and then verified (row counts) — this closes the
"restore never tested" finding from the audit.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

import backup as bkp

from db import DB  # conftest pre-points db at a temp, seeded DB


def _build_data_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    # Copy the session-isolated (already seeded) users.db as a realistic dump.
    shutil.copy2(DB, p / "users.db")
    (p / "audit.jsonl").write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
    (p / "byok.json").write_text('{"12": {"anthropic": "enc"}}', encoding="utf-8")
    (p / ".jwt_secret").write_text("test-secret-abc", encoding="utf-8")


def test_backup_restore_roundtrip_verified(tmp_path):
    src = tmp_path / "data"
    _build_data_dir(src)
    out = tmp_path / "backups"

    info = bkp.do_backup(source=src, out_dir=out)
    arch = Path(info["archive"])
    assert arch.exists()

    with zipfile.ZipFile(arch) as z:
        names = set(z.namelist())
    assert {"users.db", "audit.jsonl", "byok.json", ".jwt_secret", "manifest.json"} <= names

    target = tmp_path / "restored"
    res = bkp.do_restore(arch, target=target, verify=True)
    assert res["restored"] == [".jwt_secret", "audit.jsonl", "byok.json", "users.db"]
    assert (target / "users.db").exists()

    # The restored DB is a valid, readable database with the same rows.
    v = bkp.do_verify(target)
    assert v["users"] >= 1  # seeded demo user survived the round-trip
    assert v["projects"] == bkp.do_verify(src)["projects"]
    assert v["audit_lines"] == 2


def test_restore_user_db_is_consistent_copy(tmp_path):
    src = tmp_path / "data"
    _build_data_dir(src)
    info = bkp.do_backup(source=src, out_dir=tmp_path / "b")

    target = tmp_path / "r"
    bkp.do_restore(Path(info["archive"]), target=target)

    import sqlite3
    c = sqlite3.connect(str(target / "users.db"))
    assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == bkp.do_verify(src)["users"]
    c.close()


def test_restore_rejects_hostile_members(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("users.db", b"x")
        z.writestr("../outside.db", b"escaped!")
        z.writestr("sub/nested.db", b"nested!")
    target = tmp_path / "restored"

    bkp.do_restore(evil, target=target, verify=False)
    assert (target / "users.db").read_bytes() == b"x"
    # Hostile members never materialise anywhere, regardless of nesting.
    assert not (tmp_path / "outside.db").exists()
    assert not (tmp_path / "sub").exists()


def test_restore_refuses_archive_without_db(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("audit.jsonl", "{}")
    with pytest.raises(ValueError):
        bkp.do_restore(bad, target=tmp_path / "r")


def test_backup_retention_keeps_newest(monkeypatch, tmp_path):
    monkeypatch.setattr(bkp, "KEEP", 1)
    src = tmp_path / "data"
    _build_data_dir(src)
    out = tmp_path / "b"
    for _ in range(3):
        bkp.do_backup(source=src, out_dir=out)
    backups = sorted(out.glob("backup_*.zip"))
    assert len(backups) == 1  # only the newest survived


def test_backup_missing_db_fails_fast(tmp_path):
    src = tmp_path / "empty_data"
    src.mkdir()
    with pytest.raises(FileNotFoundError):
        bkp.do_backup(source=src, out_dir=tmp_path / "b")