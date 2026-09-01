# 01 — Project Discovery

Phase: 1 · Date: 2026-08-30 · Evidence mode: CODE + RUNTIME

## Stack (actual)

| Layer | Technology | Evidence |
|-------|-----------|----------|
| Backend | Python 3.11, FastAPI 0.133, uvicorn 0.41 | `backend/requirements.txt`; `main.py:39` |
| Frontend | Single-file Jinja2 `templates/index.html` (~36 KB, vanilla JS, AR/EN i18n) | `main.py:38`, `index.html` |
| DB | SQLite single file `backend/data/users.db` (57 KB), schema in `db.py:56` | `db.py:35` |
| Auth | JWT HS256, 7-day expiry; Argon2id passwords (legacy sha256 auto-upgrade) | `auth.py:14-15,40`; `db.py:138-224` |
| Vault | Fernet at-rest encryption for BYOK provider keys | `vault.py` |
| Rate limit | In-process fixed-window (Redis-capable), JSONL audit | `ratelimit.py` (`limiter_backend: memory` runtime) |
| Agent runtime | External `hermes` CLI (kanban/swarm), ECC devops squad | `hermes_client.py` |
| Billing | Paddle (Merchant of Record), signature-verified webhooks; gate `FLUXSWARM_PAYMENTS` | `payments.py`, `main.py:542-584,587-611` |
| Real-time | WebSocket `/ws/{slug}` polling (4 s) | `main.py:1249` |
| Marketplace | squad templates (credit store) | `main.py:1072-1227; db.py:570-689` |
| Telegram | python-telegram-bot 22.8, account linking via 6-hex pairing code | `telegram_bot.py` |

## Architecture (actual)

- **Single process** FastAPI serving HTML + JSON API + WS. One shared SQLite file. Hermes CLI driven via subprocess per launch/dispatch (`hermes_client._run`).
- **Isolation model**: each project = Hermes kanban board named `u{uid}-{epoch}-{rand16}`; API ownership guard = slug prefix check `slug.startswith(f"u{uid}-")` (`main.py:462,472,1236,1274`). No per-user DB rows for board data; boards live on the host Hermes install.
- Billing gate is closed in dev: `FLUXSWARM_PAYMENTS=0` (`.env.example:19`), paid subscribe returns 402 (`main.py:555-561`). StubGateway default (`payments.py:65`).
- Deploy: Docker (python:3.11-slim) + Caddy TLS (`deploy/Caddyfile.us`), systemd-less bootstrap (`deploy/bootstrap.sh`), age-encrypted backups (`deploy/backup.sh/restore.sh`), monitor probe (`deploy/monitor.sh`). request_body max 1MB in Caddy.

## Services / external providers

- Hermes (local install `C:/Users/DELL/AppData/Local/hermes`) — squad runtime + kanban store. Runtime `/health` shows `hermes_bin_ok: true`.
- Paddle (configured in dev as stub → **not operative**: sandbox keys present in `.env` but `FLUXSWARM_PAYMENTS` unset).
- Telegram bot (`aiforsaashermes_bot`) — token in Hermes `.env`.
- Optional Redis (declared in compose; **absent locally** — runtime `limiter_backend: memory`).

## Key dependencies

`fastapi, uvicorn[standard], websockets, pydantic, Jinja2, httpx, PyJWT, argon2-cffi, redis, python-telegram-bot, aiohttp, cryptography` — 12 direct deps, small surface.

## CI/CD

`.github/workflows/ci.yml`: pytest + import + py_compile + static secret-leak grep + `docker build`. **Observation: trigger is `push: branches: [main]` but the repo's only branch is `master` → the CI job would never fire.** (Finding FLX-CI-1.)

## Git

Local repo only (`git remote -v` empty). 6 commits on `master` (`752fc5f` → `3792aeb`), clean working tree. `.env` present but gitignored (`.gitignore:2-4`). No production host, no domain, no remote yet.

## Runtime baseline (severity notes)

- `GET /health` → `{"ok":true,"version":"0.2.0","pid":19896,"db":true,"hermes_bin_ok":true,"limiter_backend":"memory"}`.
- `GET /api/plans` → 4 plans (demo/starter/pro/scale). **Encoding check needed**: PowerShell displayed mojibake; retest with utf-8 (see Phase 2).
- App bound to 127.0.0.1:8787 locally (dev).

## Unknowns / risks / missing components

- No production hosting/domain/CDN/WAF; infra audit mostly BLOCKED until a VPS exists.
- Billing live path unverified (needs LIVE Paddle keys; money path **UNVERIFIED**).
- Email (verification / password reset / transactional) — **absent**.
- MFA, SSO, teams/roles — **absent** (scope: single-user accounts).
- No monitoring/error-tracking integration (only local JSONL audit + shell probe).
- No DB migration tooling (schema via `CREATE TABLE IF NOT EXISTS`; `payment_events` etc. added by init).
- Secrets: `.env` local only; rotate_secrets.py present but no documented rotation run in prod.

## Sensitive files

- `backend/data/.jwt_secret` (fallback JWT secret, gitignored), `~/.fluxswarm/fernet.key` (Fernet key, outside repo), `backend/data/byok.json` (encrypted blobs), `backend/data/audit.jsonl` (append-only audit), `backend/.env` (real values). None committed.

## First-pass findings (high-level, deep-dive in later phases)

- FLX-CI-1: CI branch mismatch (`main` vs `master`) — P2.
- FLX-DEV-1: `security.py:22-23` + `hermes_client.py:30-31` hardcode Windows dev paths with no env fallback for `security.py` — P2 portability.
- FLX-LEG-1: privacy page claims storage "in North America" + "full erasure" — must match actual ops region and actual deletion scope — P2 (Phase 10 deep-dive).
- FLX-DEMO-1: any authenticated user may read/dispatch/watch every `flux-demo-*` board (prefix is shared); demo slugs are timestamp-only (enumerable); dispatch lacks rate limit/credit gate — candidate HIGH (Phase 5 evidence).
- FLX-TG-1: anonymous Telegram mode launches swarms (max_spawn=8) with no account, credit, or rate limit — candidate HIGH (Phase 5).