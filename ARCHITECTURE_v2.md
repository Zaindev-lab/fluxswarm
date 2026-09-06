# FluxSwarm — Architecture v2 (Production Overhaul)

> Current-as-of: 2026-09 · supersedes `ARCHITECTURE.md`
> Status: **production-ready code**, all 6 overhaul phases landed, CI green
> (346 tests passing, 7 POSIX-only tests that run on Linux CI and skip on Windows).

## 0. What changed since v1 (6-phase overhaul)

| # | Phase | What landed |
|---|---|---|
| 1 | SQLite → PostgreSQL | `db_postgres.py` is a sync drop-in for `db.py` (same function names/shapes); Alembic-managed schema (`001`, `002`, `003`); `init_db()` runs `alembic upgrade head`; `scripts/migrate_sqlite_to_postgres.py` migrates a live SQLite store |
| 2 | Hermes isolation | Hermes agents run inside a hardened Docker runner (`hermes_docker.py`) instead of a subprocess; opt-in via `FLUXSWARM_DOCKER_DISPATCH=1`; advisory-lock dispatch; 24 dedicated tests |
| 3 | No free tier + BYOK hardening | Free providers (`opencode-free`/`big-pickle`) removed everywhere; every BYOK key is sealed with KMS envelope encryption; provider keys are usable only after the user accepts that provider's agreement |
| 4 | Paddle webhook integrity | Raw-body signature verification enforced (V1 base64 HMAC **and** V2 `ts=;h1=` HMAC-SHA256 over `f"paddle-{ts};{body}"`) with a ±300 s replay window |
| 5 | CCPA/CPRA ADMT | Access/Modification/Deletion (`export`/`PATCH`/`DELETE /api/account`); **pre-use notice + acknowledgment, opt-out (dispatch 403) / opt-in, human-review queue + admin resolution, per-project logic disclosure** (`admt_disclosures` via migration 004), `docs/CCPA_RISK_ASSESSMENT.md` |
| 6 | Windows↔Linux parity + CI/CD | POSIX `fcntl.flock` server lock with auto-release; `.devcontainer/` (Linux, Postgres 15 + Redis); CI runs the PG suite against an ephemeral Postgres service |

## 1. Product

FluxSwarm turns a natural-language goal into a **swarm of 6 AI agents**
(Planner → Architect → DevOps → TDD → Reviewer → Builder) running on a live
kanban board; generated files land in a browsable project workspace. One
**credit** per launch (Demo plan starts with 3 non-expiring credits; paid
Starter/Pro/Scale packs via Paddle as Merchant of Record).

- **BYOK**: users may bring Anthropic / OpenAI / Gemini / Kimi keys. A key is
  never shown back, is sealed with KMS envelope encryption, and only becomes
  usable after the user accepts that provider's agreement
  (`POST /api/agreements/{provider}`).
- **No free provider**: the runtime is either an operator-configured default
  (`FLUXSWARM_DEFAULT_PROVIDER` + `FLUXSWARM_MODEL_*`) or a user BYOK key.
  An unconfigured runtime **fails fast** (`ProviderConfigError`) — never
  silently downgrades to a free model.

## 2. Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 (dev venv pinned to `Python311`) |
| Web | FastAPI + Uvicorn on `127.0.0.1:8787` (single process; `run.ps1`/`run.sh` supervise + restart on failed `/health`) |
| Frontend | `templates/index.html` (Jinja2, RTL Arabic + English i18n) + inline legal pages |
| Database | **PostgreSQL 15** (primary, `db_postgres.py`) — Alembic migrations `001..003`; SQLite (`db.py`) kept as a drop-in fallback/dev store |
| Storage | `data/audit.jsonl` (append-only, sensitive-value purging), `data/byok.json` (KMS ciphertext), `.server.lock` (flock/POSIX) |
| Encryption | Argon2id passwords; KMS envelope encryption for BYOK (fresh per-blob AES-256-GCM DEK wrapped by the KEK: file / AWS KMS / Azure Key Vault / HashiCorp Vault Transit) |
| Agent runtime | Hermes CLI (vendored; `FLUXSWARM_HERMES_BIN`) — subprocess (`hermes_client.py`) or hardened Docker runner (`hermes_docker.py`, image `fluxswarm/hermes-runner:latest`) |
| Payments | Paddle Merchant-of-Record, server-side Checkout API + signature-verified webhooks |
| Billing env | Redis (multi-worker rate limiting) or in-process fallback |
| CI/CD | GitHub Actions: ubuntu-latest + Postgres 15 service + pytest + secret-leak scan + Docker build |
| Dev parity | `.devcontainer/` (Linux image, Postgres + Redis compose) for Windows developers |

