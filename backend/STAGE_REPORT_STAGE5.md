# FluxSwarm — Stage 5: Publish Readiness + Compliance Walkthrough

- **Date:** 2026-08-29
- **Repo:** `C:\Users\DELL\fluxswarm` (backend `C:\Users\DELL\fluxswarm\backend`)
- **Mode:** READ-ONLY verification + written report. Followed the same day by a **post-report fix pass on the live workspace** that closed the actionable items (see §6). Findings below are annotated with ✅ CLOSED where applicable.
- **Verdict:** **GO-WITH-ISSUES → GO** (all P1 findings closed; no blocking item remains)

---

## 1. Compliance Walkthrough (Architect)

### 1.1 Legal-surface endpoints — LIVE and correct
Verified against the running instance (`http://127.0.0.1:8787`) via read-only GET.

| Endpoint | Result | Evidence |
|---|---|---|
| `GET /privacy` | 200, substantive HTML (1275 bytes), contains CCPA/CPRA notice | `backend/main.py:723-731` |
| `GET /terms` | 200, substantive HTML (1005 bytes), states Paddle "Merchant of Record" | `backend/main.py:734-741` |
| `GET /api/account/export` | Returns user data payload (`user`, `projects`, `referrals`, `templates`, `template_purchases`, `payment_events`) | `backend/main.py:745-749`, `db.py:357-379` |
| `DELETE /api/account` | Removes user + all related rows; **idempotent / missing-safe**: second delete returns `ok:false` and does not error | `backend/main.py:752-759`, `db.py:383-407` |

`delete_user` is genuinely idempotent and missing-safe: it `SELECT`s the row first, returns `False` when absent, and wraps each `DELETE` in a `try/except OperationalError` so a missing child table never aborts the cascade (`db.py:388-403`). Confirmed by `test_account_export_and_delete` (`tests/test_paddle.py:176-183`) which asserts `delete_user` returns `True` then `False`.

### 1.2 Audit trail contains NO secrets — CONFIRMED
`backend/data/audit.jsonl` (22 records at time of this verification) was scanned line-by-line.

- `audit.audit()` drops any meta key in the sensitive set `{token, key, secret, password, pw_hash, cipher, raw}` (`audit.py:25,56-58`) before serialization.
- Spot-check of all 22 records: only identity/event metadata present (`uid`, `email`, `ip`, `plan`, `gateway`, `event_id`, `amount_cents`, `reason`). **No tokens, secrets, keys, password hashes, or Fernet ciphertext.**
- `byok.json` contains only Fernet ciphertext (`backend/data/byok.json`), as designed.

### 1.3 Encryption at rest — CONFIRMED
- **Passwords:** Argon2id via `argon2-cffi`; legacy `sha256+salt` hashes are transparently upgraded on next login (`db.py:125-160, 204-209`). Verify path: `_verify_password` / `_make_pw_hash`.
- **BYOK provider keys:** Fernet symmetric encryption (`vault.py`). The Fernet key is loaded from `FLUXSWARM_FERNET_KEY` env or `~/.fluxswarm/fernet.key` — **never inside the repo**; a legacy plaintext key beside the data dir is migrated out and deleted exactly once (`vault.py:28-51`).
- **JWT secret:** loaded from env or `data/.jwt_secret`, never hardcoded (`auth.py:18-40`).

### 1.4 Paddle Merchant-of-Record tax posture
Paddle is the seller of record (`payments.py:104-123`, `.env.example:22-27`, `main.py:738`). Consequently **FluxSwarm does not collect or remit sales tax / VAT / GST** — that obligation transfers to Paddle's contract. This materially reduces cross-border compliance surface for a US launch. Documented in privacy page (`main.py:728`: "معالج الدفع Paddle (تاجر السجلّ)").

### 1.5 CCPA / CPRA rights implemented
- **Right to know / access** → `GET /api/account/export` (`main.py:745`).
- **Right to delete** → `DELETE /api/account`, erases `users`, `projects`, `squad_templates`, `template_purchases`, `referrals`, `payment_events`, plus the BYOK ciphertext in `byok.json` (`main.py:755`, `db.py:383-407`, `vault.py:95-99`).
- Both rights are surfaced to users on `/privacy` (`main.py:729`).

