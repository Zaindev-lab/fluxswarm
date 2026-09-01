# CLAIM-VERIFICATION — FluxSwarm Audit

Result per claim from Phases 1–5. Statuses: **VERIFIED** (runtime evidence),
**CODE** (source-confirmed), **PARTIAL**, **BLOCKED** (tool/env), **FAILED** (claim
false). Every claim maps to evidence (test id, file:line) or a stated limitation.

| Claim (from discovery docs) | Status | Evidence |
|------------------------------|--------|----------|
| Argon2id password hashing (legacy sha256 auto-upgrade) | CODE | db.py:22-28, auth.py |
| JWT HS256, 7-day expiry, secret from file/env | CODE | auth.py, DEPLOY.md:13 |
| `alg=none` and tampered tokens rejected | VERIFIED | RT03, RT04 |
| No user enumeration on login | VERIFIED | RT02 |
| Brute-force lockout on login | VERIFIED | RT05 (429 after 5 fails; ratelimit.py) |
| Global per-IP rate limit | VERIFIED | RT06 (20/min → 429) |
| Maximum password length enforced | **FAILED** | RT07: 64 KiB password accepted → FLX-AUTH-1 |
| Tenant isolation (read/dispatch/scan forbidden across users) | VERIFIED | RT-T1..T3 (403) |
| User sees only own projects | VERIFIED | RT-T4 |
| Owner guard lets owner through | VERIFIED | RT-T5 |
| Template marketplace: private-flagged owners, public listing, 50% author earn | VERIFIED | RT-T6 (earn = max(1,price//2), db.py:637-638) |
| Cannot buy your own template | VERIFIED | RT-T6a (400) |
| Mock/dev payment endpoints sealed in production | VERIFIED | RT-T7 (404) |
| Any `flux-demo-*` board usable by any user | **FAILED (confirmed)** | RT-T8: 200, no debit; guard main.py:462-463, 472-473 → FLX-DEMO-1 |
| Demo dispatch is metered/rate-limited | **FAILED (confirmed)** | RT-E2: 6× dispatch, no 429, no debit → FLX-DEMO-1 |
| WebSocket requires auth (fail-closed) | VERIFIED | RT-T9 |
| Traversal/path-injection slugs rejected | VERIFIED | RT-P1 |
| Template agent allowlist | VERIFIED | RT-P2 (400) |
| Scriptable template names blocked | VERIFIED | RT-X1 (400) |
| HTML stored safely, escaped at render | CODE | templates/index.html `esc()`; RT-X2 |
| Webhook: unsigned rejected | VERIFIED | RT-W1 (400) |
| Webhook: valid HMAC v2 signature grants plan+credits | VERIFIED | RT-W2 (plan=starter, credits=25) |
| Webhook: exact replay is deduplicated | VERIFIED | RT-W2 (deduplicated=true; payment_events.event_id UNIQUE, db.py:105-113) |
| Webhook: stale timestamp rejected | VERIFIED | RT-W3 (400) |
| Webhook: wrong secret rejected | VERIFIED | RT-W4 (400, plan unchanged) |
| Refund downgrades plan, keeps credits | VERIFIED | RT-W5 (plan=demo, credits=120) |
| Payment gate closed → no free upgrades | VERIFIED | RT-E1 (402) |
| Subscription/Paddle LIVE checkouts work | BLOCKED/UNVERIFIED | no live Paddle keys/domain; `PADDLE_LIVE_CHECKLIST.md` |
| Account deletion removes owned data | PARTIAL | RT-E3 (user + projects rows removed; Hermes boards persist physically → Phase 6 gap) |
| Rate limiting survives restart / multi-instance | UNVERIFIED | in-memory backend (ratelimit.py); single-node only → Phase 7 |
| No secrets in repository / .gitignore | VERIFIED | `.env` ignored; audit never prints secrets |
| Production browser UX safe (XSS/DOM) | BLOCKED | no browser tool (Phase 8) |
| CI runs the correct branch | **FAILED (minor)** | actions trigger on `main`, repo branch is `master` → FLX-CI-1 |
| Anonymous Telegram swarms are capped | **FAILED (confirmed)** | FLX-TG-1 code path (telegram_bot.py, max_spawn=8, no throttle) |
| Refunds can't be double-spent | VERIFIED | RT-W5 + refund idempotency (db.py:650-689; RT-W2 replay) |
| Credit transfer is atomic | VERIFIED | RT-T6 (BEGIN IMMEDIATE, db.py:625-641); backend test_db_refund.py |
| Dispatch without credits impossible on paid boards | PARTIAL | RT-T8: shared demo surface bypasses → FLX-DEMO-1 |

## Verification depth per claim
- **RUNTIME**: exercised through the real ASGI endpoints (TestClient) with mocked
  Hermes subprocesses and isolated stores.
- **CODE**: static line evidence; execution depends on external systems not
  available here.

## Carries forward
- LIVE money path, multi-instance rate limiting, browser checks and host-side
  Hermes sandboxing = BLOCKED (instrumented for Phase 7/8/9/11/12).