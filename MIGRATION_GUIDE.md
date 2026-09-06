# FluxSwarm — Migration & Launch Guide

Step-by-step for moving an existing FluxSwarm install to the production
architecture (PostgreSQL, KMS-encrypted BYOK, no free tier, Paddle webhook
integrity, CCPA/CPRA ADMT) plus the launch-readiness checklist.

> Companion docs: `ARCHITECTURE_v2.md` (design), `backend/PRODUCTION_RUNBOOK.md`,
> `backend/DEPLOY-US.md`, `backend/PADDLE_LIVE_CHECKLIST.md`.

---

## 1. The one big migration: SQLite → PostgreSQL

`db_postgres.py` is a **drop-in** for `db.py`: every function in `db.py`
(currently used by `main.py`) has a same-named, same-shape counterpart. The
persistence layer is selected by configuration rather than code.

### 1.1 Provision PostgreSQL (15)

```bash
docker run -d --name fluxswarm-pg -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=fluxswarm \
  -p 5432:5432 postgres:15
```

### 1.2 Point the app at it

```ini
# .env
FLUXSWARM_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/fluxswarm
```

On boot `db_postgres.init_db()` runs `alembic upgrade head` automatically
(migrations `001` → `002` → `003`). You do **not** run alembic by hand.

### 1.3 Migrate an existing SQLite store

```bash
python scripts/migrate_sqlite_to_postgres.py   # reads FLUXSWARM_DATABASE_URL;
                                               # optional arg: sqlite path
```

- Idempotent: safe to re-run; `alembic_version` ends at `["003"]` exactly once
  (asserted by `test_db_postgres.py::test_migrations_are_idempotent`).
- Migrates users, projects, referrals, templates, purchases, payment events,
  telegram links/codes, password resets, demo usage and provider agreements.
- **Secrets are NOT copied by the script.** BYOK ciphertext (`byok.json`) is
  sealed with its own KEK, not the DB, so re-keying/re-wrapping happens through
  the vault (see §3).

### 1.4 Safe switch-over

1. Backup SQLite: `python backup.py` (or copy `backend/data/users.db`).
2. Run the migration script; confirm the row counts.
3. Set `FLUXSWARM_DATABASE_URL` in `.env`; restart via `run.ps1`/`run.sh`.
4. Verify `/health` shows the DB reachable and run
   `python backend/tests` (the PG parity suite against the same URL).

Rollback: unset `FLUXSWARM_DATABASE_URL` and the app returns to SQLite (the
old store is untouched by the migration).

---

## 2. Key migration: BYOK plaintext/Fernet → KMS envelope

`vault.py` no longer Fernet-encrypts keys directly. Every key is an
**envelope**: per-blob AES-256-GCM DEK, wrapped by the KEK from the configured
KMS. Existing Fernet-encrypted blobs still decrypt (legacy fallback), so you
can switch KEKs without deleting user keys.

| Backend | Env |
|---|---|
| `file` (default; single-node ok) | `FLUXSWARM_KMS_BACKEND=file`, `FLUXSWARM_KMS_FILE_KEY=<strong passphrase>` |
| AWS KMS | `FLUXSWARM_KMS_BACKEND=aws_kms`, `FLUXSWARM_AWS_KMS_KEY_ID`, AWS credentials (`boto3`) |
| Azure Key Vault | `FLUXSWARM_KMS_BACKEND=azure_keyvault`, `FLUXSWARM_AZURE_KEYVAULT_URL`, `FLUXSWARM_AZURE_KEYVAULT_KEY`, `DefaultAzureCredential` |
| HashiCorp Vault | `FLUXSWARM_KMS_BACKEND=hashicorp_vault`, `FLUXSWARM_VAULT_ADDR`, `FLUXSWARM_VAULT_TOKEN`, `FLUXSWARM_VAULT_TRANSIT_KEY` (Transit engine) |

- **Rotation**: changing the KEK affects only newly written envelopes; legacy
  blobs keep decrypting until the user re-saves their key.
- **Production rule**: never let `file` auto-generate its own ephemeral key —
  `FileKms` generates demo keys only in `FLUXSWARM_DEMO_MODE` and refuses
  otherwise; always set `FLUXSWARM_KMS_FILE_KEY` (or a real KMS).

---

## 3. No-free-tier runtime config (Phase 3)

`opencode-free` / `big-pickle` are gone. The launch runtime resolves to:

