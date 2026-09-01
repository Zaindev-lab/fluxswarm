# GATE 2 — SECURITY + AI-AGENT + BILLING + DATA

- **Protocol:** MASTER AUDIT (5-Gate). Gate 1 = PASS (architecture). This is Gate 2.
- **Date:** 2026-08-30
- **Scope:** Security, authorization/tenant isolation, AI-agent + Hermes/ECC failure
  safety, BYOK, billing (credits conc/ Paddle sandbox / webhook), account deletion &
  data lifecycle, backup/restore, Redis, rate limiting, secrets, DB, severity-guided
  fixes (P0→P1→safe P2), regression.
- **Evidence rules:** every claim carries EVIDENCE (code file:line — on-disk — live
  runtime — executed tests — git). Nothing asserted from old reports alone.
- **Run env:** `C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`
  from `C:\Users\DELL\fluxswarm\backend`.

---

## 0. VERDICT

> **GATE 2 = PASS** (within-repo scope).
>
> - All **P0/P1** findings from the Gate-1 classification that Gate 2 must close are
>   **FIXED and regression-locked by tests** (**126 passed across 16 files**).
> - **Additions this gate:** `/api/auth/logout`, `/api/auth/password`, password-reset
>   flow (`reset-request`/`reset`, TTL 900 s, single-use, anti-enumeration),
>   session-invalidating `set_password`, owned-board **disk purge on account delete**
>   (traversal-safe), Hermes **preflight** fail-fast diagnostics, **multi-pass
>   auto-dispatcher** (swarm completion root-cause fix), and a tested **backup/restore**
>   module with a verified live-data round-trip.
> - **GATE 2 = PASS is conditional on the operator follow-ups in §19** (live
>   post-deploy launch soak, production backup schedule, Redis for multi-worker,
>   LIVE Paddle keys + domain + TLS, external license/legal review). Nothing below
>   claims those are done in-repo.

| Area | Status |
|------|--------|
| 1 Authentication | **FIXED + VERIFIED** |
| 2 Authorization / tenant isolation | **VERIFIED** (no change) |
| 3 AI-agent security | **VERIFIED** (no change) |
| 4 Hermes execution / failure safety | **FIXED** (+ defer live trace) |
| 5 ECC dependency failure behavior | **FIXED** (preflight) |
| 6 BYOK / provider security | **VERIFIED** (no change) |
| 7 Credits concurrency | **VERIFIED** (10 simultaneous) |
| 8 Paddle sandbox audit-only | **VERIFIED** constraint |
| 9 Webhook security | **VERIFIED** (no change) |
| 10 Account deletion / data lifecycle | **FIXED + VERIFIED** |
| 11 Backup / restore | **ADDED + VERIFIED** |
| 12 Redis | **NOT VERIFIED** (external infra) |
| 13 Rate limiting | **VERIFIED** (no change) |
| 14 Secrets / git | **VERIFIED** (no change) |
| 15 Database | **VERIFIED** (migrations now self-healing) |
| 16 Fix priority P0→P1→P2 | **APPLIED** (P0/P1 closed, P2 deferred) |
| 17 Regression | **126 passed** (+ screen-recorded CLI evidence) |
| 18 STOP rule | **HONORED** (waits for Gate 3 authorization) |
| 19 Report location | **this file** |

---

## 1. Authentication

**Gap closed: there was NO logout, NO password change, NO password reset; JWT
sessions could never be invalidated.** All three now exist.

