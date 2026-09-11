"""Thin demo executor tests (Free-tier convergence path).

The free demo surface must converge within its budget: the full Hermes worker
crashes ~40-80s into real work on the 512MB host (measured via recovered
reason=crash), so demo lanes use a bounded thin executor — real board, real
provider completion, real artifact, real completed event — while the paid
path keeps the full agents untouched. These tests lock the thin path's
honesty boundaries and the reaper's ownership split for flux-demo-* boards.
"""
from __future__ import annotations

import json
import os
import sqlite3

import hermes_client as hc
import demo_llm


def _mk_demo_board(tmp_path, monkeypatch, board="bdemo", task_ids=("t1",)):
    """Point HERMES_HOME at a temp sandbox and create a real board DB with
    generic 'ready' tasks (the launch_demo_profile shape minus the graph)."""
    monkeypatch.setattr(hc, "HERMES_HOME", str(tmp_path))
    hc._ensure_demo_board_db(board)
    c = sqlite3.connect(str(hc._board_db_path(board)))
    try:
        for i, tid in enumerate(task_ids):
            c.execute(
                "INSERT INTO tasks (id,title,assignee,status,created_at) "
                "VALUES (?,?,?,?,?)",
                (tid, "Task", "ecc-planner", "ready", 1000 + i))
            c.execute("INSERT INTO task_events (task_id,kind,payload,created_at) "
                      "VALUES (?,?,?,?)",
                      (tid, "created", None, 1000 + i))
        c.commit()
    finally:
        c.close()


def _db_events(board, tid):
    c = sqlite3.connect(str(hc._board_db_path(board)))
    try:
        return c.execute(
            "SELECT kind, payload FROM task_events WHERE task_id=? ORDER BY id",
            (tid,)).fetchall()
    finally:
        c.close()


def _db_task(board, tid):
    c = sqlite3.connect(str(hc._board_db_path(board)))
    try:
        row = c.execute(
            "SELECT status, result, completed_at FROM tasks WHERE id=?", (tid,)).fetchone()
        return row
    finally:
        c.close()


def test_demo_llm_gemini_completion_payload(monkeypatch):
    """gemini path builds the generateContent payload and extracts text."""
    seen = {}

    def fake_post(url, payload, headers=None):
        seen["url"] = url
        seen["payload"] = payload
        return {"candidates": [{"content": {"parts": [{"text": "  hello demo  "}]}}]}

    monkeypatch.setattr(demo_llm, "_post_json", fake_post)
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    text = demo_llm.completion("gemini", "gemini-3.5-flash-lite", "do it")
    assert text == "hello demo"
    assert "generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent" in seen["url"]
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
        demo_llm.completion("gemini", "gemini-3.5-flash-lite", "x")
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
    # Web-intent goals map to a browsable index.html (served live by /p/<slug>/).
    assert demo_llm.deliverable_filename("build a website for a bakery") == "index.html"
    assert demo_llm.deliverable_filename("a landing page for our startup") == "index.html"
    assert demo_llm.deliverable_filename("create a dashboard UI") == "index.html"
    assert demo_llm.deliverable_filename("a single-page web app") == "index.html"
    assert demo_llm.deliverable_filename("anything else") == "deliverable.md"


def test_web_intent_guidance_in_prompts():
    web = demo_llm.builder_prompt("Build", "a landing page for a startup", "plan")
    assert "index.html" in web
    assert "no external CDNs, fonts, images, or libraries" in web
    assert "Never abbreviate content" in web
    code = demo_llm.builder_prompt("Build", "a FastAPI REST API", "plan")
    assert "index.html" not in code
    assert "single self-contained index.html" not in code


def test_builder_token_budget_scales_with_web_goals():
    assert demo_llm.builder_max_tokens("a landing page") > 400
    assert demo_llm.builder_max_tokens("build a dashboard UI") > 400
    assert demo_llm.builder_max_tokens("a REST API") == demo_llm._NORMAL_MAX_TOKENS
    assert demo_llm.lane_max_tokens("a landing page", None) > 400
    assert demo_llm.lane_max_tokens("a REST API", "PLAN.md") == demo_llm._NORMAL_MAX_TOKENS