1. **BYOK** (user keys, always pinned `provider_keys=`), or
2. **Operator default**: `FLUXSWARM_DEFAULT_PROVIDER` + the matching
   `FLUXSWARM_MODEL_<PROVIDER>` (e.g. `FLUXSWARM_MODEL_ANTHROPIC=claude-...`), or
3. **Fail fast**: `ProviderConfigError` — an unconfigured runtime refuses to
   launch. No guessing, no silent downgrades.

**BYOK usage gate**: a provider key only counts once the user has accepted that
provider's agreement:

```http
POST /api/agreements/anthropic          → {"ok": true}   (first accept)
GET  /api/agreements                    → required_versions + accepted
```

`POST /api/keys` rejects anything outside
`anthropic|openai|gemini|kimi` (including the legacy `opencode-free`).

---

## 4. Paddle webhook integrity (Phase 4) — operator checklist

- Webhook URL: `https://<domain>/api/payments/webhook`.
- The handler verifies the **raw bytes** of the body:
  - V2: `Paddle-Signature: ts=<unix>;h1=<hex>` with `h1 = HMAC-SHA256(secret,
    f"paddle-{ts};{body}")` and `|now-ts| <= 300` s.
  - V1 (classic): `base64(HMAC-SHA256(body))`.
- `PADDLE_WEBHOOK_SECRET` must match the secret shown when the webhook was
  created. The classic mistake is a **stale secret saved in `.env`** — updating
  the secret is the usual root cause of `bad_signature` (documented in
  `ARCHITECTURE_v2.md` §10 / `PRODUCTION_RUNBOOK.md` §17).
- Replays are safe: a duplicated `event_id` returns `{"accepted":true,
  "deduplicated":true}` and never double-grants.

---

## 5. CCPA/CPRA ADMT (Phase 5) — endpoints & duties

| Right | Endpoint | Notes |
|---|---|---|
| Access | `GET /api/account/export` | Full account payload; audited |
| Correction | `PATCH /api/account` `{"name": ...}` | Audited `account.rectify`; frontend = Settings → Account → Save |
| Deletion | `DELETE /api/account` | Erases rows + BYOK + own Hermes boards; idempotent |
| Transparency | `/privacy`, `/privacy-en`, `/terms`, `/terms-en`, `/cookies`, `/cookies-en` | Data categories, no-sale, audit-retention disclosure |

Operators must keep the audit log append-only and disclose that retention in
the privacy policy (already in the shipped pages).

---

## 6. Windows↔Linux parity (Phase 6)

- **Server lock**: POSIX now uses `fcntl.flock` (auto-released on crash);
  Windows keeps the `O_EXCL` fallback. `FLUXSWARM_LOCK_FILE` overrides the
  path (used by tests). `FLUXSWARM_ALLOW_MULTI=1` bypasses it for dev only.
- **Process cleanup**: `kill_process_tree` runs on both platforms (WIN32
  snapshot vs POSIX ps-tree + SIGTERM→SIGKILL); POSIX tests are skipped on
  Windows and execute on Linux CI.
- **Dev container**: open the repo in VS Code with the Remote-Containers
  extension (or push to Codespaces) — `.devcontainer/` boots Python 3.11 +
  Postgres 15 + Redis and runs the full suite on the first setup
  (`postCreateCommand`). This gives Windows developers the exact production
  runtime without a VM.
- **CI**: `test-backend` runs on ubuntu-latest with an ephemeral Postgres 15
  service → the PG/Alembic suite is verified on every push/PR, plus the
  secret-leak scan and an app-image Docker build.

---

## 7. Env bootstrap (production `.env`)

```ini
# --- mandatory in production (envguard refuses boot without them) ---
FLUXSWARM_JWT_SECRET=<at least 32 random bytes>
FLUXSWARM_FERNET_KEY=<at least 32 random bytes>
FLUXSWARM_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/fluxswarm

# --- KMS (Phase 3) ---
FLUXSWARM_KMS_BACKEND=file
FLUXSWARM_KMS_FILE_KEY=<strong random passphrase>

# --- runtime (Phase 3, no free tier) ---
FLUXSWARM_DEFAULT_PROVIDER=anthropic
FLUXSWARM_MODEL_ANTHROPIC=claude-<latest>

# --- Paddle (Phase 4) ---
FLUXSWARM_PAYMENTS=1
FLUXSWARM_PAYMENT_PROVIDER=paddle
PADDLE_API_KEY=pdl_live_...
PADDLE_WEBHOOK_SECRET=...
PADDLE_CLIENT_TOKEN=...
PADDLE_PRICE_STARTER=pri_...
PADDLE_PRICE_PRO=pri_...
PADDLE_PRICE_SCALE=pri_...

# --- platform ---
FLUXSWARM_PUBLIC_BASE_URL=https://yourdomain.com
FLUXSWARM_CORS_ORIGINS=https://yourdomain.com
FLUXSWARM_TRUSTED_PROXIES=...
REDIS_URL=redis://redis:6379/0

# --- optional (Phase 2) ---
FLUXSWARM_DOCKER_DISPATCH=1

# NEVER set these in production:
# FLUXSWARM_DEMO_MODE, FLUXSWARM_ALLOW_MULTI
```

