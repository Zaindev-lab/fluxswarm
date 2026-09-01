# 03 — Architecture & Code Audit

Phase: 3 · Evidence: CODE inspection (file:line) · Date: 2026-08-30

## Separation of concerns — mostly good
- Clean layering: `main.py` (API/routes) · `db.py` (persistence + transactions) · `auth.py` (JWT) · `payments.py` (gateway abstraction) · `vault.py` (encryption) · `ratelimit.py` · `audit.py` · `serverlock.py` · `hermes_client.py` · `security.py`.
- Provider-agnostic billing interface (`PaymentGateway` ABC, `payments.py:37`) with safe stub default.
- Env live-read pattern (CORS/proxies/payments gate) avoids stale config (main.py:46,113,154).

## Findings

### GAP-1 Absent email subsystems (P1, product gap)
No email verification, no password reset, no transactional email anywhere (`grep` for smtp/sendgrid/reset = none). A password-locked or mistyped-email user has no self-service recovery. Launch gap for a paid US/UK product (support burden, risk of account locks).

### ARCH-1 Raw internal errors leak to clients (P2)
`api_create_project` returns `HTTPException(500, detail=str(e))` (main.py:447); `api_tasks`/`api_dispatch` same (main.py:467,477); `api_security` same (main.py:1248). `hc.dispatch` raises `RuntimeError(f"dispatch failed: {r.stderr}")` (hermes_client.py:271) — real path names, command args, and CLI stderr are echoed to the browser. Information disclosure + poor error UX.

### ARCH-2 Blocking subprocess inside async WS handler (P2)
`ws_board` calls `hc.list_tasks(slug)` (a synchronous `subprocess.run` with `timeout=300`) directly inside the async coroutine every 4 s (main.py:1287-1291). A stuck Hermes CLI would stall the entire event loop (all HTTP + WS). Should run in a thread pool (`run_in_executor`) or `asyncio.to_thread`.

### ARCH-3 Per-socket Hermes CLI spawns (P2 perf)
Every WS client polls the board by spawning a new `hermes` subprocess every 4 s (main.py:1287, `_run` at hermes_client.py:158). N open tabs × process spawn = unnecessary fork load; a proper file-watcher or single poller broadcasting to `_SUBS` would scale.

### ARCH-4 Duplicate / dead debug surface (P2)
`/api/payments/debug-verify` (main.py:619-657) and `_debug_webhook_failure` (main.py:660-704) are TEMPORARY diagnostics shipping in `main.py`. Debug-verify echoes `secret_len` + truncated `secret_sha` (localhost-gated) and writes request bodies to the temp dir. Must be removed or flag-gated before launch (they also skip the `gateway_operative` requirement for `debug-verify` — inconsistent).

### ARCH-5 Duplicated refund logic (P3)
`api_create_project` refunds with a manual `UPDATE users SET credits=credits+1` (main.py:441-447) while `db.add_credit()` (db.py:701) exists and is used by the bot. Also: `ensure_board`/`add_project` ordering means an `ensure_board` exception leaves the credit spent (no try around it, main.py:437-448).

### ARCH-6 Missing password max-length (P3)
Register enforces min 8 (main.py:369-370) but no max; login accepts arbitrary length. Argon2 cost scales with input → modest DoS, mitigated by the 20/min/IP and 10/h/IP caps and Caddy's 1 MB body cap. Recommend `max_length=1024` on both schemas + trim email.

### ARCH-7 Sync webhook handler in async route (P3)
`api_payments_webhook` is `async def` but runs blocking `handle_webhook` + `db.record_payment_event` synchronously (main.py:587-611,714). Blocks the loop during signature + DB work — minor at current scale.

### ARCH-8 Duplicate operative check (P3)
`if not getattr(gw,"operative",False)` appears twice in `api_payments_webhook` (main.py:596 and 602-605); second branch is unreachable dead logic.

### ARCH-9 Missing index on projects(user_id) (P3)
`list_user_projects` filters `WHERE user_id=?` on an unindexed column (db.py:71-79). Fine at small scale; add index before scale.

### ARCH-10 No explicit transactions for template publish (P1 for integrity)
`publish_template` (db.py:571) has no `BEGIN IMMEDIATE`; `buy_template` does (db.py:616). Review: `buy_template` grants author 50% live on purchase and `refund_template_purchase` claws back (db.py:650) — both atomic. Publish is a single INSERT — fine.

### Strengths verified by code
- `deduct_credit`/`buy_template`/`reward_referrer_once`/`refund_template_purchase` all use `BEGIN IMMEDIATE` + rowcount claims (db.py:267,616,296,650) — race-free money/credit ops.
- All SQL is parameterized (no string interpolation into SQL anywhere).
- Input hardening: `_clean_text` rejects `< > script javascript: onerror=` for templates and caps lengths (main.py:1090-1116).
- No shell=True anywhere; goal/args passed as argv lists (hermes_client.py:141-158).
- CORS deny-by-default with wildcard hard-error (main.py:46-79); trusted-proxy XFF handling (main.py:326-343).
- Secrets only in `.env`/persisted files outside git; `audit.py` strips sensitive keys (audit.py:25-29).

## Summary
Architecture is coherent and the transactional core is genuinely race-safe. The audit-blocking issues live in robustness (ARCH-2), information disclosure (ARCH-1), and absent auth-recovery UX (GAP-1) rather than in the core logic.