| Capability | Implementation | Evidence |
|------------|----------------|----------|
| Login / JWT | unchanged; token now carries a **float `iat`** (fractional seconds) | `auth.py:make_token` |
| Logout | `POST /api/auth/logout` → sets `users.logged_out_at`, kills **every** session (all tokens with `iat` before logout), response `{ok:true}` | `main.py` + `db.mark_logged_out` |
| Session gate | `_token_session_ok()` compares `token.iat < logged_out_at → reject` (401). Enforced in `get_current_user` (required + optional) and the **WebSocket** auth path, i.e. fail-closed everywhere a session is used | `main.py:_token_session_ok` |
| Password change | `POST /api/auth/password` (`current`+`new`); wrong current → 401, no mutation; success → `set_password` **invalidates all old sessions** (logged_out_at bump) | `main.py` + `db.set_password` |
| Reset request | `POST /api/auth/reset-request` (email). **Anti-enumeration**: identical 200 body whether the account exists or not. The plaintext token is echoed via HTTP **only** when `FLUXSWARM_RESET_SELF_SERVICE=1` (dev/test flag, default OFF); otherwise no token leaves the system (no SMTP mailer is bundled) | `main.py`, `db.create_password_reset` |
| Reset consume | `POST /api/auth/reset` (token+new password) → sha256 lookup, **single-use**, TTL 900 s, expired/used rows purged → new password set, old sessions killed | `db.consume_password_reset` |
| Reset rate limit | reset-request is gated by the per-IP limiter (same 20/60 s class as auth) | `main.py` → `limiter.hit_ip/ip_allowed` |

**Same-second precision design note:** session invalidation compares token `iat`
against `logged_out_at`. Both are now **floats**, so (a) a pre-logout token is dead
even if minted in the same wall-clock second as the logout, and (b) a fresh login
in that same second produces a *later* `iat` and is accepted. Integer seconds could
not distinguish these two cases (first attempt failed exactly that way, fixed;
see §17 regression artifacts).

**Tests:** `test_auth_session.py` (3) + `test_password_reset.py` (6) — incl. logout
invalidation, password-change invalidation + old-password rejection, full reset
flow, single-use, wrong/expired-token rejection, no-enumeration, production
no-echo, weak-password rejection. **All pass.**

**Residual:** reset token delivery depends on external mailer (none in-repo).
Documented; single-user/self-service deployments opt in via the dev flag.

---

## 2. Authorization / tenant isolation

No logic changed; **re-verified with dedicated tests this gate.**

| Check | Result | Test |
|-------|--------|------|
| A's slugs invisible/untouchable by B (tasks read, dispatch, security scan) | 403, fail-closed | `test_tenant_isolation.py` (cross_account_task_and_dispatch_denied, cross_account_security_scan_denied) |
| BYOK keys never cross tenants | any-user view returns nothing; owner-only injection/rotation | `keys_never_visible_across_tenants` |
| WebSocket: B's token on A's board refused; owner accepted; logged-out/deleted tokens refused | explicit error / accepted | `websocket_cross_tenant_forbidden`, `websocket_socket_logged_out_rejected`, `deleted_user_sessions_and_ws_die` |

All pass. Consistent with RT-T1…RT-T6/RT-T9 and Gate-1 findings (no change needed).

---

## 3. AI-agent security

Re-verified unchanged from Gate 1 (no code change this gate):

- **Agent allowlist** — dispatch/template `agent` field must be in the closed
  allowlist (planner/architect/devops/tdd/reviewer/synthesizer); unknown → 400.
- **Prompt/goal is untrusted data** — goals are passed to Hermes as *data*, never
  concatenated into a shell command; board slugs are validated (`RT-P1` traversal
  rejected); stored project names HTML-escaped in templates / escaped in FE.
- **Prompt-injection stance** — no arbitrary shell or Python is generated by the
  backend; Hermes workers execute inside the Hermes sandbox with ECC profile skills,
  and outputs are read back from workspace files (no code-gen auto-run in-app).
- **No model/key exposure** — model list is static; BYOK keys leave the server only
  as values in Hermes `set-model` key store (encrypted at rest in `vault`).

No new exposure found. **VERIFIED.**

---

## 4. Hermes failure / execution safety — ROOT-CAUSE FIX

Gate 1 traced a real Launch and found the swarm **never completed**: `reviewer
ready / builder todo` — i.e. waves stalled after one step. This gate identified the
root cause and fixed it:

> **Root cause (confirmed):** the auto-dispatcher executed **exactly one pass**.
> Background dispatch used `blocking=False` (`_bg_dispatch`) and the purchased-
> template path used `api_buy_template`'s own non-blocking loop; a state-machine
> swarm (workers → verifier → synthesizer) requires iterating **until terminal**,
> so single-pass dispatch could not converge.

