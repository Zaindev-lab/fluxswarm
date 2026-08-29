"""Tests for the append-only audit trail truncation guard.

A single enormous meta value must be capped so the JSONL trail stays parseable
line-by-line for every downstream reader.
"""

import json

import audit


def _count_lines(path):
    text = path.read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if ln.strip()]


def test_audit_truncates_oversized_record(tmp_path):
    f = tmp_path / "audit.jsonl"
    audit.AUDIT_FILE = f
    # A multi-KB blob must be truncated, not crash, and still be valid JSON.
    big = "x" * 20000
    audit.audit("test.event", uid=1, outcome="ok", blob=big)
    lines = _count_lines(f)
    assert len(lines) == 1
    rec = json.loads(lines[0])
    # Either the full record (with trimmed blob) or the minimal fallback must be
    # present and VALID JSON — the whole point is the trail stays parseable.
    assert rec["event"] == "test.event"
    assert len(lines[0].encode("utf-8")) <= audit._MAX_RECORD_BYTES + 8


def test_audit_skips_sensitive_keys(tmp_path):
    f = tmp_path / "audit.jsonl"
    audit.AUDIT_FILE = f
    audit.audit("auth.login", uid=1, email="a@b.com", token="supersecretvalue")
    rec = json.loads(_count_lines(f)[0])
    assert "token" not in rec
    assert rec["email"] == "a@b.com"


def test_audit_normal_record(tmp_path):
    f = tmp_path / "audit.jsonl"
    audit.AUDIT_FILE = f
    audit.audit("project.create", uid=7, outcome="ok", slug="u7-1")
    rec = json.loads(_count_lines(f)[0])
    assert rec["slug"] == "u7-1"
    assert rec["uid"] == 7