## 3. High-level flow

```
Browser ──► Caddy/nginx (TLS) ──► FastAPI:8787
                ├─ auth.py        JWT HS256 + session kill (logged_out_at)
                ├─ ratelimit      login/register/IP/purchase limits (Redis|memory)
                ├─ db_postgres.py PostgreSQL + Alembic (or db.py SQLite)
                ├─ vault.py       BYOK -> KMS envelope (FileKms/AWS/Azure/Vault)
                ├─ hermes_client  launch_swarm/dispatch -> Hermes (subprocess or Docker)
                ├─ hermes_docker  sandboxed runner (Phase 2, opt-in)
                ├─ provider.py    provider health/preflight (no credit spent)
                ├─ payments.py    PaddleGateway create_checkout + verify_paddle_signature
                └─ telegram_bot.py (separate process; links chats to accounts)

Paddle (MoR)
   └─ POST /api/payments/webhook  (raw body; V1/V2 HMAC; ±300s window; idempotent)
        └─ transaction.completed -> upgrade_plan + credits
        └─ (adjustment) refund / chargeback -> downgrade_subscription
```

## 4. Repository layout

```
fluxswarm/
├── ARCHITECTURE_v2.md  MIGRATION_GUIDE.md     # this doc + migration runbook
├── Dockerfile  docker-compose.yml  .env.example  Caddyfile  nginx.conf
├── .github/workflows/ci.yml                    # pytest+PG service+secret scan+docker
├── .devcontainer/                             # Linux dev container (compose+Dockerfile+setup.sh)
├── deploy/
│   ├── bootstrap.sh  Caddyfile.us  monitor.sh  backup.sh  restore.sh
│   └── docker/{hermes-runner.Dockerfile, hermes-home/profiles/…}
└── backend/
    ├── main.py            all routes, middleware, marketing/legal pages
    ├── db.py              SQLite drop-in layer
    ├── db_postgres.py     PostgreSQL/Alembic drop-in (same API surface)
    ├── alembic/           001…003 migrations
    ├── models/__init__.py SQLAlchemy ORM models
    ├── hermes_client.py   Hermes bridge: boards, dispatch, outcome reconcile,
    │                      kill_process_tree (WIN32 + POSIX), runtime pinning
    ├── hermes_docker.py   Docker dispatch transport (lock, run, audit, cp)
    ├── provider.py        BYOK provider health/preflight
    ├── kms_client.py      envelope KMS backends (file/aws_kms/azure_keyvault/hashicorp_vault)
    ├── vault.py           BYOK store -> KMS envelopes (+ legacy Fernet fallback)
    ├── payments.py        PaddleGateway + verify_paddle_signature (V1/V2)
    ├── ratelimit.py       Redis|memory interchangeable backends (+ limiter.reset())
    ├── audit.py           append-only JSONL, purges/truncates sensitive fields
    ├── security.py        static secret-constant scanner
    ├── envguard.py        refuse boot without production secrets
    ├── serverlock.py      single instance: fcntl.flock (POSIX) / O_EXCL (Windows)
    ├── scripts/migrate_sqlite_to_postgres.py
    ├── data/              runtime data (gitignored)
    ├── templates/, static/
    └── tests/             ~350 tests (see §15)
```

## 5. Data layer

### 5.1 Storage backends
- **Primary**: `db_postgres.py` — asyncpg pool in a background thread; sync
  functions mirror `db.py` 1:1. `init_db()` connects and runs
  `alembic upgrade head`. `FLUXSWARM_DATABASE_URL` is required when selected.
- **Fallback/dev**: `db.py` — SQLite with `PRAGMA foreign_keys=ON` and
  `BEGIN IMMEDIATE` around balance-changing operations.

### 5.2 Tables
`users` (Argon2id `pw_hash`, plan, credits, ref_code, referred_by,
logged_out_at) · `projects` (board_slug, launch_status/outcome/reason/refunded) ·
`referrals` · `squad_templates` + `template_purchases` · `payment_events`
(event_id UNIQUE → idempotency) · `telegram_links`/`telegram_codes` ·
`password_resets` (SHA-256 only, single-use, 900 s TTL) · `demo_usage`
(PK who,day) · **`provider_agreements`** (PK user_id,provider; agreed_at,
version → Phase 3 gate).

### 5.3 Race-safety
`deduct_credit`, `refund_launch_credit`, `reward_referrer_once`,
`buy_template/refund_template_purchase` are atomic against concurrency
(SQLite `BEGIN IMMEDIATE` / PG locking; tests cover credit-concurrency + the
referral single-reward race).