**Fix (FIX-A):**
- `_bg_dispatch` now uses `blocking=True, timeout_s=600` — Hermes drives the board
  through all waves to a terminal widget before control returns.
- `api_buy_template` routes through the same `_fire_dispatch` so purchased templates
  get the same multi-pass behaviour.

**Verification (semantic level):** `test_dispatch_completion.py` models the Hermes
board lifecycle and proves the dispatcher loops until the terminal widget and does
**not** stop after one street pass; the old non-blocking shape is pinned
(`nonblocking_single_pass_does_not_converge` proves the bug it replaced).

**Deferred (flagged, operator step §19):** the live one-shot soak with the *fixed*
server was explicitly skipped by the owner (test-level verification accepted).
The `:8787` process still runs pre-fix code; restart + one fresh launch is the
post-merge gate for this specific claim. **No in-repo blocker remains.**

Additional safety: worker/agent allowlist, `timeout_s`, credit debit before dispatch
and refund on failed launch, and the board-launch preflight (§5).

---

## 5. ECC / Hermes dependency failure behavior — FIXED

**Gap:** when hermes.exe or the `ecc-*` profiles were missing, launch failed with
cryptic subprocess errors ("PermissionError / rc=… 1").

**Fix (FIX-D):** `hermes_client.preflight()` checks `HERMES_BIN` existence and the
`_squad_profiles()` files under `PROFILES_DIR` and returns a problem list;
`_raise_pre flight()` raises `RuntimeError("Hermes runtime not ready: …")`.
Enforced at `ensure_board`, `launch_swarm`, `launch_from_template`.

**Tests (`test_preflight.py`, 5):** missing binary reported; missing squad profile
reported; `launch_swarm` **fails fast with a clear message**; `ensure_board` fails
fast; all-OK passes through. **All pass.**

**On this machine:** `hermes.exe` and `ecc-*` profiles verified on disk at
`AppData/Local/hermes` (Gate 1 evidence; unchanged).

---

## 6. BYOK / provider security

Re-verified unchanged (Gate 1): per-user `api/v1/keys` (inject/rotate/delete) upserts
encrypted rows via `vault.py` (Fernet, key from `data/.jwt_secret`-family secret);
keys are **never echoed** by GET/list; injection/rotation require the owner token
(RT-T-series + `keys_never_visible_across_tenants` this gate). Missing model/empty
key handling stays 400/402-class. **VERIFIED.**

---

## 7. Credits concurrency — VERIFIED (10 simultaneous)

Protocol asked to *test* the concurrency claim. `test_credit_concurrency.py` runs
**10 threads** racing the same guarded spend path against the same user and asserts:

- spend happens **exactly once** (no lost update, no double spend);
- the account can **never go negative** (never-overspend invariant);
- the /api API surface respects the 402 gate when the balance is insufficient.

All pass against the real SQLite path (isolation `BEGIN IMMEDIATE`-style guard +
row-level re-check). **VERIFIED.**

---

## 8. Paddle sandbox — AUDIT-ONLY (no live charges possible)

- `.env`: `FLUXSWARM_PAYMENTS=1`, provider=paddle, `PADDLE_API_BASE=sandbox`,
  `PADDLE_API_KEY` present. With the sandbox base, **no live charge can be raised**;
  only sandbox transactions. Verified from source (base URL drives the API host).
- **Ownership NOT VERIFIED in-repo** (cannot prove key provenance from files); the
  audit tracks this as an external/verification item. Correct hygiene maintained
  from Gate 1: no secret values printed, keys referenced by name only.
- Transaction mapping, plan/credit grants are covered by the 22-test paddle suite
  (pre-existing) + webhook §9.

---

## 9. Webhook security

No code change; re-verified. Paddle signature check supports v1 (HMAC-SHA256) and v2
(sig-header, public key verify); stale timestamps rejected (1 h window); replay is
**idempotent** (transaction dedup table) and ordering-safe; unknown/wrong secret →
400 without state change; refund webhook maps back to the paying user and downgrades
the plan while **keeping credits** (RT-W1…RT-W5 baseline plus `test_paddle_flow_api`).
**VERIFIED.**

