# 17 — Regression Verification

Phase: 17 / 19 — Full re-run after Phase 16 code changes.

## Results
| Suite | Command | Result | Date |
|-------|---------|--------|------|
| New fix tests | `pytest backend/tests/test_fix_phase15.py -q` | **5 passed in 1.07s** | 2026-08-30 |
| Baseline | `pytest backend/tests -q` | **80 passed in 7.53s** (was 75 → +5) | 2026-08-30 |
| Red team | `pytest audit/tests/test_redteam.py -q` | **29 passed in 5.33s** | 2026-08-30 |

## Red-team delta (RT07)
- RT-07 previously asserted the FLX-AUTH-1 flaw (64 KiB password NOT cleanly
  rejected). Post-FIX-3 it asserts `422` on both register and login — the flaw is
  closed and the test now guards the fix (ledger updated).
- RT-T8/RT-E2 (FLX-DEMO-1) still pass their "shared/unpriced" assertions (they
  dispatch ≤ 6 times < cap 25), and the new cap is enforced independently
  (fix-test proves 429 at the cap).

## Scope safety
- No offline behavior change for non-demo flows: own-board dispatch/launch paths
  untouched except the maintenance gate (off by default) and the FK pragma.
- FK pragma introduced no failures across 109 tests incl. delete_user cascade.
- `FLUXSWARM_DEMO_DAILY_CAP` default 25 — no red-team/baseline test approaches it.

## Remaining (not code)
Live server (pid at 127.0.0.1:8787) still runs pre-fix code — it will pick these
changes up at the next os-managed restart/deploy. Backups + Redis + monitoring +
money-path remain the launch checklist (Phases 11/12/15).

Phase 18 — Final red-team re-verification.