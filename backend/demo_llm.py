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


def builder_prompt(task_title: str, objective: str, plan: str,
                   repair: bool = False, qa: list[str] | None = None) -> str:
    if _is_web_objective(objective):
        deliverable = (
            "The objective is a WEBSITE / WEB APP — produce ONE self-contained "
            "index.html that is GENUINELY EXCELLENT:\n" + _WEB_BUILD_SPEC
        )
    else:
        deliverable = (
            "Produce the SINGLE final deliverable file that achieves the "
            "objective, COMPLETE and correct. If the objective mentions a "
            "specific file name (e.g. README.md or app.py), write exactly "
            "that; otherwise write the concise code/document file that "
            "fulfills the objective. Never truncate or half-finish output."
        )
    if repair:
        if qa:
            bullets = "\n".join(f"  - {i}" for i in qa[:12])
            fix = (f"\nNOTE — your previous attempt failed automated QA:\n{bullets}\n"
                   "Fix EVERY listed problem and re-issue the ENTIRE, COMPLETE "
                   "file now, ending with </html>.")
        else:
            fix = ("\nNOTE — your previous attempt was truncated or incomplete: "
                   "re-issue the ENTIRE, COMPLETE file now, ending with </html>.")
    else:
        fix = ""
    return (
        f"Task: {task_title}\n\n"
        f"Objective: {objective}\n\n"
        f"Plan (from the Planner):\n{plan or '(none)'}\n\n"
        "Act as the Builder. "
        f"{deliverable} "
        "Output ONLY the file content — no commentary, no markdown fences, no "
        "``` code blocks."
        f"{fix}\n"
    )


_WEB_BUILD_SPEC = (
    "DESIGN SYSTEM (apply it expertly):\n"
    "- Define CSS custom properties up front: --bg, --surface, --text, --muted, "
    "--accent, --accent-2, --border, --radius, --shadow. Choose ONE deliberate, "
    "coherent palette that fits the brand (monochrome base + 1-2 accents; dark "
    "or light theme picked intentionally). Body text must keep WCAG AA contrast. "
    "NEVER render dark-on-dark or light-on-light: any dark background must be "
    "paired with an explicit light text color on the SAME selector, and every "
    "CSS variable you reference must be defined (with a fallback).\n"
    "- Typography: system-ui font stack, a clear type scale using clamp() for "
    "the hero title, line-height 1.55 body / 1.1 headings, paragraphs capped at "
    "~70ch.\n"
    "- Layout & shapes: a centered container (~1140px max), one consistent "
    "spacing rhythm, section padding ~96-120px desktop / 56-64px mobile; cards "
    "in an auto-fit grid with 12-16px radius, hairline border and soft shadow.\n"
    "- Motion: subtle hover lift on cards/buttons, smooth-scroll navigation, "
    "gentle fade/slide reveals — all respecting prefers-reduced-motion.\n"
    "- Responsive: mobile hamburger menu with a working toggle, clamp() "
    "everywhere, zero horizontal scroll.\n"
    "STRUCTURE (adapt to the objective but keep the pattern): sticky translucent "
    "header with nav, hero (headline, one-liner, primary + secondary CTA), "
    "features grid, testimonials or stats, pricing (if relevant), FAQ accordion, "
    "contact/form, footer. EVERY named section must actually exist — never link "
    "to a missing section.\n"
    "ACCESSIBILITY & QA:\n"
    "- Add <html lang>, <title>, meta description, a skip link, visible "
    ":focus-visible styles, ARIA labels on icon-only controls, and of course "
    "assignment of alt — but there are no images: use inline SVG icons or CSS "
    "gradients only.\n"
    "- ANTI-HALLUCINATION: never invent real addresses, phone numbers, emails, "
    "real companies, or quotes attributed to real people; invent plausible "
    "fictional details only. Every href="#..." must target a real section id; "
    "no dead buttons.\n"
    "- SANDBOX: the page renders as a standalone HTML file inside a sandboxed "
    "iframe — do NOT use localStorage, sessionStorage, cookies, or fetch; keep "
    "all state in the DOM or inline JS variables.\n"
    "- COMPLETE & VALID: no external CDNs, fonts, images, or libraries; end "
    "with </html>; every tag closed and the layout clean with no JS errors.\n"
    "- Never output lorem ipsum or placeholder fragments; write finished, "
    "meaningful copy. Never abbreviate content to save tokens — cut whole "
    "optional sections instead of leaving truncated words."
)


def web_artifact_needs_repair(text: str) -> bool:
    """QA gate for a web deliverable: obviously truncated or hollow art
    (missing closing html tag, near-empty, or a code fence that survived the
    artifact strip) → rebuild."""
    t = (text or "").strip()
    if not t or len(t) < 500:
        return True
    first = t.splitlines()[0].strip() if t else ""
    if first.startswith("```"):
        return True
    return not bool(re.search(r"</html\s*>", t, re.IGNORECASE))