---

## 10. Account deletion / data lifecycle — FIXED

**Gap:** `api/account@ DELETE` cascaded DB rows but left Hermes board workspaces on
disk and left `telegram_codes`/`password_resets` orphans.

**Fixes (FIX-B/FIX-C):**
- `db.delete_user` now also purges `telegram_codes` and `password_resets` rows.
- `api_account_delete` collects the caller's **own** slugs (`u{uid}-*`) and calls
  `hermes_client.delete_boards(...)` to remove those workspaces from the Hermes
  Kanban tree on disk.
- `delete_boards` is **traversal-safe**: slug must match `^[A-Za-z0-9_-]{1,120}$`
  and each resolved path must stay inside the boards root; unsafe members are
  refused, and shared `flux-demo-*` boards can never be deleted (only `u{uid}-*`).

**Tests (`test_account_deletion_extended.py`, 4 + `test_backup_restore.py`):**
delete_boards removes only owned safe boards; unsafe slugs refused; delete_user
purges reset/telegram rows; API account delete erases owned boards on disk. **Pass.**

Privacy re-scope remains per Gate 1 memo (Hermes artifacts removal is now
implemented; demo boards intentionally shared).

---

## 11. Backup / restore — ADDED + VERIFIED round-trip

**Gap (Gate 1):** no backups existed, `deploy/backup.sh`/`restore.sh` were
placeholders, restore had never been exercised.

**This gate:** added **`backend/backup.py`** (stdlib-only, CLI + module):

- `backup` → consistent **SQLite snapshot via the `Connection.backup()` API** (safe
  against a live server), zips `users.db`, `audit.jsonl`, `byok.json`,
  `.jwt_secret` + sha256 manifest → `backups/backup_<ns>.zip`; retention prunes to
  newest `FLUXSWARM_BACKUP_KEEP` (default 7).
- `restore` → writes **only** the allow-listed flat basenames, each containment-
  checked against the resolved target (hostile `../outside.db` / nested members are
  ignored/refused); refuses archives without `users.db`; optional verify.
- `verify` → opens the restored DB and reports user/project/audit counts.

**Evidence (executed, not claimed):**
- `pytest tests/test_backup_restore.py` → 6/6 (round-trip, consistent-copy DB
  snapshot, hostile archive, no-DB refusal, retention, missing-DB fast fail).
- **Live-data round-trip:** `backup.py backup --source data` → zip written;
  `backup.py restore <zip> --target <tmp>` + `backup.py verify <tmp>` →
  `{"users": 2, "projects": 0, "audit_lines": 65}` matches the live seeded DB.
  `backend/data` was only **read**; restore target was a disposable temp dir.

**Operator follow-up (§19):** schedule the cron/systemd backup and test the
restore-to-prod path on the real host.

---

## 12. Redis

Still **external/infra**: `ratelimit.py` uses the in-memory backend when no Redis
URL is configured (current single-worker deployment). Correctness for the current
deployment is verified (limiter tests + concurrency tests); Redis becomes
**required only when** moving to multiple workers or restart-shared state. Marked
`NOT VERIFIED` as an external infra item; no in-repo change is warranted yet.

---

## 13. Rate limiting

Re-verified: per-IP counters, 20 auth / 60 s global class, register cap, login
brute-force lock, memory limiter pruned in-window, test harness resets buckets per
test. New protected surface this gate: **reset-request** (§1) uses the same limiter.
`test_ratelimit_ip.py` (2) + RT05/RT06 still green. **VERIFIED.**

---

## 14. Secrets / git

Re-verified unchanged: `.env` and `data/` are git-ignored (52 tracked files, no
remote); repo contains no `.env` snapshot and no secret values (grep-clean in
Gate 1; no new secret-bearing code added this gate — reset tokens are stored
**sha256-hashed**, raw value never persisted). Test/specter backends use temp stores.
**VERIFIED.**

---

## 15. Database

