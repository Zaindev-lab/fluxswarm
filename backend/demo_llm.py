from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

_GEMINI_KEY_ENV = "GEMINI_API_KEY"
_OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"

_KEY_ENV = {
    "gemini": _GEMINI_KEY_ENV,
    "openrouter": _OPENROUTER_KEY_ENV,
}

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_COMPLETION_TIMEOUT_S = int(os.environ.get("FLUXSWARM_DEMO_LLM_TIMEOUT_S", "90"))

# A full landing page / web app rarely fits in the 400-token lane default; the
# builder lane (the artifact the user actually sees in /p/) gets a much larger
# output budget. Operators cap it per-env. 3000 tokens ≈ 2-4k words of HTML.
_BUILDER_MAX_TOKENS = int(os.environ.get("FLUXSWARM_BUILDER_MAX_TOKENS", "3000"))
_NORMAL_MAX_TOKENS = 800

# Transient upstream 5xx/429 are a fact of the free pool: retry a bounded
# number of times with small backoff so a 503 hiccup mid-swarm doesn't park the
# whole project board (the thin path would otherwise finalize launch_error at
# the first lane to sneeze). 4xx (401/404/…) is never retried.
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
_THIN_RETRIES = int(os.environ.get("FLUXSWARM_THIN_RETRIES", "2"))
_THIN_RETRY_BACKOFF_S = float(os.environ.get("FLUXSWARM_THIN_RETRY_BACKOFF_S", "2.0"))

_HTTP_CODE_RE = re.compile(r" HTTP (\d{3}):")

# Objectives that describe a browsable website/web app should produce a single
# self-contained index.html — the project preview (/p/<slug>/) then renders it
# as a live Lovable-style page instead of a plain file listing.
_WEB_HINTS = (
    "web app", "webapp", "web application", "website", "web page", "webpage",
    "landing", "landingpage", "landing page", "single-page", "spa", "dashboard",
    "frontend", "portfolio", "saas", " ui", "ui ", " ecommerce",
)


class DemoLLMError(RuntimeError):
    """A real, non-silent failure for the thin demo executor."""


def _http_code(exc: BaseException) -> int | None:
    m = _HTTP_CODE_RE.search(str(exc))
    return int(m.group(1)) if m else None


def _call_with_retries(request, attempts: int) -> str:
    last: DemoLLMError | None = None
    for i in range(max(1, attempts)):
        try:
            return request()
        except DemoLLMError as exc:
            last = exc
            code = _http_code(exc)
            if code is None or code not in _RETRYABLE_HTTP:
                raise
            if i == attempts - 1:
                raise
            time.sleep(_THIN_RETRY_BACKOFF_S * (i + 1))
    raise DemoLLMError("retries exhausted") from last  # pragma: no cover


def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_COMPLETION_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:400]
        except Exception:
            pass
        raise DemoLLMError(f"{provider_hint(exc)} HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise DemoLLMError(f"demo LLM request failed: {exc}") from exc


def provider_hint(exc: BaseException) -> str:
    return "gemini" if "generativelanguage" in getattr(exc, "url", "") else "openrouter"


def completion(provider: str, model: str, prompt: str, max_tokens: int = 400,
               api_key: str | None = None) -> str:
    """One real, bounded completion for the thin executor.

    ``api_key`` optionally overrides the env key (used by the project path to
    honor a user's BYOK key in-process, without ever writing it to disk). The
    demo only ever lands on gemini or the openrouter free fallback; anything
    else fails loudly rather than silently minting a keyed path we have not
    audited.
    """
    provider = (provider or "").strip().lower()
    key_env = _KEY_ENV.get(provider)
    if not key_env:
        raise DemoLLMError(f"thin demo executor has no completion path for provider={provider!r}")
    key = (api_key or os.environ.get(key_env, "")).strip()
    if not key:
        raise DemoLLMError(f"missing {key_env} for the demo executor")

    if provider == "gemini":
        url = _GEMINI_URL.format(model=model, key=key)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
        }

        def _run_gemini() -> str:
            data = _post_json(url, payload)
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError, TypeError) as exc:
                raise DemoLLMError(
                    f"gemini completion shape unexpected: {str(data)[:300]}") from exc
            return text.strip()

        return _call_with_retries(_run_gemini, attempts=_THIN_RETRIES + 1)

    if provider == "openrouter":
        url = _OPENROUTER_URL
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }

        def _run_openrouter() -> str:
            data = _post_json(url, payload, headers={"Authorization": f"Bearer {key}"})
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise DemoLLMError(
                    f"openrouter completion shape unexpected: {str(data)[:300]}") from exc
            return text.strip()

        return _call_with_retries(_run_openrouter, attempts=_THIN_RETRIES + 1)

    raise DemoLLMError(f"unhandled demo provider: {provider!r}")


