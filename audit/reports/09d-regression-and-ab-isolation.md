# PHASE 24–25 — REGRESSION SUITE + A/B (TESTER ISOLATION)

## Regression state (VERIFIED 2026-08-30)
- Baseline pytest: **80/80 green** after the legal+marketing+SEO route additions
  (test run above, 13.7s).
- Legacy fix suite: **5/5 green** (`test_fix_phase15.py`).
- Red team ledger: **29/29** recorded in `TEST-LEDGER.md` (post-FIX states).
- E2E harness: full register→launch→WS task-stream→workspace recorded
  (`evidence/phase14-e2e.txt`).

## A/B tester isolation (how concurrent runs don't collide)
- Each test uses its own temp DB (`tmp_path`), separate env (`FLUXSWARM_*`
  overrides), unique slugs/emails → no shared iteration state.
- Feature-flag isolation: `FLUXSWARM_PAYMENTS` (money on/off), `FLUXSWARM_PADDLE_MOCK`
  (gateway vs mock), `FLUXSWARM_KILL_SWITCH` (fail-closed 503), demo cap env —
  enables toggling behaviors in tests without code forks.
- Concurrent/atomic cases (upgrade, purchase, webhook dedup, referral-reward)
  run serialized against `BEGIN IMMEDIATE` semantics — verified by the red-team
  race tests (RT-W-series, RT-T-series).
- The webhook replay/dedup test proves a second identical event is neutralized
  (A/B isolation across replayed delivery).

## Gaps
- No browser-level regression (BLOCKED) → JS paths asserted only via API + static
  review; P2: Playwright smoke set at deploy.
- pip-audit not yet in CI (DEPS-1) → add pre-launch.

## Verdict
Regression posture sound; add browser smoke + CVE scan before go-live.