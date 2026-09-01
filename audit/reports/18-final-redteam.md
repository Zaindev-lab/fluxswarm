# 18 — Final Red-Team Re-verification

Phase: 18 / 19 — Evidence: `audit/evidence/phase18-final.txt`.

## Runs (fresh, after Phase 16 code)
- Red team: `pytest audit/tests/test_redteam.py` → **29 passed in 5.38s**
- Baseline: `pytest backend/tests` → **80 passed in 5.93s**
- Fix suite: `pytest backend/tests/test_fix_phase15.py` → **5 passed in 1.08s**

## Post-fix posture
| Finding | Status | Guard |
|---------|--------|-------|
| FLX-AUTH-1 (max password) | **FIXED** | RT-07 now asserts 422; fix-suite |
| FLX-DEMO-1 (unbounded demo) | **CAPPED** | fix-suite 429-at-cap + kill-switch 503 |
| Everything else (auth, tenant, webhook, marketplace, referral, E2E) | UNCHANGED-green | 29/29 |

All 109 evidence-backed tests green on the post-fix tree. Money path and
production infra remain externally blocked (subjects of the certification's
conditions, not of code).

Phase 19 — Certification.