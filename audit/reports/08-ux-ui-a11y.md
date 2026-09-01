# 08 — UX / UI / Accessibility Audit

Phase: 8 / 19 — **Browser tooling is unavailable in this environment**: all
rendered-behavior checks (visual contrast, focus/keyboard flows, screen-reader
semantics, mobile viewport quirks, DOM XSS exploitation) are **BLOCKED** and
listed as follow-ups. What follows is CODE-EVIDENCE for the written template.

## Runtime-independent facts (CODE)
- `backend/templates/index.html` (single page, 36.4 kB): `<html lang="ar" dir="rtl">`
  default, `setLang()` updates `document.documentElement.lang/dir` + persists
  `localStorage["flux-lang"]` (esp. `:387-393`) — corrective i18n is real.
- `esc()` present and consistently applied to user-controlled data rendered into
  the DOM (HTML stored as data + escaped output; RT-X2 VERIFIED at API level).
- Internationalization: `t()`/`fmt()` single-file dictionary, EN/AR.
- No `<script src>` external — dependency-light (less supply-chain/DOM risk).
- Static CSS inline in `<head>`; no `static/style.css` (dir absent).

## Accessibility gaps (CODE-verified, need visual confirmation later)
| Check | Status | Evidence |
|-------|--------|----------|
| Semantic landmarks / ARIA | **missing** | zero `aria-*`/`role=` in template |
| Skip-link for keyboard nav | **missing** | no skip/landmark target |
| `<html lang>` declarative set | ✅ default ar + runtime switch | lang="ar"; setLang |
| Focus-visible styles | unverified | no `:focus-visible` grep hit |
| prefers-reduced-motion | **missing** | not referenced |
| Alt text on meaningful icons/emojis | partial | CSS-only; emojis as "labels" |
| Screen-reader test | BLOCKED | no browser/AT |
| Color contrast verification | BLOCKED | cannot measure |
| Keyboard-only flow audit | BLOCKED | cannot interact |
| Mobile/viewport layout @320px | BLOCKED | viewport meta present but untested |

## Verdict
Architecture is sound (one page, escaped output, persisted lang), but the page
lacks explicit a11y affordances (ARIA/skip/focus/reduced-motion). Recommend a
manual a11y pass (axe-core + keyboard walkthrough) after the browser tool is
available; add ARIA landmarks + skip-link + `:focus-visible` in Phase 15 as a
low-risk code improvement (non-blocking for launch compliance, P3).

## BLOCKED register
- Visual/contrast/interactive audits — require a real browser.
- Screen readers / keyboard EV — require AT or headless Chrome.

Phase 9 — Billing / Pricing / Economics.