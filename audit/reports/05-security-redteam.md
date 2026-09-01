# 05 — Security & Red Team Report

Phase: 5 / 19 — Repository: `C:\Users\DELL\fluxswarm@master`
Method: threat-model-driven runtime + code verification, no product code changes.

## Scope & rules of engagement
- Target: the real FastAPI app (`backend/main.py`) via ASGI `TestClient`.
- Isolation: temp SQLite via `FLUXSWARM_DB`; `vault._STORE`/`audit.AUDIT_FILE`
  repainted to temp; `FLUXSWARM_PAYMENTS`/`FLUXSWARM_PAYMENT_PROVIDER` popped.
- `hermes_client` subprocess calls monkeypatched to no-ops → **no real Hermes
  swarm, token, board or cost was ever incurred**; demo boards exercised only by
  mock-backed endpoints.
- Live data, production keys and the running server (pid 19896) were untouched.

## Full result
- 29/29 red-team tests pass. 26 checks confirm defenses hold; **3 checks confirm
  real weaknesses** (two findings). Evidence ledger: `audit/TEST-LEDGER.md`.

## Confirmed defenses (runtime-verified)

| Area | Evidence |
|------|----------|
| Auth roundtrip, 7-day JWT | RT01 |
| No user enumeration (identical 401 body) | RT02 |
| `alg=none` rejected | RT03 |
| Tampered token rejected | RT04 |
| Brute-force lockout (5 fails → 429) | RT05 |
| Global IP cap (20/min) enforced | RT06 |
| Tenant isolation: read/dispatch/scan 403 | RT-T1..T3 |
| Project list per-owner isolation | RT-T4 |
| Owner guard passes for owner | RT-T5 |
| Marketplace isolation + 50% author earn | RT-T6, RT-T6a |
| Mock/dev endpoints sealed when mock off | RT-T7 |
| WS fail-closed (unauthenticated → explicit error) | RT-T9 |
| Traversal / command-injection slugs rejected | RT-P1 |
| Unknown template agents rejected (allowlist) | RT-P2 |
| Scriptable template names rejected | RT-X1 |
| HTML stored as data (frontend `esc()`) | RT-X2 (code: templates/index.html `esc`) |
| Webhook: unsigned/stale/wrong-secret rejected | RT-W1, W3, W4 |
| Webhook: valid v2 sig grants; exact replay dedup | RT-W2 |
| Refund downgrades plan but preserves credits | RT-W5 |
| Payment gate closed → no free upgrade | RT-E1 |
| Account delete cascades owned rows | RT-E3 |

## Findings confirmed by runtime evidence (Phase 5)

### FLX-DEMO-1 — Shared `flux-demo-*` boards: read + unbounded unpriced dispatch (HIGH)
Status: **RUNTIME-VERIFIED** (RT-T8, RT-E2). Owner guard at `main.py:462-463` /
`main.py:472-473` permits any authenticated user to view/dispatch ANY slug
`flux-demo-*`. RT-T8: arbitrary `flux-demo-9999999999` is readable (200) and
dispatchable (200) with **zero credit debit**; RT-E2: 6 back-to-back dispatches
succeed with no 429 and no spend.
Impact: public read of shared boards; unbounded platform-cost window (each
dispatch targets a real Hermes board in production). No per-endpoint quota.
Severity: HIGH (economics/availability), not direct cross-tenant data leak.
Fix plan: Phase 15 (pin demo-session ownership or per-user quota + hard spend cap).

### FLX-TG-1 — Anonymous Telegram swarms without quota/caps (HIGH)
Status: CODE-VERIFIED (Phase 4); runtime exercise requires live Hermes → not
rerun here. `telegram_bot.py` allows unauthenticated `/launch` up to
`max_spawn=8` with no per-chat or per-IP throttle. Impact: free unbounded
compute cost.
Fix plan: Phase 15 (explicit per-chat rate limit + global concurrency cap +
budget guard; mirrors money-path limits).

### FLX-AUTH-1 — No maximum password length (MEDIUM)
Status: RUNTIME-VERIFIED (RT-07): a 64 KiB password registers successfully
(not a clean 422). Argon2id hashing cost scales with input RAM/time at
registration & login; 64 KiB inputs are the documented maximum and not attacker
controlled per-hit, but a bulk-registration vector amplifies hashing cost and DB
size. Recommend `password_max = 1024`.
Severity: MEDIUM (DoS amplification), low real-world exploitability.

### ARCH-1 — Raw Hermes error text leaks on failure (MEDIUM)
Status: CODE-VERIFIED (`main.py:447` raises `str(e)` to client; `hermes_client`
exceptions include subprocess stderr). Runtime: project creation succeeded under
mock; failure path not exercised end-to-end (would need a real failing subprocess).
Fix plan: Phase 15 (map to generic "launch failed" + server-side log).

### ARCH-2 — Blocking subprocess inside async websocket loop (MEDIUM)
Status: CODE-VERIFIED (`main.py` WS handler calls `hc.list_tasks`/`ensure_board`
synchronously per poll). Starvation under load, not a security breach.
Fix plan: Phase 15 (offload to worker thread / `run_in_executor`).

### GAP-1 — No email verification / password reset (MEDIUM gap)
Status: CODE-VERIFIED (auth module has no verification or reset flow). Blocked
by no external mail infra configured. Affects account-takeover safety for
recovered accounts and GDPR account hygiene.
Fix plan: Phase 15 decision (post-MVP; document as known limitation).

## Defense-in-depth observations (non-blocking)
- `db.py` credit txn uses `BEGIN IMMEDIATE`; refund is idempotent (RT-W2,W5).
- Webhook replay dedup via `payment_events.event_id UNIQUE` (RT-W2).
- `security.py` tests subprocess without `shell=True` (Phase 3 ARCH notes).
- CORS deny-by-default; secrets stored via Fernet vault, gitignored.

## Not verifiable in Phase 5 (BLOCKED / UNVERIFIED — carried to later phases)
| Item | Status | Reason |
|------|--------|--------|
| LIVE Paddle keys & webhook trust chain end-to-end | UNVERIFIED | no live keys / domain (`PADDLE_LIVE_CHECKLIST.md`) |
| Browser XSS/DOM exploitation & a11y | BLOCKED | no browser tool in this environment (Phase 8) |
| Hermes skills sandboxing on the host | UNVERIFIED | outside repo (Hermes install attests its own policy) |
| OWASP ZAP / Burp dynamic scan | BLOCKED | same tool constraint |

## Verdict (Phase 5 technical posture)
No Critical findings proven. Two HIGH-class economics/abuse findings
(FLX-DEMO-1, FLX-TG-1) plus one MEDIUM auth-flaw (RT-07) need Phase 15 repairs
before launch. Authentication, tenant isolation, webhook integrity, replay
protection, refunds and credit gating are **working as designed**.

Evidence: TEST-LEDGER.md, test_redteam.py.