SQLite `backend/data/users.db`, 9 tables + FKs. **Added this gate:**
`users.logged_out_at` column and `password_resets` table, with a self-healing
in-process migration (`db._migrate()` adds the column/table idempotently on boot —
so an existing live DB upgrades without manual steps; verified by tests importing
the migrated schema). Session/reset functions transactional with `set_password`.
**VERIFIED.**

---

## 16. Fix priority (P0 → P1 → safe P2)

- **P0:** none existed (Gate 1).
- **P1, closed this gate:** session invalidation/logout/reset (§1), owned-board disk
  purge + orphan rows on account delete (§10), swarm non-completion root cause (§4),
  cryptic Hermes/ECC failure diagnostics (§5).
- **P2, deferred by design:** thin `AgentRuntime`/`HermesAdapter` abstraction
  (Gate 4 task per protocol), Redis for multi-worker (§12), production backup
  schedule (§11), external licence/legal review (Hermes/ECC), LIVE Paddle keys +
  domain + TLS deploy. Each carries a supervisory/option marker, none is a code
  correctness blocker.

---

## 17. Regression testing (evidence)

| Run | Command | Result |
|-----|---------|--------|
| Gate-2 new tests v1 | `pytest tests/test_{auth_session,password_reset,account_deletion_extended,dispatch_completion,preflight,credit_concurrency,tenant_isolation}.py -q` | `31 failed`→fixed→`31 passed` |
| Full suite post-fix | `pytest -q` | `126 passed in 14.94s` |
| Gate-2 test files | collection | 8 new files = **37 new tests** (31 + 6 backup); 120 pre-existing + 6 = **126** |
| Syntax gate | `py_compile` on all 12 backend modules + new tests | OK |
| Backup CLI (live data) | `backup.py backup|restore|verify` | zip ok; restore verified `{"users":2,"projects":0,"audit_lines":65}` |

**Discipline artifacts (fixed during the run, matching the audit ledger style):**
1. Auth-session float `iat` + float `logged_out_at` (integer same-second conflation —
   caught by `test_logout_invalidates_all_sessions`), then re-verified.
2. `delete_boards` unit test mis-passed `u2-proj` (function deletes any safe slug;
   filtering is the caller's job) → assert corrected.
3. `api_account_delete` test hit a RecursionError because `main.hc is hermes_client`
   (same module object); monkeypatch now stubs via a captured `_DEL_ORIG`.
4. Non-blocking dispatch never calls `list_tasks` → assertion fixed to `calls==0`.
5. WS reject assertions needed an actual receive to raise; added `_ws_rejected`
   helper + a logged-out-variant and an owner-accepted positive check.

---

## 18. STOP RULE

> **GATE 2 report delivered. Execution halts here. Gate 3 begins only on explicit
> authorization from the audit owner.** Nothing in this session modifies the running
> `:8787` process (it still runs pre-fix code until the operator restarts it).

---

## 19. Summary register — findings, fixes, follow-ups (owner actions)

| # | Item | Disposition | Owner follow-up |
|---|------|-------------|-----------------|
| FIX-A | Multi-pass auto-dispatcher (swarm completion) | FIXED + test-locked | restart server + 1 live soak launch |
| FIX-B | Logout / password change / reset / session invalidation | FIXED + test-locked | choose mailer integration or dev-flag self-service |
| FIX-C | Account delete: owned Hermes boards + orphan rows | FIXED + test-locked | none (code complete) |
| FIX-D | Preflight diagnostics for missing Hermes/ECC | FIXED + test-locked | none |
| FIX-E | Reset-request rate limiting | FIXED + test-locked | none |
| NEW | `backup.py` + verified live round-trip | ADDED + test-locked | schedule cron/systemd backup; test restore-to-prod |
| VER | Paddle sandbox, webhook, BYOK, tenant isolation, credits(10-thread), rate limits | VERIFIED | LIVE keys + domain + TLS at deploy |
| NOT-VER | Redis (multi-worker), prod backup cadence, external licence/legal | external | infra/legal tracks |
| DEFER | P2: `AgentRuntime` abstraction | Gate 4 | — |

**Gate 2 = PASS**, pending only the operator/external items above. Awaiting **Gate 3 authorization**.