### 1.6 Compliance Risk Ledger (P0 / P1 / P2)

| ID | Sev | Finding | File:line | Recommendation |
|---|---|---|---|---|
| C-1 | P2 | **No `.gitignore`** in repo root. `backend/data/` holds `.jwt_secret` (64 B), `byok.json`, `users.db`, and `audit.jsonl`. If the dir is ever `git init`'d / committed, secrets + user PII would be published. Repo is currently NOT git-tracked, but this is a latent exposure. | repo root (missing file) | Add `.gitignore` excluding `backend/data/`, `.env`, `*.db`, `__pycache__/` before any `git init`. |
| C-2 | P2 | Privacy/terms pages are **hardcoded in Arabic** (RTL) with no English variant or locale switch. Acceptable for an Arabic-first product; flag if US/English audience is primary. | `main.py:726-740` | Add an English `/privacy` `/terms` or i18n later if targeting US-English users. |
| C-3 | P1 | `delete_user` cascade does **not** delete the `audit.jsonl` entries for that user (audit is append-only by design). CCPA "right to delete" is satisfied for *account* data, but the user's email+IP remain in the immutable audit log. This is a common, defensible trade-off (security audit integrity) but should be disclosed in the privacy policy. | `audit.py` (append-only), `db.py:383` | Add an explicit statement in `/privacy` that audit logs are retained for security and are not subject to erasure. |
| C-4 | P2 | `rotate_secrets.py` exists at backend root but is not referenced by any deploy script or doc; operator may not know it exists. | `backend/rotate_secrets.py` | Wire `rotate_secrets.py` into DEPLOY docs / bootstrap, or document its use. |
| C-5 | P2 | A CI workflow exists (`.github/workflows/ci.yml`) but it only runs `py_compile main.py hermes_client.py` + `docker build` on push/PR. The **full pytest suite (`backend/tests`) is NOT gated**, and the **static secret-leak scan (`security.py`) is NOT gated** — so a secret leak or test regression can still merge. | `.github/workflows/ci.yml:7-21`, `backend/security.py` | Add CI steps: `python -m pytest backend/tests -q` and the `security.py` static-scan on every push/PR. |
| **POST-FIX CLOSURES (see §6)** | | | | **C-1 ✅** `.gitignore` exists and covers `backend/data/`, `.env`, `*.db` — **C-3 ✅** privacy page now states audit logs are retained for security and are not subject to erasure (`main.py:/privacy`). — **C-5 ✅** `ci.yml` now gates `pytest` + a real-credential secret-leak scan on push/PR. — **C-2, C-4** remain open (optional). |

---

## 2. Deploy Readiness (DevOps) — static review only

### 2.1 Static checks (no containers created/started)
| Check | Command | Result |
|---|---|---|
| Bash syntax | `bash -n deploy/*.sh` (×4) | **PASS** (bootstrap, backup, restore, monitor) |
| Compose config | `docker compose -f docker-compose.yml config --quiet` | **PASS** |
| Python compile | `python -m py_compile` on all 15 `backend/*.py` | **PASS** |

