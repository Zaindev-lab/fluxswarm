# TEST LEDGER — FluxSwarm Audit

Every test event is recorded here as evidence. Product code is never modified by
audit tests; the red-team harness runs the real ASGI app (`TestClient`) in-process
against an isolated temp DB (`FLUXSWARM_DB`), a repainted vault/audit store, and
`hermes_client` subprocess calls monkeypatched to no-ops so **no real swarm,
token or board is ever touched**.

Run environment (all commands from repo root):
- venv python: `C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`
- Baseline: `-m pytest backend/tests -q`
- Red team:  `-m pytest audit/tests/test_redteam.py -q`

## 1. Baseline regression (Phase 2 + Phase 17 will redo)

| Run | Command | Result | Date |
|-----|---------|--------|------|
| baseline-1 | `pytest backend/tests -q` | `75 passed in 5.96s` | 2026-08-30 |
| post-phase-5 | `pytest backend/tests -q` | `75 passed in 5.58s` | 2026-08-30 |

## 2. Red-team harness (Phase 5)

Final run: `pytest audit/tests/test_redteam.py -q` → **`29 passed in 5.33s`**.

| # | Test | Attack / verification | Expected | Actual |
|---|------|-----------------------|----------|--------|
| RT01 | auth_roundtrip | register→/api/me | OK | PASS |
| RT02 | no_user_enumeration | unknown vs known email, identical 401 body | no diff | PASS |
| RT03 | alg_none_rejected | unsigned `alg=none` token | 401 | PASS |
| RT04 | tampered_token_rejected | flipped signature bytes | 401 | PASS |
| RT05 | login_bruteforce_locks | 6 bad logins → 429 | 429 | PASS |
| RT06 | register_ip_cap | 20 auth hits consume 20/min IP cap → 21st blocked | 429 | PASS |
| RT07 | no_max_password_length | 64 KiB password accepted (not clean 422) | no 422 | **PASS = flaw confirmed** (medium) → **FIXED Phase 16 (FIX-3)** |
| RT-T1 | b_cannot_read_a_tasks | cross-tenant read | 403 | PASS |
| RT-T2 | b_cannot_dispatch_a_board | cross-tenant dispatch | 403 | PASS |
| RT-T3 | b_cannot_scan_a_board | cross-tenant security scan | 403 | PASS |
| RT-T4 | project_list_isolated | B sees no A slug | isolated | PASS |
| RT-T5 | owner_access_control | A passes own guard | no 403 | PASS |
| RT-T6 | template_marketplace_isolation_and_earn | B can't see A's "mine"; buy pays author 50% | earn=1 | PASS |
| RT-T6a | cannot_buy_own_template | self-purchase | 400 | PASS |
| RT-T7 | dev_endpoints_mock_gated | /mock-checkout, /dev-complete sealed | 404 | PASS |
| RT-T8 | demo_board_shared_and_unpriced | any user reads + dispatches `flux-demo-*`, no debit | 200, no credit change | **PASS = flaw confirmed** (FLX-DEMO-1, high) → **capped Phase 16 (FIX-1)** |
| RT-T9 | ws_fail_closed | unauthenticated WS | explicit error | PASS |
| RT-P1 | traversal_slug_rejected | `../`, `%2f`, `; rm -rf /` slugs | 403/404 | PASS |
| RT-P2 | unknown_template_agent_rejected | non-allowlisted agent | 400 | PASS |
| RT-X1 | scriptable_template_blocked | `<script>`, `onerror=`, `javascript:` | 400 | PASS |
| RT-X2 | project_name_html_not_reflected_unsafe | stored as data; frontend escapes | 200 | PASS |
| RT-W1 | unsigned_rejected | webhook no signature | 400 | PASS |
| RT-W2 | signed_grants_and_replay_idempotent | valid v2 sig → plan+credits; replay dedup | 200, dedup | PASS |
| RT-W3 | stale_signature_rejected | 1h-old ts | 400 | PASS |
| RT-W4 | wrong_secret_rejected | bad secret, plan unchanged | 400 | PASS |
| RT-W5 | refund_downgrades_keeps_credits | refund → demo plan, 120 credits kept | kept | PASS |
| RT-E1 | insufficient_credit_402 | subscribe gate closed | 402 | PASS |
| RT-E2 | dispatch_unlimited_on_shared_board | 6 dispatches, no 429, no debit | 200 ×6 | **PASS = flaw confirmed** (FLX-DEMO-1, high) → **capped Phase 16 (FIX-1)** |
| RT-E3 | account_delete_removes_owned_rows | /api/account DELETE cascade | user+projects gone | PASS |

## 3. Harness engineering notes (process evidence)

1. First harness run failed 21/29: the shared in-process 20/min IP limiter poisoned
   later tests (all TestClient traffic shares one source IP). Fixed with an
   autouse fixture that resets `ratelimit._MEMORY` buckets per test.
2. `RT06` requires exactly the global IP cap: 20 auth hits then the 21st request
   must be throttled (the 10th was not enough — 10 < 20).