> Never commit `.env`. `backend/data/`, `.db` files, and `byok.json` are
> gitignored. `deploy/backup.sh` snapshots the DB + vault; `rotate_secrets.py`
> rotates JWT + Fernet safely.

---

## 8. Migration checklist (existing installs)

- [ ] `git pull` + `pip install -r backend/requirements.txt` (asyncpg, alembic,
  sqlalchemy now required).
- [ ] Set new env vars (§7) — **do not set `FLUXSWARM_DEMO_MODE`**.
- [ ] Provision PG 15 and verify `FLUXSWARM_DATABASE_URL` is reachable.
- [ ] Run `scripts/migrate_sqlite_to_postgres.py`; sanity-check row counts.
- [ ] Restart; check `/health` (`db:` field, `alembic` at `003`).
- [ ] Configure KMS backend + `FLUXSWARM_DEFAULT_PROVIDER`/`FLUXSWARM_MODEL_*`.
- [ ] Re-save a test user's BYOK key through the UI → confirm it now appears in
      `byok.json` as an envelope (`v1`) blob.
- [ ] Confirm the app never attempts `opencode-free` (try an unauth launch →
      clear `ProviderConfigError` message; no fallback).
- [ ] Paddle: update `.env` secrets, register the webhook URL, replay an old
      `transaction.completed` → `deduplicated:true`.
- [ ] CCPA: exercise `GET /api/account/export`, `PATCH /api/account`,
      `DELETE /api/account` on a scratch account.
- [ ] Optional: enable `FLUXSWARM_DOCKER_DISPATCH=1` and confirm a dispatch
      runs inside `fluxswarm/hermes-runner:latest`.

---

## 9. Global launch checklist (~350 tests)

| Area | Gate | Where |
|---|---|---|
| DB parity SQLite↔PG | `test_db_postgres.py` (incl. Alembic idempotent `003`) | CI (Postgres service) + local |
| Migration script | `test_migrations_are_idempotent` | CI |
| Docker dispatch | `test_hermes_docker.py` (all CLI calls monkeypatched — no docker daemon needed) | CI |
| Runtime pinning / no-free | `test_provider_pinning.py`, `test_production_prep.py`, `test_preflight.py` | CI |
| Provider health | `test_provider_health.py`, `test_provider_resilience.py` | CI |
| KMS envelope | `test_kms_client.py` (roundtrip, tamper, unconfigured→raise) | CI |
| Vault fallback | `test_kms_client.py` (legacy Fernet fallback) | CI |
| Agreements gate | `test_agreements.py` | CI |
| BYOK privacy | `test_tenant_isolation.py` | CI |
| Paddle signatures V1+V2 + replay | `test_paddle.py` | CI |
| Paddle HTTP flow + idempotent refund | `test_paddle_flow_api.py` | CI |
| CCPA/CPRA ADMT | `test_privacy_admt.py` | CI |
| Account deletion cascade | `test_account_deletion_extended.py`, `test_telegram.py` | CI |
| Process-tree cleanup (WIN32+POSIX) | `test_process_cleanup.py` (POSIX parts run on Linux CI) | CI |
| Rate limits / audit / ratelimit-IP | `test_ratelimit*`, `test_audit.py`, `test_fix_phase15.py` | CI |
| UX / GATE-3 (register→delete journey) | `test_gate3_ux.py` | CI |
| Verifier skill resolution | `test_verifier_skill.py` (skips w/o Hermes venv) | CI |
| Legal pages AR+EN | `test_legal.py` | CI |
| Secret-leak static scan | ci.yml step (fixtures excluded by policy) | CI |
| `import main` + py_compile | ci.yml | CI |
| Docker build of app image | ci.yml `docker` job | CI |

Core commands:

```bash
# full suite (local; PG URL from env when set)
cd backend && python -m pytest -q

# just the PG layer
python -m pytest tests/test_db_postgres.py -q
```