from __future__ import annotations

import json
import os
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


class DemoLLMError(RuntimeError):
    """A real, non-silent failure for the thin demo executor."""


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
        data = _post_json(url, payload)
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DemoLLMError(f"gemini completion shape unexpected: {str(data)[:300]}") from exc
        return text.strip()

    if provider == "openrouter":
        url = _OPENROUTER_URL
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        data = _post_json(url, payload, headers={"Authorization": f"Bearer {key}"})
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DemoLLMError(f"openrouter completion shape unexpected: {str(data)[:300]}") from exc
        return text.strip()

    raise DemoLLMError(f"unhandled demo provider: {provider!r}")


def planner_prompt(task_title: str, objective: str) -> str:
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        "Act as the Planner. Produce a SHORT plan (at most 12 lines, plain text)"
        " describing the minimal steps to achieve the objective. Do not write "
        "any files. Output only the plan text.\n"
    )


def builder_prompt(task_title: str, objective: str, plan: str) -> str:
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        f"Plan (from the Planner):\n{plan or '(none)'}\n\n"
        "Act as the Builder. Produce the SINGLE final deliverable file that "
        "achieves the objective. Output ONLY the file content — no commentary, "
        "no markdown fences. If the objective mentions a specific file name "
        "(e.g. README.md), write exactly that; otherwise write a concise text "
        "document that fulfills the objective.\n"
    )


def deliverable_filename(objective: str) -> str:
    lower = (objective or "").lower()
    if "readme" in lower:
        return "README.md"
    if "html" in lower:
        return "index.html"
    if "python" in lower or "py " in lower:
        return "app.py"
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