_HEX_COLOR_RE = re.compile(r'#((?:[0-9a-f]{3}){1,2}|[0-9a-f]{8})\b', re.IGNORECASE)
_BG_PROP_RE = re.compile(r'background(?:-color)?\s*:\s*([^;{}]+)')
_TEXT_COLOR_RE = re.compile(r'(?<!-|[a-z])color\s*:\s*([^;{}]+)')


def _hex_to_rgb(s: str) -> tuple[int, int, int] | None:
    m = _HEX_COLOR_RE.search(s)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) < 6:
        return None
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return None


def _is_dark(hexish: str) -> bool:
    rgb = _hex_to_rgb(hexish)
    if not rgb:
        return False
    r, g, b = rgb
    # simplified relative luminance; luminance < 0.2 ≈ visibly dark color.
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return lum < 0.2


def _style_blocks(css: str) -> list[tuple[list[str], str]]:
    out = []
    for sel, body in re.findall(r'([^{}]+)\{([^}]*)\}', css):
        sels = [s.strip() for s in sel.split(",")]
        out.append((sels, body))
    return out


def web_qa_issues(html: str) -> list[str]:
    """Deterministic post-build audit of a generated web page. Runs on every
    web deliverable so a truncated, broken, hollow or sandbox-hostile page is
    repaired BEFORE the user ever sees it in /p/."""
    text = html or ""
    low = text.lower()
    issues: list[str] = []
    if "</html>" not in low:
        issues.append("truncated: no closing </html>")
    clean = len(text.strip())
    if clean < 500:
        issues.append(f"too short ({clean} chars) to be a real page")
    if "<nav" not in low and "<header" not in low and "<ul" not in low:
        issues.append("no navigation (<nav>/<header>/<ul>) found")
    if "<footer" not in low:
        issues.append("no <footer> section")
    if not re.search(r"<h1\b", low):
        issues.append("no <h1> headline (page has no obvious title)")
    # ---- invisible-page / black-page guards -------------------------------
    css_blocks = [blk for blk in re.findall(r"<style[^>]*>(.*?)</style>", text, re.S)]
    css_text = "\n".join(css_blocks)
    for sels, body in _style_blocks(css_text):
        if not any(s == "html" or s == "body" or s == "*" for s in sels):
            continue
        bgm = _BG_PROP_RE.search(body)
        bg = _hex_to_rgb(bgm.group(1)) if bgm else None
        if bg is None or not _is_dark(bgm.group(1)):
            continue
        colm = _TEXT_COLOR_RE.search(body)
        if colm is None or _is_dark(colm.group(1)):
            issues.append(
                f"dark background with no light text color ({', '.join(sels)} "
                f"uses {bgm.group(1).strip()}) — invisible/black page risk")
    defined = set(re.findall(r"--([\w-]+)\s*:", css_text))
    for v in sorted(set(re.findall(r"var\(\s*--([\w-]+)\s*", text))):
        if v in defined:
            continue
        uses = re.findall(r"var\(\s*--" + re.escape(v) + r"\s*([,)])", text.lower())
        if uses and any(clo == ")" for clo in uses):
            issues.append(f"uses undefined CSS variable --{v} (no fallback — "
                          "invisible-content risk)")
    nodeps = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S)
    visible_text = re.sub(r"\s+", " ",
                          re.sub(r"<[^>]*>", " ", nodeps)).strip()
    if len(visible_text) < 80:
        issues.append("almost no readable text on the page (hollow/blank layout)")
    for pat, label in (("localstorage.", "localStorage"), ("sessionstorage.", "sessionStorage"),
                       ("document.cookie", "document.cookie"), ("fetch(", "fetch(…)")):
        if pat in low:
            issues.append(f"sandbox-unsafe: uses {label} (breaks inside the preview iframe)")
    for m in set(re.findall(r'(?:src|href)\s*=\s*["\']https?://', low)):
        issues.append(f"external resource referenced ({m}) — must be inline")
    if re.search(r'''href=["']#["']''', text):
        issues.append("dead link: href='#' (no target)")
    ids = set(re.findall(r'''id=["']([^"']+)["']''', text))
    for h in sorted(set(re.findall(r'''href=["'](#[^"']*)["']''', text))):
        if h != "#" and h[1:] not in ids:
            issues.append(f"broken anchor: {h} links to a missing id")
    for tok in ("lorem ipsum", "sample text", "your text here", "replace this",
                "image here", "type your", "change this", "dummy "):
        if tok in low:
            issues.append(f"placeholder/filler copy: '{tok}'")
    return issues


# Issue prefixes that are hard failures (structural/sandbox) → an automatic
# repair round is triggered. Missing-nav/footer/filler-copy alone are soft and
# would churn without adding real value.
_HARD_QA_PREFIXES = ("truncated", "too short", "broken anchor", "sandbox-unsafe",
                     "external resource", "dead link", "no closing",
                     "dark background", "undefined CSS variable",
                     "almost no readable text")


def web_qa_should_repair(issues: list[str]) -> bool:
    return any(i.startswith(_HARD_QA_PREFIXES) for i in issues)


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