3. `RT-T6` initially asserted author ends at `3+1`; the real baseline is `3-1`
   because `_mk_ab`'s project creation costs A one credit (`main.py:434`). Fixed
   the assertion to measure `base_a + 1`. Confirms the marketplace 50% transfer
   is correct — NOT a product bug.
4. `RT-P1` slashes (`../`, `%2f`) never reach `{slug}` routing → 404, which is
   equally a rejection; accepted `(403, 404)`.
5. Temporary `db.py` diagnostics (rowcount/commit visibility prints) were fully
   reverted; git-clean audit of `db.py:635-641` afterwards.

## 4. Evidence files
- `audit/tests/test_redteam.py` — the harness (self-contained, isolated, mocked).
- `audit/reports/05-security-redteam.md` — analysis and findings (this ledger's parent).
- `audit/evidence/` — raw outputs captured during each phase (see manifest).

## 5. Gate 2 — Security + AI + Billing + Data (2026-08-30)

Gate-2 tests run from `C:\Users\DELL\fluxswarm\backend` with the same venv python;
`--collect-only` reported **120 tests** for the whole tree; full suite passes.

| Run | Command | Result |
|-----|---------|--------|
| gate2-v1 | `pytest tests/test_{auth_session,password_reset,account_deletion_extended,dispatch_completion,preflight,credit_concurrency,tenant_isolation}.py -q` | 7 failed → refined → **31 passed** |
| gate2-final | `pytest -q` (all) | **126 passed in 14.94s** |
| backup-cli | `python backup.py backup --out <tmp> --source data` | zip written (`.jwt_secret, audit.jsonl, byok.json, users.db`) |
| backup-cli | `python backup.py restore <zip> --target <tmp>` + `verify` | `{"users": 2, "projects": 0, "audit_lines": 65}` = live counts |
| syntax | `python -m py_compile` (12 modules + new tests + `backup.py`) | OK |

New test files (this gate): `test_auth_session.py` (3), `test_password_reset.py`
(6), `test_account_deletion_extended.py` (4), `test_dispatch_completion.py` (4),
`test_preflight.py` (5), `test_credit_concurrency.py` (3), `test_tenant_isolation.py`
(6), `test_backup_restore.py` (6).
New product code (this gate): `backup.py` whole; `db.py` (`logged_out_at` +
`password_resets` + `set_password`/reset fns + widened `delete_user`),
`auth.py` (float `iat`), `main.py` (logout/password/reset routes, `_token_session_ok`,
WS gate, blocking `_bg_dispatch`, `api_account_delete` board purge,
`_fire_dispatch` for buy-template), `hermes_client.py` (`preflight`,
`delete_boards` traversal-safe).

Design changes worth recording for reviewers:
1. **Session invalidation precision** — token `iat` and `users.logged_out_at` are
   both floats so a pre-logout token dies even when minted the same wall-clock
   second as the logout, while a fresh post-logout login in that second still works.
   Integer seconds conflate the two cases (first attempt failed exactly here).
2. **Password reset delivery** — the plaintext token is only echoed over HTTP when
   `FLUXSWARM_RESET_SELF_SERVICE=1` (dev/test). Default OFF; DB stores sha256 only;
   TTL 900 s; single-use. Anti-enumeration keeps identical responses for known and
   unknown emails.
3. **Board purge on account delete** — the API collects only `u{uid}-*` slugs and
   passes them to `delete_boards`; `delete_boards` re-validates every slug against
   `^[A-Za-z0-9_-]{1,120}$` and a resolved-path containment check. Shared
   `flux-demo-*` boards are never deletable.
4. **Monkeypatch trap** — `main.hc` and `hermes_client` are the *same module object*;
   stubbing `main.hc.delete_boards` replaces the very function the restore lambda
   calls. Tests capture `_DEL_ORIG = hc_mod.delete_boards` at import time.
5. **Backup snapshot** — `users.db` is copied through the SQLite `Connection.backup()`
   API, giving a consistent copy even while the live server holds the file; hostile
   zip members (`../outside.db`, nested paths) can never escape the restore target.

## 6. Gate 3 — Product + UX + US/UK + Legal + Pricing (2026-08-30)

Gate-3 tests run from `C:\Users\DELL\fluxswarm\backend` with the same venv python.

| Run | Command | Result |
|-----|---------|--------|
| gate3-new | `pytest tests/test_gate3_ux.py -q` | **14 passed in 2.42s** |
| gate3-final | `pytest -q` (all) | **140 passed in 16.54s** |
| paddle-regression | `pytest tests/test_paddle_flow_api.py -q` | 10 passed (Arabic checkout assertion → EN; cascade fixed) |
| syntax | `python -m py_compile main.py db.py hermes_client.py backup.py` | OK |
| live-restart | `restart_gate3.ps1` (env-loading identical to `run.ps1`) | new uvicorn on :8787, `/health` 200 |
| live-probes | `/`, `/pricing`, `/faq`, 11 legal pages, `/api/plans`, workspace endpoint | EN default, no banned claims, all 200, workspace 200/401 |

New test file (this gate): `test_gate3_ux.py` (14) — page availability + no-mojibake,
EN-default landing, marketing-claims scan, pricing accuracy vs `db.PLANS`, FAQ/How-it-
works scope honesty, legal translation pairs + consumer rights, squad API shape,
workspace auth/ownership rules, full journey (register→login→create→history→tasks→
workspace→password→export→delete), failed-launch credit refund.

Product changes (this gate): `templates/index.html` rewritten (EN-first, History &
results / workspace viewer / Account & data cards, a11y, mobile, accurate copy);
`main.py` adds `GET /api/projects/{slug}/workspace` and rewrites `/pricing`,
`/how-it-works`, `/faq`, `/terms` + `/terms-en`, `/checkout`, `/mock-checkout`
(English; packs not subscriptions; no US-law clause).

Design notes worth recording for reviewers:
1. **EN-first SPA** — default `<html lang="en" dir="ltr">`, browser-`ar` auto-switch
   and manual toggle retained; AR→EN error map `errt()` in the SPA.
2. **Test-staleness discipline** — Gate-3's English checkout conversion legitimately
   broke the old Arabic assertion; the fix is to assert the new English marker, never
   to relax the underlying grant/refund assertions (10/10 paddle-flow still green).
3. **API error bodies stay Arabic** (`غير مصرّح` etc.) — the EN UI maps them; flagged
   P3 for raw-API consumers in the Gate-3 report §24.
## 7. Gate 4 ??? Production Hardening + Performance + Operations (2026-08-31)

Gate-4 tests run from `C:\Users\DELL\fluxswarm\backend` with the venv python.

| Run | Command | Result |
|-----|---------|--------|
| baseline | `pytest -q` (full) | **140 passed** |
| security-rerun | `pytest tests/test_tenant_isolation.py tests/test_auth_session.py tests/test_password_reset.py tests/test_account_deletion_extended.py tests/test_gate3_ux.py tests/test_ratelimit_ip.py -q` | **35 passed** |
| dispatch | `pytest tests/test_dispatch_completion.py -q` | 4 passed (re-pinned 1800) |
| backup/restore | `pytest tests/test_backup_restore.py -q` | 6 passed |
| code changes | `main.py`, `hermes_client.py` (FREE_MODEL + verifier/synth assignee), `test_dispatch_completion.py` | py_compile OK; 140 passed after |

Live evidence (http://127.0.0.1:8787, demo account, 1 credit/launch):

| Probe | Result |
|-------|--------|
| /health | ok:true, hermes_bin_ok:true, limiter_backend:memory |
| restart cycle | fresh uvicorn pid, /health green; users.db persists (2 users) |
| WS no-token | error unauthorized (closed) |
| WS foreign slug | error forbidden (closed) |
| WS owner | snapshot + update frames |
| Launch Run A (pre-fix) | workers running->blocked (hy3-free 401 + non-spawnable assignee) |
| Launch Run B (free-model fix) | 4 workers done w/ artifacts (hello.txt); reviewer/build-fixer queued forever (assignee bug) |
| Launch Run C (both fixes) | 4 workers run, produce hello.txt/package.json; workers stall 'running'; dispatch TimeoutExpired (event-loop stall) |
| backup->restore->verify | restored 4 files; verify = {users:2, projects:4, audit_lines:77} |
| audit.jsonl | dispatch.fire error TimeoutExpired recorded; no secret leak in logs |

**GATE 4 = FAIL** (verified production blocker: Hermes gateway event-loop stall
prevents reliable swarm convergence to a final result). 140 tests all green; 3
config fixes applied in-repo; external P0 documented. STOP RULE active.

Notes:
1. Root cause is external: Hermes gateway event loop stall (`event loop stalled
   10796s / GIL pressure suspected`; 14 events Aug 30-31). Not a FluxSwarm code fix.
2. `FREE_MODEL` `hy3-free` (401 on the opencode-free provider) -> `nemotron-3-ultra-free` (proven OK live).
3. Verifier/synthesizer must be profile-only (`kanban_db` skips non-spawnable
   `terminal lane` assignees) - `hermes_client.py:212-213,280-283`.
4. Dispatch `timeout_s` 600->1800 to fit a full 6-agent serial run.

## 8. Gate 4 - Provider Resilience remediation (2026-08-31)

| Run | Command | Result |
|-----|---------|--------|
| resilience (matrix A-L) | `pytest tests/test_provider_resilience.py -q` | **13 passed** |
| full regression | `pytest -q` | **145 passed** (was 140) |
| py_compile | changed modules (main, db, hermes_client, payments, billing, audit, auth, security, vault, serverlock, ratelimit) | OK |

Live evidence (isolated backend :8792, fresh temp DB + isolated boards; prod DB untouch):
| Probe | Result |
|-------|--------|
| controlled provider-failure E2E | `u2-1788177628-6c8ebd68` running->stuck/blocked, launch_refunded=1, **~253s** |
| real provider-outage E2E | `u2-1788176894-218e79ae` stuck/no_progress in **~86s** (not indefinite) |
| provider health during gate | `https://opencode.ai/zen/v1` -> **404** (site 200; API route down) |
| success E2Es | **PENDING provider recovery** (see GATE-4-PROVIDER-RESILIENCE.md �10.3) |