def _is_web_objective(objective: str) -> bool:
    lower = (objective or "").lower()
    return any(h in lower for h in _WEB_HINTS)


def builder_max_tokens(objective: str = "") -> int:
    """Output-budget for the final deliverable lane (what /p/ renders live)."""
    if not objective or _is_web_objective(objective) or "html" in (objective or "").lower():
        return _BUILDER_MAX_TOKENS
    return _NORMAL_MAX_TOKENS


def lane_max_tokens(objective: str, artifact_name: str | None) -> int:
    """Per-lane token budget: the final deliverable gets the large budget; the
    smaller plan/doc/lint lanes keep the normal cap."""
    if artifact_name is None:
        return builder_max_tokens(objective)
    return _NORMAL_MAX_TOKENS


def planner_prompt(task_title: str, objective: str) -> str:
    prefix = " Deliver the site as ONE self-contained index.html." if _is_web_objective(objective) else ""
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        "Act as the Planner. Produce a SHORT plan (at most 12 lines, plain text)"
        " describing the minimal steps to achieve the objective."
        f"{prefix}"
        " Do not write any files. Output only the plan text.\n"
    )


def builder_prompt(task_title: str, objective: str, plan: str) -> str:
    if _is_web_objective(objective):
        deliverable = (
            "The objective is a WEBSITE / WEB APP — produce ONE self-contained "
            "file named index.html that is GENUINELY EXCELLENT:\n"
            "- A complete, polished, production-looking page, not a sketch: a "
            "coherent design system (deliberate color palette, readable "
            "typography, generous spacing) and a responsive layout for mobile "
            "and desktop.\n"
            "- Real, meaningful copy written for this brand or project. NEVER "
            "lorem ipsum, NEVER placeholder fragments, NEVER truncated words — "
            "every section (hero, features, pricing, contact, etc.) gets "
            "finished sentences.\n"
            "- Working interactions: a smooth-scrolling nav with anchor links, "
            "hover/active states, a functioning form or CTA, and any JS the "
            "page needs — everything inlined, no external CDNs, fonts, or "
            "images.\n"
            "- The file MUST be complete: every tag closed, ending with "
            "</html>. Never abbreviate content to save tokens; if you must "
            "choose, cut whole optional sections rather than leave a sentence "
            "half-finished."
        )
    else:
        deliverable = (
            "Produce the SINGLE final deliverable file that achieves the "
            "objective, COMPLETE and correct. If the objective mentions a "
            "specific file name (e.g. README.md or app.py), write exactly "
            "that; otherwise write the concise code/document file that "
            "fulfills the objective. Never truncate or half-finish output."
        )
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        f"Plan (from the Planner):\n{plan or '(none)'}\n\n"
        "Act as the Builder. "
        f"{deliverable} "
        "Output ONLY the file content — no commentary, no markdown fences, no "
        "``` code blocks.\n"
    )


def deliverable_filename(objective: str) -> str:
    lower = (objective or "").lower()
    if "readme" in lower:
        return "README.md"
    if "python" in lower or "py " in lower or lower.endswith(".py"):
        return "app.py"
    if "html" in lower or _is_web_objective(objective):
        return "index.html"
    return "deliverable.md"


def architect_prompt(task_title: str, objective: str) -> str:
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        "Act as the Architect. Produce a SHORT architecture document (at most "
        "20 lines, plain text, no markdown fences) covering components, data "
        "flow, and the key interfaces of the solution. This becomes "
        "ARCHITECTURE.md. Output only the document text.\n"
    )


def devops_prompt(task_title: str, objective: str, plan: str = "") -> str:
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        "Act as the DevOps engineer. Output ONLY a production-ready Dockerfile "
        "(plain text, no markdown fences, no commentary) that would containerize "
        "this project as a simple Python or static web service.\n"
    )


def tdd_prompt(task_title: str, objective: str, brief: str = "") -> str:
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        "Act as the TDD specialist. Output ONLY the Python source of a pytest "
        "test suite (plain text, no markdown fences) with 3-6 focused tests for "
        "the core behavior described in the objective. No commentary outside "
        "the code.\n"
    )


def reviewer_prompt(task_title: str, objective: str, brief: str = "") -> str:
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        f"Project artifacts produced so far:\n{(brief or '(none)')[:2000]}\n\n"
        "Act as the Reviewer. Output a SHORT review (at most 15 lines, plain "
        "text) listing the strengths and any gaps or risks in the artifacts "
        "relative to the objective. This becomes REVIEW.md.\n"
    )