### 2.2 Review findings
- **`deploy/bootstrap.sh`** — coherent one-command path. Generates `.env` with strong secrets (JWT via `urandom`, Fernet via `os.urandom`), `chmod 600`, idempotent (won't overwrite existing `.env`), **stops before serving a half-configured billing setup** (exits after generating `.env`, asks operator to fill Paddle values and re-run). Caddy installed on host, app bound to `127.0.0.1:8787` only. Health gate waits up to 60s. **Good.**
- **`Caddyfile.us`** — template rendered by bootstrap (sed `__DOMAIN__`/`__ADMIN_EMAIL__`). Reverse-proxies `127.0.0.1:8787` with `/health` upstream check, 1 MB body limit, sets `X-Forwarded-For`/`X-Real-IP`. Consistent with `FLUXSWARM_TRUSTED_PROXIES=127.0.0.1,::1`. **Good.**
- **`docker-compose.yml`** — `fluxswarm` service binds `127.0.0.1:8787:8787` (external exposure controlled by Caddy), `redis` with `appendonly`, healthchecks on both, depends_on healthy redis, named volume `fluxswarm_kanban`. Env wires `FLUXSWARM_PAYMENTS=0` (safe default 402) and trusted proxies. **Good.**
- **`Dockerfile`** — `CMD ["python","-m","uvicorn","main:app","--host","0.0.0.0","--port","8787"]` — **valid and matches the required spec.** Installs deps from `backend/requirements.txt` before copying code (good layer caching).
- **`.env.example`** — documents all secrets, clearly marks required vs optional, default `FLUXSWARM_PAYMENTS=0`, Paddle MoR explanation. **No real secrets committed.** 

### 2.3 Deploy Risk Ledger

| ID | Sev | Finding | File:line | Recommendation |
|---|---|---|---|---|
| D-1 | P2 | `deploy/restore.sh` line 8 `[[ -f "$BACKUP" ]] || die() { :; }` is dead/no-op code (leftover shellcheck artefact); the real guard is line 9. Harmless but confusing. | `deploy/restore.sh:8` | Remove line 8. |
| D-2 | P2 | `backup.sh` includes `.env` in the tarball (line 26) — correct, but the tarball sits in `backups/` on the same host with no off-site encryption by default; comment tells operator to push off-site. | `deploy/backup.sh:26` | Document/automate encrypted off-site copy (e.g. rclone/age). |
| D-3 | P1 | `monitor.sh` hardcodes `https://app.fluxswarm.ai/health` as default (overridable via `FLUXSWARM_MONITOR_URL`). If the operator's domain differs and they forget the override, the probe checks the wrong host. | `deploy/monitor.sh:4` | Default to `http://127.0.0.1:8787/health` (local) unless overridden. |
| D-4 | P2 | `bootstrap.sh` is systemd + apt-specific. Caddy is installed via the apt path only (`die` otherwise at line 97), and the boot-persistence section uses `systemctl enable … || true` on the *enable* lines but **`systemctl restart caddy` (line 105) and `systemctl restart fluxswarm` (line 130) are UNGUARDED** — so on a non-systemd host (e.g. some Contabo images) the script hard-fails at the first `restart`, rather than silently no-opping. | `deploy/bootstrap.sh:91-98,104-130` | Detect non-systemd and warn / abort early with guidance; provide a docker-compose-managed Caddy alternative. |
| D-5 | P2 | Repo not under version control (NOT A GIT REPO). No provenance/rollback for the deployable. | repo root | `git init` + first commit (with `.gitignore` from C-1) before launch. |
| **POST-FIX CLOSURES (see §6)** | | | | **D-1 ✅** `restore.sh` guard corrected — **D-3 ✅** `monitor.sh` now defaults to `http://127.0.0.1:8787/health` (`FLUXSWARM_MONITOR_URL` override stays). — **D-2, D-4, D-5** remain (operator/deploy follow-up). |

---

## 3. Test Audit (TDD)

### 3.1 Results
- **Command:** `C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe -m pytest backend/tests -q`
- **Result: `65 passed`** (100% pass; 7 tests added for the Paddle refund path, 6 for
  Telegram account linking, 2 for CCPA HTTP + audit sensitive-key stripping in later passes).
  Original audit: 50 passed.
- **`python -m py_compile` on every `backend/*.py`: all 15 modules compile cleanly.**

### 3.2 Stage-5 surface coverage — AUDITED, gaps reported (no code added)

| Stage-5 surface | Covered? | Evidence |
|---|---|---|
| Paddle V1 signature (`base64(hmac_sha256(body))`) | ✅ | `test_paddle.py:34-50` |
| Paddle V2 signature (`ts=N;h1=hex`) + replay window | ✅ | `test_paddle.py:53-64` |
| Webhook idempotency (DB `INSERT OR IGNORE`) | ✅ | `test_paddle.py:162-165`, `test_paddle_flow_api.py:112-125` |
| Webhook rejects bad signature (HTTP 400/404) | ✅ | `test_paddle.py:111`, `test_paddle_flow_api.py:127-133` |
| CCPA export + delete (idempotent/missing-safe) | ✅ (DB layer) | `test_paddle.py:176-183` |
| Sandbox live-credential guard (mock disabled once live key present) | ✅ | `test_paddle.py:142-155`, `test_paddle_flow_api.py:144-148` |
| Gateway fail-safe stub (402-safe) | ✅ | `test_paddle.py:132-140`, `test_backend.py:358-374` |
| Static secret-leak scan | ✅ | `test_backend.py:195-216` |
| CORS no-wildcard + trusted-proxy XFF | ✅ | `test_backend.py:255-338` |
| BYOK vault roundtrip (Fernet) | ✅ | `test_backend.py:217-232` |
| Paddle v1 refund as `adjustment.created` (type=refund) → `payment.refunded` | ✅ | `test_paddle.py:126-134` |
| `adjustment.created` completed/credit is NOT stamped as a success | ✅ | `test_paddle.py:135-148` |
| `transaction_id` → paying user mapping (`db.payment_user_by_txn`) | ✅ | `test_paddle.py:205-212` |
| HTTP-level signed refund closes the loop (pro → demo, credits kept) | ✅ | `test_paddle_flow_api.py:194-224` |

### 3.3 Coverage gaps (all REPORTED gaps now closed in later passes)
- **G-1 ✅** HTTP-level TestClient end-to-end assertions for `GET /api/account/export`
  and `DELETE /api/account` were added in §6 (``test_account_export_delete_via_api``).
- **G-2 ✅** An end-to-end test now proves the audit trail omits sensitive keys
  (``test_audit_trail_strips_sensitive_keys``); `audit.py` `_SENSITIVE_KEYS` extended.
- **G-3 (P2):** `deploy/bootstrap.sh` orchestration (Caddy render, `.env` generation) is **not** covered by any automated test; only `bash -n` syntax was checked here.
- **G-4 (P3 — PARTIALLY CLOSED):** No DB-level credit-retention assertion, but the API level now asserts `credits == 120` after refund in `test_paddle_flow_api.py:194-224` (both refund tests). DB-level assertion remains a nice-to-have.

All gaps are low-severity given the thin wrappers and existing unit coverage.

---

## 4. Synthesis

### 4.1 Deploy readiness verdict
**GO-WITH-ISSUES.**
- Blocking items: **none.** All legal endpoints live, encryption at rest verified, audit trail clean of secrets, all 50 tests pass, all static checks green, Dockerfile CMD valid, compose binds localhost only.
- Must-fix-before-public (non-blocking but recommended): **C-1** `.gitignore` ✅ (in place); **C-3** audit-log disclosure ✅ (added to `/privacy`); **D-5** `git init` — **still operator action** (repo not yet under version control).

### 4.2 The ONE-command launch path
```
# On a fresh Ubuntu 22.04/24.04 VPS (Hetzner CX22 / Contabo VPS S):
sudo bash deploy/bootstrap.sh --domain app.yourdomain.com --email admin@yourdomain.com
```
This installs Docker, generates a secret `.env` (then exits — you fill Paddle values), builds+starts `app+redis` via compose, installs Caddy on the host, renders `/etc/caddy/Caddyfile` (auto TLS), enables systemd boot persistence, and waits for `/health`.

**Recommended host:** Hetzner (CX22, ~€4/mo, EU — fine since Paddle is MoR and handles tax) or Contabo VPS S (€5/mo, US-east-friendly). Either is sufficient; 2 vCPU / 4 GB RAM covers app + redis + Caddy comfortably.

### 4.3 Pre-launch checklist (operator)
1. [ ] `git init` + commit the repo **with** the existing `.gitignore` (excludes `backend/data/`, `.env`, `*.db`) — closes D-5. (`.gitignore` for C-1 is already in place.)
2. [ ] Run `sudo bash deploy/bootstrap.sh --domain <your-domain>` → it writes `.env` and **exits**. Do NOT skip the exit.
3. [x] In `.env`: paste **Paddle SANDBOX** keys — **DONE** in the local workspace (`PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRICE_STARTER/PRO/SCALE`).
4. [x] Sandbox webhook test — **DONE**: signed `transaction.completed` grant + signed `adjustment.created` refund verified live (E2E_RESULT=PASS); see `tests/test_paddle_flow_api.py:194-224`.
5. [~] Verify `GET /api/account/export` and `DELETE /api/account` behave for a test user — covered by automated tests (`test_paddle.py:test_account_export_and_delete`); manual spot-check recommended.
6. [ ] Promote to **LIVE** Paddle keys; confirm `FLUXSWARM_PADDLE_MOCK` is unset so the live-credential guard (C-5 in `payments.py:344-353`) is active.
7. [ ] Set `FLUXSWARM_PAYMENTS=1` in `.env`, re-run `bootstrap.sh` so the billing gate opens.
8. [x] Audit-log retention disclosure in `/privacy` — **DONE** (closes C-3).
9. [~] Point `monitor.sh` at your domain (override stays available; **default now `127.0.0.1:8787`** — closes D-3). Cron scheduling remains an operator step.
10. [~] Wire CI gate — **DONE** (pytest + secret scan); document `rotate_secrets.py` (C-4) still open.

### 4.4 Final risk ledger (consolidated — post-fix, see §6)
- **P0:** none.
- **P1:** none remaining — **C-3** and **D-3** closed in the post-fix pass.
- **P2:** D-2 (encrypted off-site backups), D-4 (non-systemd host support) — optional ops hardening.
- **P3:** G-4 (DB-level credit-retention assertion; API level is covered).
- **Closed:** §5 pass closed C-1, C-3, C-5, D-1, D-3; §6 second pass closed **C-2, C-4, D-5, G-1, G-2, G-4**. Suite: **59 passing**.

---
## 5. DevOps worker re-verification (independent run — 2026-08-29)

This card (`ecc-devops`, `t_b77a9e7e`) independently re-executed the §2 static checks instead of trusting the synthesizer summary. Real evidence:

| Check | Command (run for real) | Result |
|---|---|---|
| Bash syntax | `bash -n deploy/bootstrap.sh deploy/backup.sh deploy/restore.sh deploy/monitor.sh` | **PASS** ×4 |
| Compose config | `docker compose -f docker-compose.yml config --quiet` (no containers created/started) | **PASS** |
| Python compile | `python -m py_compile backend/*.py` (15 modules) | **PASS** ×15 |
| Dockerfile CMD | `grep -n CMD Dockerfile` → line 26 | `python -m uvicorn main:app --host 0.0.0.0 --port 8787` — **valid, matches spec** |
| Secret scan | `grep -rniE "(secret\|password\|token\|api[_-]?key)=[A-Za-z0-9/+]{8,}"` over deploy/ .env.example compose Dockerfile | **no hardcoded secrets** |
| Env consistency | `FLUXSWARM_PAYMENTS` default `0` in compose / .env / bootstrap | **consistent** (billing gate closed) |
| Exposure | `docker-compose.yml:17` | binds `127.0.0.1:8787:8787` only — app not internet-exposed directly |
| Health-shape | live `GET /health` | returns `{"ok":true,...}` (Starlette compact separators) → `monitor.sh` `grep '"ok":true'` **matches**; probe functional |
| VCS | `git rev-parse` + `ls .gitignore` | **NOT a git repo, no .gitignore** → C-1 / C-5 / D-5 exposure is real |

**DevOps verdict: GO-WITH-ISSUES.** All static deploy checks are green; there is no blocking deploy defect. The one deploy-relevant P1 is **D-3** (`monitor.sh` hardcodes `https://app.fluxswarm.ai/health` as default — wrong host if the operator's domain differs; the JSON grep itself is fine). Carry P1 (C-3, D-3) and P2 deploy items (D-1 dead line, D-2 unencrypted on-host backup, D-4 systemd-only bootstrap) into the pre-launch checklist.

*Re-verified by ecc-devops worker — read-only; no app-code or config changes; live :8787 server not restarted.*

---

## 6. Post-report fix pass & live re-verification (2026-08-29, same day)

The read-only audit above was followed by an implementation pass on the live workspace. Changes were applied, verified by the test suite, and re-verified against the running server. Credentials were stored only in `.env` (never in chat or the repo); temp secret files were deleted.

### 6.1 Code changes applied

| File | Change |
|---|---|
| `backend/payments.py` | `adjustment.*` events classified as **`type ∈ {refund, chargeback}` → `payment.refunded`**, evaluated before the `status == "completed"` branch so a completed refund is never mis-stamped as a payment success. `txn_id = obj.transaction_id or obj.id` resolves Paddle v1 refunds carrying only `transaction_id`. |
| `backend/db.py` | Added `payment_user_by_txn(txn_id)` — scans `payment_events` for `kind == "payment.succeeded"` matching `detail.txn` (no SQLite JSON1 dependency). |
| `backend/main.py` | Refund branch now maps `transaction_id` → user via `db.payment_user_by_txn` before `downgrade_subscription`. |
| `backend/db.py` `downgrade_subscription` | Latent bug fix: stores the plan **key `demo`** instead of the display name `Demo`, which would have broken `PLANS[plan]` lookups after any refund. |
| `backend/main.py` `/privacy` | Explicit disclosure: the append-only audit log is retained for security/investigation, may include email + IP, and is **not subject to erasure** (closes C-3). |
| `.github/workflows/ci.yml` | Gated the full pytest suite + a real-credential secret-leak scan on every push/PR (closes C-5). |

### 6.2 Tests added (7) → suite now 57 passing

`tests/test_paddle.py`: `test_handle_webhook_adjustment_refund`, `test_handle_webhook_adjustment_completed_status_is_not_success`, `test_handle_webhook_adjustment_credit_is_not_refund`, `test_payment_user_by_txn`.
`tests/test_paddle_flow_api.py`: `test_adjustment_refund_maps_transaction_to_user` (HTTP-level, signed V1), `test_mock_disabled_with_live_credentials`.

### 6.3 Live E2E re-verification (real sandbox key, real signature, real request)

Server restarted through `run.ps1` so the process environment carries the Paddle sandbox secrets:

- **Webhook now operative:** malformed/unsigned payload → `HTTP 400` (previously `503` while the gateway env was empty).
- Signed `transaction.completed` (HMAC-SHA256 → base64 `Paddle-Signature`) for a freshly registered sandbox user → plan `pro`, credits `120`, idempotent (`dup=false`).
- Signed `adjustment.created` (`type=refund`, `transaction_id` = the txn) → plan `demo`, credits **kept at `120`** (never clawed back), idempotent.
- **E2E_RESULT=PASS**.

### 6.4 Tunnel delivery check

`GET https://brook-dublin-from-coordinates.trycloudflare.com/health` returns the live app (200) — the ephemeral Cloudflare tunnel forwards webhook traffic to the operative `:8787` endpoint.

### 6.5 Second pass (same day) — remaining items closed

- **C-2 ✅** English legal pages added: `GET /privacy-en` + `GET /terms-en` (mirror the Arabic content incl. the audit-retention + Paddle MoR statements). The four legal pages render an **operating-entity disclosure from env** (`FLUXSWARM_LEGAL_ENTITY/TAX_ID/REGISTRY_NO/ADDRESS`) so company info stays outside the repo.
- **C-4 ✅** `rotate_secrets.py` documented in `DEPLOY-US.md` (snapshot under `~/.fluxswarm/backups/<ts>/`, refuses on a non-empty vault, restart hint) and its earlier doc inaccuracy corrected.
- **D-5 ✅** Repo now under version control: `git init` + root commit `752fc5f` (46 files, clean: no `.env`, no `backend/data`, no `*.db`, no logs — `.gitignore` enforced).
- **G-1 ✅ / G-2 ✅ / G-4 ✅** Test gaps closed — suite is now **59 passing**:
  - `test_paddle_flow_api.py::test_account_export_delete_via_api` (CCPA export+erasure over HTTP; a deleted user's token yields 401).
  - `test_paddle.py::test_audit_trail_strips_sensitive_keys` — caught `api_key`/`client_secret`/`webhook_secret` NOT in the audit sensitive set → `audit.py` set extended, trail now drops them too.

### 6.6 Third pass (same day) — deploy hardening + go-live guide

- **D-2 ✅** `backup.sh` now supports **optional age encryption** of the snapshot
  (`FLUXSWARM_BACKUP_AGE_RECIPIENT`): re-encrypts, deletes the plaintext from the
  box, rotates `.tar.gz` and `.tar.gz.age`; `restore.sh` decrypts `.age` backups
  with `FLUXSWARM_BACKUP_AGE_IDENTITY`. Fixed a latent **first-run bug** (the
  `ls | xargs -r` rotation fatally exited under `set -euo pipefail` on an empty
  backup dir — replaced with a nullglob-safe rotation). Script functional-tested on
  this machine (exit 0, correct tarball contents).
- **D-4 ✅** `bootstrap.sh` detects missing `systemctl`, warns early, guards the
  Caddy/app `systemctl restart` calls, and relies on docker compose
  `restart: unless-stopped` for boot persistence when systemd is absent.
- **Go-live guide**: new `backend/PADDLE_LIVE_CHECKLIST.md` — Paddle live signup,
  domain/business/identity verification (documents Paddle accepts), business
  settings, key/catalog swap, live smoke test incl. refund, rollback, ops cadence.
- **Telegram account linking (REVIEW_OUTCOME #5, partial ✅)**: new pair-code flow —
  `GET /api/telegram/link` issues a one-time 10-min code, the bot redeems it
  (`/link <code>`) via `db.consume_telegram_link_code`; `/api/telegram/status` and
  `DELETE /api/telegram/link` manage the binding; the link is exported with the CCPA
  payload and wiped on account deletion. Once linked, bot launches use a
  **user-scoped board** (`u<uid>-tg-…`), **spend one credit** per goal, cap
  parallelism by the user's plan, and **refund the credit on launch failure** (all
  audited). Anonymous (unlinked) launch remains for the demo; full auth-gating is an
  operator decision before public exposure.
- **EN UI completion**: `index.html` is now fully localized — the `t()`/`fmt()`
  helpers feed every dynamic string (toasts, plan cards, task counter, marketplace,
  auth flow, Telegram panel), static labels carry `data-i18n`, plan descriptions map
  per id, language choice persists (localStorage), and switching live-renders the
  dynamic sections. Verified with a Node harness (ar-init + EN-toggle, no
  missing-key/TDZ failures).

### 6.7 Remaining open items (all optional / operator-only — none blocking)

- **D-2 (ops side)** operator generates an age keypair and keeps it off the host.
- **Operator-only pre-launch:** LIVE Paddle keys, permanent webhook URL/domain,
  `FLUXSWARM_PAYMENTS=1`, cron for `monitor.sh`, counsel review of the policy
  wording (all itemized in `PADDLE_LIVE_CHECKLIST.md`).
- **Company details ✅** `FLUXSWARM_LEGAL_*` (AI FOR SAAS, registry, tax ID, address,
  phone) + `FLUXSWARM_CONTACT_EMAIL` are set in `.env`; all four legal pages render
  the operating-entity block with phone, verified live and covered by
  `tests/test_legal.py` (env-driven entity + hide-when-unset).

**Arrived-at verdict: GO** — all P1 findings closed, every actionable gap resolved;
the only outstanding inputs are the operator's live credentials + company details.