def test_web_artifact_qa_gate_catches_broken_deliverables():
    good = ("<!doctype html><html lang='en'><head><title>N</title></head><body>"
            + ("<p>" + "x" * 900 + "</p>") * 2 + "</body></html>")
    assert demo_llm.web_artifact_needs_repair(good) is False
    assert demo_llm.web_artifact_needs_repair("<!doctype html><title>cut") is True
    assert demo_llm.web_artifact_needs_repair("tiny") is True
    assert demo_llm.web_artifact_needs_repair("") is True
    fenced = "```html\n<!doctype html><html><body></body></html>\n"
    assert demo_llm.web_artifact_needs_repair(fenced) is True


def test_web_qa_issues_detect_broken_and_sandbox_broken_pages():
    bad = ("<!doctype html><html><body><nav><a href='#missing'>x</a></nav>"
           "<a href='#'>dead</a><script>localStorage.setItem('a',1);"
           "fetch('/x')</script><link rel='stylesheet' href='https://cdn.x/style.css'>"
           + ("<p>lorem ipsum text</p>" * 3) + "</body>")
    issues = demo_llm.web_qa_issues(bad)
    joined = "\n".join(issues)
    assert "no closing </html>" in joined
    assert "broken anchor" in joined
    assert "dead link" in joined
    assert "sandbox-unsafe: uses localStorage" in joined
    assert "fetch(" in joined
    assert "external resource referenced" in joined
    assert "lorem ipsum" in joined
    assert demo_llm.web_qa_should_repair(issues) is True

    good = ("<!doctype html><html lang='en'><head><title>N</title></head><body>"
            "<nav><a href='#features'>F</a></nav>"
            f"<section id='features'><h1>N</h1>{('<p>' + 'x' * 900 + '</p>') * 2}"
            "</section><footer>N 2026</footer></body></html>")
    assert demo_llm.web_qa_issues(good) == []


def test_web_qa_catches_invisible_black_page():
    dark_on_dark = ("<!doctype html><html><head><style>"
                    "body{background:#0b0f1a;color:#0b0f1a;font-family:system-ui}"
                    "</style></head><body><nav><a href='#f'>F</a></nav>"
                    + ("<p>'x' * 300</p>") + "garbage</body></html>")
    joined = "\n".join(demo_llm.web_qa_issues(dark_on_dark))
    assert "dark background with no light text color" in joined
    assert demo_llm.web_qa_should_repair(demo_llm.web_qa_issues(dark_on_dark)) is True

    undefined_var = ("<!doctype html><html><head><style>"
                     "body{background:#0b0f1a;color:var(--text)}</style></head>"
                     "<body><nav><a href='#f'>F</a></nav><section id='f'>"
                     + ("<p>" + "x" * 300 + "</p>") * 2 + "</section></body></html>")
    joined = "\n".join(demo_llm.web_qa_issues(undefined_var))
    assert "undefined CSS variable --text" in joined

    hollow = ("<!doctype html><html><head><style>"
              "body{background:#fff;color:#111}</style></head>"
              "<body><nav></nav><h1></h1><section></section><footer></footer>"
              "</body></html>")
    joined = "\n".join(demo_llm.web_qa_issues(hollow))
    assert "almost no readable text" in joined


def test_thin_execute_strips_wrapping_markdown_fence(monkeypatch, tmp_path):
    """A lenient completion that wraps HTML in backticks still lands a clean
    artifact on disk (the builder prompts forbid fences, but never trust the
    model)."""
    import secrets
    monkeypatch.setattr(hc, "HERMES_HOME", str(tmp_path))
    board = "u1-fence-" + secrets.token_hex(3)
    ws = tmp_path / "ws"
    hc._ensure_demo_board_db(board)
    c = sqlite3.connect(str(hc._board_db_path(board)))
    try:
        hc._demo_insert_task(c, board, task_id="t_fix", title="Build",
                             assignee="ecc-build-fixer", status="ready")
        c.commit()
    finally:
        c.close()
    monkeypatch.setattr("hermes_client.demo_llm.completion",
                        lambda p, m, prompt, max_tokens=400, api_key=None:
                        "```html\n<!doctype html><title>X</title>\n```")
    hc.thin_execute(board=board, task_id="t_fix", workspace=str(ws),
                    provider="gemini", model="m", prompt="p",
                    objective="a landing page", artifact_name="index.html")
    out = (ws / "index.html").read_text(encoding="utf-8")
    assert "```" not in out
    assert out.startswith("<!doctype html>")