## 6. Auth & sessions

- JWT HS256, 7-day lifetime; `FLUXSWARM_JWT_SECRET` mandatory in production
  (`envguard` refuses boot otherwise).
- Collective invalidation: `logged_out_at` — any token with `iat` older than
  the stored value is refused (logout, password change, reset).
- Reset tokens: random 32 bytes, SHA-256 at rest, 900 s TTL, single-use.
- Rate limiting: 5 login failures / 900 s per ip+email; 20 auth requests /
  60 s per IP; 10 registrations / hour per IP; 5 template purchases / hour.
  Counters are reset per test (conftest autouse) so the suite never trips 429s.

## 7. Security posture

1. **envguard** — production boot without `FLUXSWARM_FERNET_KEY`,
   `FLUXSWARM_JWT_SECRET`, `FLUXSWARM_DATABASE_URL` (in non-demo mode) fails.
2. **CSP on every response** (`script-src 'self' 'unsafe-inline' https://cdn.paddle.com`,
   Paddle iframe origins, `frame-ancestors 'none'`, `X-Frame-Options: DENY`,
   `Cache-Control: no-store` for `/api`).
3. **CORS** — explicit `FLUXSWARM_CORS_ORIGINS` only; `*` is a boot error.
4. **X-Forwarded-For** trusted only from `FLUXSWARM_TRUSTED_PROXIES` (prevents
   rate-limiter evasion through the proxy).
5. **BYOK at rest** — KMS envelope encryption (`kms_client.py`): a fresh
   per-blob AES-256-GCM DEK wrapped by the KEK; the store alone is useless.
   Backends: file / AWS KMS / Azure Key Vault / HashiCorp Vault Transit;
   legacy Fernet blobs still decrypt (smooth migration).
6. **No free fallback** — unconfigured runtime raises `ProviderConfigError`;
   agreement-gated BYOK only after the user accepts per-provider terms.
7. **Runtime sandbox** — Hermes in Docker (read-only FS, no-new-privileges,
   capped memory, non-root) when `FLUXSWARM_DOCKER_DISPATCH=1`; subprocess
   path still cleans up its whole process tree (WIN32 snapshot / POSIX ps-tree
   with SIGTERM→SIGKILL).
8. **Single instance** — `serverlock.py`: POSIX `fcntl.flock` (auto-releases
   on crash) / Windows `O_EXCL` with stale-pid reclaim.
9. **Audit** — append-only JSONL; sensitive values purged, records capped.
10. **Webhooks** — credits granted only on a signature-verified Paddle payload
    (raw bytes), never on a client redirect.

## 8. Agent runtime (`hermes_client.py`)

- Board-per-project isolation; slug regex-safety; outcomes reconciled into
  `projects.launch_status/outcome/reason`.
- **Runtime resolution** (`_resolve_runtime`): explicit BYOK keys → pinned
  provider+model (`FLUXSWARM_MODEL_<PROVIDER>` or a built-in default);
  otherwise operator default (`FLUXSWARM_DEFAULT_PROVIDER`+model). Nothing →
  `ProviderConfigError` (fail fast). No provider is ever guessed.
- `dispatch()` loop to terminal state, `DISPATCH_TIMEOUT_S` hard ceiling,
  no-progress stall detector; stray/failed launches refund the credit once.
- **Docker transport** (`hermes_docker.py`): advisory-lock per board via the
  pg pool, sanitized board dir, `docker run` resource caps, output copied back,
  dispatch audit (`data/dispatch_audit.jsonl`).

## 9. BYOK + agreements

- `GET /api/keys` → masked per-provider state (`has_key`, `provider`,
  `masked`, `byok_active`). Only `anthropic|openai|gemini|kimi` are accepted;
  anything else (including legacy `opencode-free`) is rejected.
- `POST /api/keys` — a key must be preceded by `POST /api/agreements/{provider}`
  (first accept is recorded; version `1.0`). `_user_provider_keys` only returns
  keys whose agreement the user accepted; gates every launch.
- Keys never leave the vault as plaintext; `DELETE /api/keys` wipes them;
  `DELETE /api/account` erases vault rows + agreement rows.

## 10. Paddle payments (`payments.py`)

- `POST /api/subscribe/{plan}` opens a transaction
  (`Paddle-Version: 1`, `Bearer PADDLE_API_KEY`, `PADDLE_PRICE_<PLAN>`) and
  returns our `/checkout` page URL (SDK v2 overlay).
