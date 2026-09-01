# FluxSwarm — Audit Evidence Vault

Master audit & production-readiness of FluxSwarm (19 phases, run 2026-08-30 on
`127.0.0.1:8787`). Everything here is evidence-backed; nothing is assumed.

## Entry points
- `CERTIFICATION.md` — Go / Conditional Go / No-Go per market + final register.
- `AUDIT-STATE.md` — runbook/state of every phase (why each conclusion was drawn).
- `AUDIT-MANIFEST.md` — phase ledger with dates and evidence links.
- `TEST-LEDGER.md` — line-by-line test evidence (red team, baseline, harness notes).
- `reports/` — per-phase analysis (01 … 18) + cross-cutting:
  CLAIM-VERIFICATION · DATA-MAP · PRODUCT-FAQ · LICENSE-AUDIT · FAILURE-MATRIX.
- `evidence/` — raw, captured command outputs (no post-hoc editing).

## How to reproduce any number
- venv python: `C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`
- red team: `python -m pytest audit/tests -q`
- baseline: `python -m pytest backend/tests -q`
- fix suite: `python -m pytest backend/tests/test_fix_phase15.py -q`
Post-fix totals: **29 / 80 / 5 all passing.**

## About fixes
Phase 16 applied a minimal, regression-tested fix set (see `reports/16-…`).
Uncommitted working-tree changes exist in `backend/main.py` from a *prior*
session (debug-verify endpoint removal) — documented in the reports; nothing was
committed because no commit was requested.

## Launch checklist (external blockers)
LIVE Paddle keys · domain + TLS · US host + backups + Redis · monitoring ·
counsel sign-off US+UK · UK GDPR addendum · replace `YOUR_DOMAIN` in
`static/{robots,sitemap}.{txt,xml}`.