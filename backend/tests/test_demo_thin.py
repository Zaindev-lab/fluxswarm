"""Thin demo executor tests (Free-tier convergence path).

The free demo surface must converge within its budget: the full Hermes worker
crashes ~40-80s into real work on the 512MB host (measured via recovered
reason=crash), so demo lanes use a bounded thin executor — real board, real
provider completion, real artifact, real completed event — while the paid
path keeps the full agents untouched. These tests lock the thin path's
honesty boundaries and the reaper's ownership split for flux-demo-* boards.
"""
from __future__ import annotations

import os

import hermes_client as hc
import demo_llm


class _R:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def _capture_run(monkeypatch, resp=None):
    calls = []

    def fake_run(args, board=None, capture=True, provider_keys=None):
        calls.append((board, list(args)))
        if resp is not None and len(calls) == 1:
            return resp
        return _R()

    monkeypatch.setattr(hc, "_run", fake_run)
    monkeypatch.setattr(hc, "_emit_working", lambda *a, **k: None)
    return calls


def test_demo_llm_gemini_completion_payload(monkeypatch):
    """gemini path builds the generateContent payload and extracts text."""
    seen = {}

    def fake_post(url, payload, headers=None):
        seen["url"] = url
        seen["payload"] = payload
        return {"candidates": [{"content": {"parts": [{"text": "  hello demo  "}]}}]}

    monkeypatch.setattr(demo_llm, "_post_json", fake_post)
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    text = demo_llm.completion("gemini", "gemini-1.5-flash", "do it")
    assert text == "hello demo"
    assert "generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent" in seen["url"]
    assert seen["payload"]["contents"][0]["parts"][0]["text"] == "do it"


def test_demo_llm_openrouter_completion(monkeypatch):
    """openrouter path sends the chat payload with the bearer key."""
    seen = {}

    def fake_post(url, payload, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        return {"choices": [{"message": {"content": "  or-answer  "}}]}

    monkeypatch.setattr(demo_llm, "_post_json", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "ork-test")
    text = demo_llm.completion("openrouter", "nvidia/nemotron-3.5-lightning:free", "hi")
    assert text == "or-answer"
    assert seen["url"].startswith("https://openrouter.ai/")
    assert seen["headers"]["Authorization"] == "Bearer ork-test"


def test_demo_llm_missing_key_raises(monkeypatch):
    """A keyless thin completion must fail loudly, never fake an answer."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    try:
        demo_llm.completion("gemini", "gemini-1.5-flash", "x")
        raise AssertionError("expected DemoLLMError")
    except demo_llm.DemoLLMError as exc:
        assert "GEMINI_API_KEY" in str(exc)


def test_demo_llm_unhandled_provider_raises():
    try:
        demo_llm.completion("anthropic", "claude-x", "x")
        raise AssertionError("expected DemoLLMError")
    except demo_llm.DemoLLMError as exc:
        assert "no completion path" in str(exc)


def test_deliverable_filename_mapping():
    assert demo_llm.deliverable_filename("write a README") == "README.md"
    assert demo_llm.deliverable_filename("landing HTML page") == "index.html"
    assert demo_llm.deliverable_filename("python script") == "app.py"
    assert demo_llm.deliverable_filename("anything else") == "deliverable.md"


def test_thin_execute_runs_claim_attach_complete_sequence(monkeypatch, tmp_path):
    """The thin executor drives the REAL board CLI (claim -> attach -> complete)
    and writes the REAL artifact from the provider answer."""
    ws = tmp_path / "ws"
    ws.mkdir()
    calls = _capture_run(monkeypatch)
    monkeypatch.setattr(demo_llm, "completion", lambda p, m, prompt: "Final deliverable body\nsecond line")

    res = hc.thin_execute("bdemo", "t1", str(ws), "gemini", "gemini-1.5-flash",
                          "PROMPT", objective="write a README")
    assert res["ok"] is True
    assert res["result"] == "Final deliverable body"
    art = ws / "README.md"
    assert art.exists()
    assert "Final deliverable body" in art.read_text(encoding="utf-8")
    kinds = []
    for board, args in calls:
        assert board == "bdemo"
        if args[0] == "claim":
            kinds.append(("claim", args[1]))
        elif args[0] == "attach":
            kinds.append(("attach", args[2]))
        elif args[0] == "complete":
            kinds.append(("complete", args[1], args[args.index("--result") + 1]))
    assert kinds[0] == ("claim", "t1")
    assert kinds[1][0] == "attach" and kinds[1][1] == str(art)
    assert kinds[2] == ("complete", "t1", "Final deliverable body")


def test_thin_execute_claim_failure_raises(monkeypatch, tmp_path):
    """A claim the board refuses must fail the lane loudly (no silent skip)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _capture_run(monkeypatch, resp=_R(rc=1, stderr="cannot claim t9: status=done"))
    try:
        hc.thin_execute("bdemo", "t9", str(ws), "gemini", "model", "p", objective="x")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "could not be claimed" in str(exc)


def test_reaper_skips_demo_boards(monkeypatch):
    """flux-demo-* boards are owned by the thin driver: the reaper must never
    dispatch a fat worker against them."""
    import main as main_mod
    dispatched = []

    monkeypatch.setattr(main_mod.hc, "list_boards",
                        lambda: [{"slug": "flux-demo-1"}, {"slug": "u1-real"}])
    monkeypatch.setattr(main_mod, "_board_finalized", lambda slug: False)
    monkeypatch.setattr(main_mod.hc, "board_is_sealed", lambda slug: False)
    monkeypatch.setattr(main_mod.hc, "kill_stale_workers", lambda slug: None)
    monkeypatch.setattr(main_mod.hc, "bump_blocked_to_ready", lambda slug: None)
    monkeypatch.setattr(main_mod.hc, "board_has_unfinished_work", lambda slug: True)
    monkeypatch.setattr(main_mod.hc, "dispatch",
                        lambda slug, max_spawn=None, blocking=False: dispatched.append(slug))

    main_mod._reconcile_boards_once()
    assert dispatched == ["u1-real"]