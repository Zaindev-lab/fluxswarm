"""Phase F: provider-usage accounting ledger — hermetic tests.

Covers the sqlite ledger layer (record -> pending -> finalized outcome via the
unique board slug) and the demo-launch integration path (a pool-picked demo
launch must appear in the ledger with runtime_source="pool"/provider probe key,
while an unavailable pool must record nothing). No network or provider probe is
touched: main is thin-stubbed exactly like the other hermetic suites.
"""
from __future__ import annotations

import time

import db

import main as main_mod


def _usage_rows(slug: str, runtime_source: str | None = None) -> list[dict]:
    c = db._conn()
    try:
        if runtime_source:
            rows = c.execute(
                "SELECT * FROM provider_usage WHERE slug=? AND runtime_source=? ORDER BY id",
                (slug, runtime_source)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM provider_usage WHERE slug=? ORDER BY id", (slug,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ---------------------------------------------------------------------------
# sqlite ledger layer
# ---------------------------------------------------------------------------
class TestDbLedger:
    def test_record_inserts_and_counts_as_pending(self):
        rid = db.record_provider_usage(
            "demo", "paid_fallback", "gemini", "gemini-1.5-flash", slug="flux-demo-t")
        assert rid >= 1
        s = db.provider_usage_summary()
        assert s["total_attempts"] >= 1
        assert s["pending_attempts"] >= 1
        assert s["by_runtime_source"]["paid_fallback"]["attempts"] >= 1
        assert s["by_surface"]["demo"]["attempts"] >= 1

    def test_update_finalizes_latest_attempt(self):
        db.record_provider_usage("project", "byok", "anthropic", "claude", slug="u1-p")
        assert db.update_provider_usage_outcome("u1-p", ok=1) is True
        rows = _usage_rows("u1-p")
        assert rows[0]["ok"] == 1
        s = db.provider_usage_summary()
        assert s["finalized"] >= 1 and s["pending_attempts"] >= 0
        # second update no-ops: no open row remains
        assert db.update_provider_usage_outcome("u1-p", ok=0) is False

    def test_update_only_touches_latest_open_row(self):
        db.record_provider_usage("project", "byok", "kimi", "moonshot", slug="u1-p2")
        db.record_provider_usage("project", "byok", "kimi", "moonshot", slug="u1-p2")
        assert db.update_provider_usage_outcome("u1-p2", ok=1) is True
        rows = _usage_rows("u1-p2")
        assert [r["ok"] for r in rows] == [None, 1]

    def test_update_noop_when_no_open_row(self):
        assert db.update_provider_usage_outcome("no-such-slug", ok=1) is False

    def test_created_at_and_day_stored(self):
        db.record_provider_usage("demo", "pool", "gemini", "m", slug="flux-demo-t2")
        row = _usage_rows("flux-demo-t2")[0]
        assert row["day"] == time.strftime("%Y-%m-%d")
        assert row["created_at"] > 0

    def test_summary_ok_and_not_ok_counts(self):
        db.record_provider_usage("demo", "pool", "gemini", "m", slug="ok-a")
        db.record_provider_usage("demo", "paid_fallback", "openai", "gpt-5", slug="ok-b")
        db.record_provider_usage("demo", "pool", "gemini", "m", slug="bad-a")
        db.update_provider_usage_outcome("ok-a", ok=1)
        db.update_provider_usage_outcome("bad-a", ok=0)
        s = db.provider_usage_summary()
        assert s["ok"] >= 1
        assert s["not_ok"] >= 1
        assert s["finalized"] == s["ok"] + s["not_ok"]


# ---------------------------------------------------------------------------
# demo-launch integration (ledger wiring in api_demo_launch)
# ---------------------------------------------------------------------------
class TestDemoLedger:
    def _smoke_demo(self, monkeypatch, pick):
        monkeypatch.setattr(main_mod.provider_pool, "pick_demo_provider", pick)
        # Hermetic quota state: THIS class only asserts the ledger rows, NEVER
        # the quota gates (those are covered by dedicated tests). A shared
        # in-memory per-IP / global / durable counter exhausted by earlier tests
        # in the same session must not 429 these launches under the "quota runs
        # only after a provider resolves" rule.
        seq = {"n": 0}

        def _uniq_ip(request):
            seq["n"] += 1
            return f"10.200.{seq['n'] // 240}.{seq['n'] % 240}"

        monkeypatch.setattr(main_mod, "_client_ip", _uniq_ip)
        monkeypatch.setattr(main_mod.limiter, "check", lambda *a, **k: True)
        monkeypatch.setattr(main_mod.db, "bump_demo_usage", lambda *a, **k: 1)
        monkeypatch.setattr(main_mod.hc, "ensure_board", lambda slug: True)

        def fake_launch(board, goal, provider_keys=None, provider=None, model=None):
            return type("S", (), {"root_id": "r1", "worker_ids": [],
                                  "verifier_id": "v", "synthesizer_id": "s"})()

        monkeypatch.setattr(main_mod.hc, "launch_swarm", fake_launch)
        monkeypatch.setattr(main_mod, "_fire_dispatch", lambda *a, **k: None)
        return main_mod.api_demo_launch(None)

    def test_pool_pick_records_usage_with_probe_key(self, monkeypatch):
        pick = {"provider": "google", "model": "gemini-1.5-flash", "requires_key": False}
        resp = self._smoke_demo(monkeypatch, lambda: pick)
        assert resp["demo"] is True
        rows = _usage_rows(resp["slug"], runtime_source="pool")
        assert len(rows) == 1
        row = rows[0]
        assert row["surface"] == "demo"
        assert row["runtime_source"] == "pool"
        assert row["provider"] == "gemini"     # resolve_provider_key("google")
        assert row["model"] == "gemini-1.5-flash"
        assert row["ok"] is None               # pending until _bg_dispatch finalizes

    def test_pool_unavailable_records_nothing(self, monkeypatch):
        resp = self._smoke_demo(monkeypatch, lambda: None)
        assert resp["error"] == "demo_provider_unavailable"
        assert _usage_rows("any") == []

    def test_paid_fallback_records_source(self, monkeypatch):
        # Operator opted into a paid last-resort runtime.
        monkeypatch.setenv("FLUXSWARM_PAID_FALLBACK_ENABLED", "1")

        def fake_default(*a, **k):
            return ("claude-3-5-sonnet", "anthropic")

        monkeypatch.setattr(main_mod.hc, "_default_runtime", fake_default)
        resp = self._smoke_demo(monkeypatch, lambda: None)
        assert resp["demo"] is True
        rows = _usage_rows(resp["slug"], runtime_source="paid_fallback")
        assert len(rows) == 1
        assert rows[0]["runtime_source"] == "paid_fallback"
        assert rows[0]["provider"] == "anthropic"
        assert rows[0]["model"] == "claude-3-5-sonnet"

    def test_health_surfaces_usage_summary(self):
        snap = main_mod.snapshot_health()
        assert "provider_usage" in snap
        assert isinstance(snap["provider_usage"]["by_runtime_source"], dict)