def test_thin_execute_drives_board_events_and_artifact(monkeypatch, tmp_path):
    """The thin executor drives REAL board state (claim -> attach -> complete)
    directly in kanban.db and writes the REAL artifact from the provider."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _mk_demo_board(tmp_path, monkeypatch)
    monkeypatch.setattr(demo_llm, "completion",
                        lambda p, m, prompt, max_tokens=400, api_key=None: "Final deliverable body\nsecond line")

    res = hc.thin_execute("bdemo", "t1", str(ws), "gemini", "gemini-3.5-flash-lite",
                          "PROMPT", objective="write a README")
    assert res["ok"] is True
    assert res["result"] == "Final deliverable body"
    art = ws / "README.md"
    assert art.exists()
    assert "Final deliverable body" in art.read_text(encoding="utf-8")

    kinds = [k for k, _ in _db_events("bdemo", "t1")]
    assert kinds == ["created", "claimed", "heartbeat", "heartbeat", "attached", "completed"]
    status, result, completed_at = _db_task("bdemo", "t1")
    assert status == "done"
    assert result == "Final deliverable body"
    assert completed_at


def test_thin_execute_failure_raises_and_marks_board(monkeypatch, tmp_path):
    """A thin-lane failure must be VISIBLE on the board (error timeline row +
    done-with-error task) before the lane re-raises — never silently stuck."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _mk_demo_board(tmp_path, monkeypatch)
    monkeypatch.setattr(demo_llm, "completion",
                        lambda p, m, prompt, max_tokens=400, api_key=None: (_ for _ in ()).throw(
                            demo_llm.DemoLLMError("gemini HTTP 401: bad key")))

    try:
        hc.thin_execute("bdemo", "t1", str(ws), "gemini", "g", "P", objective="x")
        raise AssertionError("expected DemoLLMError to propagate")
    except demo_llm.DemoLLMError:
        pass

    kinds = [k for k, _ in _db_events("bdemo", "t1")]
    assert "claimed" in kinds          # the lane really started
    assert "error" in kinds            # the reason is on the board
    status, result, _ = _db_task("bdemo", "t1")
    assert status == "done"            # never left running/stuck
    assert "ERROR" in (result or "")


def test_thin_execute_ignores_provider_absence_only_via_real_error(monkeypatch, tmp_path):
    """A board with no copy of the lane still surfaces the write failure loudly
    (error event) instead of pretending the lane ran."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _mk_demo_board(tmp_path, monkeypatch, task_ids=("t_other",))
    monkeypatch.setattr(demo_llm, "completion", lambda p, m, prompt, max_tokens=400, api_key=None: "ok")
    try:
        hc.thin_execute("bdemo", "missing", str(ws), "gemini", "g", "P", objective="x")
        raise AssertionError("expected RuntimeError")
    except Exception:
        pass
    kinds = [k for k, _ in _db_events("bdemo", "missing")]
    assert "error" in kinds


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


def test_thin_execute_failure_lands_error_event(monkeypatch, tmp_path):
    """A thin-lane failure must be VISIBLE on the board (error timeline row)
    before the lane re-raises — the demo never fails silently."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _mk_demo_board(tmp_path, monkeypatch)
    monkeypatch.setattr(demo_llm, "completion",
                        lambda p, m, prompt, max_tokens=400, api_key=None: (_ for _ in ()).throw(
                            demo_llm.DemoLLMError("gemini HTTP 401: bad key")))

    try:
        hc.thin_execute("bdemo", "t1", str(ws), "gemini", "g", "P", objective="x")
        raise AssertionError("expected DemoLLMError to propagate")
    except demo_llm.DemoLLMError:
        pass
    err_rows = [p for k, p in _db_events("bdemo", "t1") if k == "error"]
    assert err_rows and "HTTP 401" in (err_rows[0] or "")


def test_error_kind_renders_in_activity_log(monkeypatch, tmp_path):
    """The UI timeline renders a 'Demo error: …' row for kind=error events."""
    monkeypatch.setattr(hc, "HERMES_HOME", str(tmp_path))
    board = "flux-demo-render"
    db = hc._board_db_path(board)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, "
                "kind TEXT, payload TEXT, created_at REAL)")
    con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("t1", "error", json.dumps({"message": "gemini HTTP 429: rate limited"}), 1000))
    con.commit()
    con.close()
    ev = hc._task_activity_events(board, "t1")
    assert ev and ev[0]["kind"] == "error"
    assert "HTTP 429" in ev[0]["label"]