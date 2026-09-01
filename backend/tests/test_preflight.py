"""Gate 2 — Hermes runtime preflight diagnostics.

A missing / misconfigured Hermes install must produce an actionable error
instead of a raw FileNotFoundError or confusing KeyError; launches fail fast
before the caller pays for a broken board.
"""
from __future__ import annotations

import pytest
from pathlib import Path

import hermes_client as hc_mod


def test_preflight_reports_missing_binary(monkeypatch):
    monkeypatch.setattr(hc_mod, "HERMES_BIN", Path("Z:/does/not/exist/hermes.exe"))
    problems = hc_mod.preflight()
    assert any("Hermes binary not found" in p for p in problems)

    with pytest.raises(RuntimeError) as ei:
        hc_mod._raise_preflight()
    assert "Hermes binary not found" in str(ei.value)


def test_preflight_reports_missing_profiles(monkeypatch, tmp_path):
    # Valid binary, but the profiles tree is absent -> profiles listed.
    fake_bin = tmp_path / "hermes.exe"
    fake_bin.write_text("x")
    monkeypatch.setattr(hc_mod, "HERMES_BIN", fake_bin)
    monkeypatch.setattr(hc_mod, "PROFILES_DIR", tmp_path / "profiles")

    problems = hc_mod.preflight()
    assert any("profiles dir not found" in p for p in problems)

    # Iterate: missing individual squad profiles are called out too.
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "ecc-planner").mkdir()  # only one profile present
    problems = hc_mod.preflight()
    assert any("squad profile missing" in p and "ecc-architect" in p for p in problems)


def test_launch_swarm_fails_fast_with_clear_message(monkeypatch):
    monkeypatch.setattr(hc_mod, "HERMES_BIN", Path("Z:/nope/hermes.exe"))
    with pytest.raises(RuntimeError) as ei:
        hc_mod.launch_swarm("u1-proj", "goal")
    assert "Hermes runtime not ready" in str(ei.value)


def test_ensure_board_fails_fast_with_clear_message(monkeypatch):
    monkeypatch.setattr(hc_mod, "HERMES_BIN", Path("Z:/nope/hermes.exe"))
    with pytest.raises(RuntimeError) as ei:
        hc_mod.ensure_board("u1-proj")
    assert "Hermes runtime not ready" in str(ei.value)


def test_preflight_all_ok(monkeypatch, tmp_path):
    fake_bin = tmp_path / "hermes.exe"
    fake_bin.write_text("x")
    monkeypatch.setattr(hc_mod, "HERMES_BIN", fake_bin)

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    for prof in hc_mod._squad_profiles():
        (profiles / prof).mkdir()

    monkeypatch.setattr(hc_mod, "PROFILES_DIR", profiles)
    assert hc_mod.preflight() == []