- Webhook `POST /api/payments/webhook`:
  - Verifies the **raw request bytes** — V2 `Paddle-Signature: ts=<unix>;h1=<hex>`
    where `h1 = HMAC-SHA256(secret, f"paddle-{ts};{body}")`, timestamp within
    `_WEBHOOK_SIG_MAX_AGE_S = 300` (replay protection); V1 `base64(HMAC(body))`
    still supported.
  - Idempotent via `payment_events.event_id UNIQUE` (`{"deduplicated":true}`).
  - `transaction.completed` → `upgrade_plan` + credits (never clawed back);
    refund/chargeback → `downgrade_subscription`; email/price fallbacks map
    dashboard/test payments.
- Env: `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_CLIENT_TOKEN`,
  `PADDLE_PRICE_{STARTER,PRO,SCALE}`, `PADDLE_API_BASE` (sandbox/live);
  `FLUXSWARM_PADDLE_MOCK=1` enables a fully-local signed sandbox path that is
  automatically disabled when LIVE credentials are present.

## 11. CCPA/CPRA (ADMT)

- **Access**: `GET /api/account/export` → users/projects/referrals/templates/
  purchases/payment_events/telegram_links/provider_agreements, audited.
- **Modification**: `PATCH /api/account` `{name}` (audited `account.rectify`);
  surfaced in the settings UI (name field + Save).
- **Deletion**: `DELETE /api/account` — erases all rows, BYOK ciphertext, and
  the user's own Hermes boards; idempotent (`ok:false` on a second delete).
- **Transparency**: AR+EN privacy/terms/refund/cookies/acceptable-use pages;
  data categories; no-sale affirmation; audit-log retention disclosed
  (append-only, excluded from erasure by policy).

### ADMT (Automated Decision-Making Technology) — full flow

- **Pre-use notice**: `GET /api/account/admt-notice?lang=en|ar` — fixed
  disclosure (`admt_types`, description, `logic_summary`, review/opt-out
  availability, `last_updated` version). Shown before the first assisted
  launch; `POST /api/account/admt-notice/acknowledge` and
  `POST /api/account/opt-in-admt` record `acknowledged_at` rows in the
  `admt_disclosures` ledger.
- **Opt-out**: `POST /api/account/opt-out-admt` (`users.admt_opt_out`,
  audited `admt.optout`, idempotent). While active: `POST
  /api/projects/{slug}/dispatch` → **403** "ADMT opt-out active. Human review
  required."; project creation degrades to *manual* mode (`manual:true`, no AI
  agents, no credit debit).
- **Opt-in**: `POST /api/account/opt-in-admt` requires re-reading the current
  notice (`acknowledge:true` + current `last_updated`); stores a fresh
  acknowledgment then clears `admt_opt_out` (audited `admt.optin`).
- **Human review**: `POST /api/projects/{id}/request-human-review` → row in
  `admt_disclosures` (`status="requested"`, `requested_at`). Operators see
  `GET /api/admin/human-review-queue` and resolve with
  `PATCH /api/admin/human-review/{id}` (`approved|rejected|needs_changes` +
  `reviewer_notes`) — admin surface is a shared-secret bearer
  (`FLUXSWARM_ADMIN_TOKEN`, deny-by-default, constant-time compare). The
  requester is notified (audit-recorded email channel; direct Telegram when a
  bot token is configured and not in demo mode).
- **Logic disclosure**: `GET /api/projects/{id}/admt-logic` (owner-only) →
  agents used, per-agent decision + rationale (deterministic from the goal),
  runtime provider/model, `generated_at`.
- **Schema**: migration `004` → `users.admt_opt_out` + `admt_disclosures`
  (user_id, project_id, disclosed/acknowledged/requested/reviewed timestamps,
  `admt_type`, `logic_summary`, `human_review_status`, `reviewer_notes`),
  FK `ON DELETE CASCADE`, indexed by user and status.
- **Risk assessment**: `docs/CCPA_RISK_ASSESSMENT.md` — ADMT inventory,
  data minimization, security measures, human-review workflow (48 h SLA),
  annual review schedule (2027-01-01), `privacy@fluxswarm.ai` contact.

## 12. Operations

- `run.ps1` (Windows) / `run.sh` (Linux): load `.env`, boot uvicorn, watch
  `/health`, auto-restart, single-instance guard.
- `/health` → {ok, version, db, hermes_bin_ok, limiter_backend, pid, uptime}.
- `backup.py` + `deploy/backup.sh`/`restore.sh`; `rotate_secrets.py`;
  `validate_paddle.py` for pre-launch credential checks.
- Production runbook: `backend/PRODUCTION_RUNBOOK.md`; US launch:
  `backend/DEPLOY-US.md`; live Paddle checklist: `backend/PADDLE_LIVE_CHECKLIST.md`.

