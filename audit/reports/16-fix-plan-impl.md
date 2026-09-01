# 16 — Fix-Plan Implementation

Phase: 16 / 19 — Applied the minimal safe fix set from `15-fix-plan.md`. Only
code-clean, behavior-preserving-where-possible edits; no schema/API churn beyond
the plan. Each change anchored, tested, and re-verified (Phase 17).

## Applied
| ID | Change | Files |
|----|--------|-------|
| FIX-1 | Durable daily demo cap: `demo_usage` table + `bump_demo_usage()` (atomic upsert, per-day per-bucket); enforced on `/api/demo/launch` (anon) and on `/api/projects/{slug}/dispatch` for `flux-demo-*` (per-user) — 429 over `FLUXSWARM_DEMO_DAILY_CAP` (default 25). Operator kill-switch `FLUXSWARM_KILL_SWITCH=1` → 503 on all dispatch/demo surfaces | db.py, main.py |
| FIX-3 | `RegisterIn.password` / `LoginIn.password` capped at 4096 via pydantic `Field(max_length=4096)` → oversized rejected pre-hash (422). Min-length behavior untouched (existing 400 path preserved) | main.py |
| FIX-4 | `vault.get_user_key` never silently ships a corrupt key: logs a warning on decrypt failure, still returns None (no behavior break) | vault.py |
| FIX-5 | `PRAGMA foreign_keys=ON` on every connection (verified row; delete_user already orders children-first; template-deletion FK case intentionally still caught → DB-3 documented behavior unchanged) | db.py |
| FIX-6 | Indexes: `idx_projects_user`, `idx_purchases_template`, `idx_purchases_buyer`, `idx_referrals_code` | db.py |
| FIX-7 | CI triggers `master` (repo's default branch) instead of `main` | ci.yml |
| FIX-8 | a11y: skip-link, `role=main`, header `role=navigation`, ARIA labels for lang buttons, `:focus-visible`, `prefers-reduced-motion` (pulse/transitions off) | index.html |
| FIX-9 | SEO: meta description, OG/twitter card, canonical (env `FLUXSWARM_PUBLIC_BASE_URL` → route context), static `robots.txt` + `sitemap.xml` (domain placeholder to fill at deploy) | index.html, main.py, static/ |
| FIX-11 | Terms (AR+EN): credits never expire, auto-refund on launch failure, refund→demo keeps balance | main.py |

## Not-applied (decisions/delayed)
- FIX-2 (Telegram capping): requires bot credentials/behavior change; promoted to
  pre-launch POD with quota design recorded (Phase 15). Code path unchanged.
- FIX-10 (UK GDPR page): copy requires counsel → bundled into Phase 12/19
  delivery checklist, not invented in-repo.
- FIX-12/13/ECON-*: ops/documentation + pricing decisions → `DEPLOY.md`/launch
  ledger, not code.

## Verification (this phase)
- New regression suite `backend/tests/test_fix_phase15.py` (5 tests) → **5 passed**
  (demo cap 4th-request 429 + own-board unaffected, kill-switch 503 on demo &
  dispatch, oversized password rejected, FK pragma on, 4 indexes present).
- `py_compile` clean on all edited modules.
- Pre-existing uncommitted `main.py` edit (debug-verify removal, from a prior
  session) verified intact — not part of this phase, still uncommitted (no
  commits without explicit request).

Phase 17 — Regression verification.