## 13. Environment map (key vars)

| Variable | Purpose |
|---|---|
| `FLUXSWARM_DATABASE_URL` | PostgreSQL DSN (required for `db_postgres`) |
| `FLUXSWARM_JWT_SECRET`, `FLUXSWARM_FERNET_KEY` | Production boot requirements |
| `FLUXSWARM_KMS_BACKEND` | `file` (default) / `aws_kms` / `azure_keyvault` / `hashicorp_vault` |
| `FLUXSWARM_KMS_FILE_KEY`, `FLUXSWARM_AWS_KMS_KEY_ID`, `FLUXSWARM_AZURE_KEYVAULT_URL`+`_KEY`, `FLUXSWARM_VAULT_ADDR`+`_TOKEN`+`_TRANSIT_KEY` | Backend-specific KEK wiring |
| `FLUXSWARM_DEFAULT_PROVIDER` + `FLUXSWARM_MODEL_<PROVIDER>` | Operator default runtime |
| `FLUXSWARM_PAYMENTS`, `FLUXSWARM_PAYMENT_PROVIDER=paddle` | Billing gate |
| `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_CLIENT_TOKEN`, `PADDLE_PRICE_{STARTER,PRO,SCALE}`, `PADDLE_API_BASE` | Paddle sandbox/live |
| `FLUXSWARM_DOCKER_DISPATCH=1` | Route Hermes through the Docker sandbox |
| `REDIS_URL`/`FLUXSWARM_REDIS_URL` | Multi-worker rate limiting |
| `FLUXSWARM_DEMO_MODE` | Dev only; never set in production |
| `FLUXSWARM_ALLOW_MULTI` | Dev only; bypass the single-instance lock |
| `FLUXSWARM_LOCK_FILE` | Override the single-instance lock path (tests) |

## 14. Dev parity + CI/CD

- `.devcontainer/` (VS Code / Codespaces): Linux image, Postgres 15 + Redis
  services, repo mounted at `/workspace`, `postCreateCommand` installs deps and
  runs the full suite — so Windows developers exercise POSIX flock, run.sh,
  `os.killpg`-equivalent cleanup and the real PG path identically.
- `.github/workflows/ci.yml` `test-backend`: ubuntu-latest, Postgres 15 service,
  `FLUXSWARM_DATABASE_URL` pointed at it (the PG+alembic suite now runs in CI),
  `import main`, `py_compile` over the core modules, full pytest, and a
  real-credential secret-leak scan (fixtures excluded by policy — their
  synthetic lookalikes are what the detector tests assert on). A `docker` job
  builds the app image (`fluxswarm:ci`).

## 15. Tests (`backend/tests`)

- **346 passing + 7 POSIX-only** tests (skip on Windows, execute on Linux CI);
  suites include: `test_db_postgres.py` (PG parity, Alembic idempotency,
  migration script), `test_hermes_docker.py`, `test_paddle.py`,
  `test_paddle_flow_api.py`, `test_provider_pinning.py`,
  `test_provider_health.py`, `test_dispatch_completion.py`,
  `test_production_prep.py`, `test_verifier_skill.py`, `test_kms_client.py`,
  `test_agreements.py`, `test_privacy_admt.py`, `test_admt_compliance.py`,
  `test_process_cleanup.py`
  (WIN32 + POSIX trees), `test_telegram.py`, `test_gate3_ux.py`,
  `test_tenant_isolation.py`, `test_backend.py`, `test_security_static.py`
  and the rate-limit/audit/refund/referral suites.
- Conftest isolates the store, pins the file-KMS backend, and resets the
  rate-limiter per test.

## 16. Remaining rollout steps (ops)

1. Provision PG 15 (`FLUXSWARM_DATABASE_URL`), set `FLUXSWARM_JWT_SECRET`,
   `FLUXSWARM_FERNET_KEY`, and a real KMS backend (or a strong
   `FLUXSWARM_KMS_FILE_KEY`).
2. Point `FLUXSWARM_DEFAULT_PROVIDER` + `FLUXSWARM_MODEL_*` at a paid provider.
3. Wire LIVE Paddle (`PADDLE_LIVE_*`, webhook URL `https://<domain>/api/payments/webhook`),
   run `validate_paddle.py`, `PADDLE_LIVE_CHECKLIST.md`.
4. `sudo bash deploy/bootstrap.sh --domain <domain> --email <email>`; enable
   `FLUXSWARM_DOCKER_DISPATCH=1` when the runner image is deployable.
5. Watch `/health`; audit + backup per `PRODUCTION_RUNBOOK.md`.