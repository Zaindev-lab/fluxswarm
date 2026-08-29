# FluxSwarm — Deployment Guide

This document covers building and running FluxSwarm in production. It assumes
the FluxSwarm backend lives in `backend/` and is driven by the Hermes CLI
(`hermes.exe` / `hermes`) to launch ECC squads.

> Nothing in this guide restarts or touches a running instance. It is
> instructions + artifacts only — execution is a manual operator step.

## 1. Required secrets (generate once, keep secret)

Never commit these. They are read from the environment (or, for JWT/Fernet,
from `backend/data/.jwt_secret` and `~/.fluxswarm/fernet.key` if env is unset).

| Var | How to generate | Purpose |
|-----|----------------|---------|
| `FLUXSWARM_JWT_SECRET` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` | Signs auth JWTs |
| `FLUXSWARM_FERNET_KEY` | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` | Encrypts user BYOK keys at rest |

Copy `.env.example` to `.env` and fill these in.

## 2. Container build

`Dockerfile` (multi-stage-friendly, python:3.11-slim) installs backend deps and
runs uvicorn on `0.0.0.0:8787`. The Hermes CLI is expected at
`/app/hermes/bin/hermes` (override via `FLUXSWARM_HERMES_BIN`) and `HERMES_HOME`
is mounted at runtime.

```bash
docker compose build
```

## 3. docker-compose (redis + backend)

`docker-compose.yml` defines:

- **redis** — `redis:7-alpine`, persistent (`--appendonly yes`), health-checked.
  Wired to the backend via `REDIS_URL` / `FLUXSWARM_REDIS_URL` so the rate
  limiter uses the multi-worker (Redis) backend instead of in-process memory.
- **flxswarm** — builds the backend, binds **only on localhost** (`127.0.0.1:8787`)
  by default. The public entrypoint is the reverse proxy, not this port. It sets
  `FLUXSWARM_TRUSTED_PROXIES=127.0.0.1,::1` (the proxy link) and keeps
  `FLUXSWARM_PAYMENTS=0` (dev billing gate → 402 until a gateway is wired).

```bash
docker compose up -d
curl -fsS http://127.0.0.1:8787/health   # expect {"ok": true, ...}
```

The Hermes home is mounted from `$HERMES_HOST_DIR`. On Linux/macOS it typically
defaults to `~/.local/share/hermes` or wherever Hermes is installed; on Windows
the original host path was `C:/Users/DELL/AppData/Local/hermes`. Override
`HERMES_HOST_DIR` per host so boards persist and the backend can drive squads.

## 4. Reverse proxy + TLS (Caddy or Nginx)

Do NOT expose 8787 directly. Terminate TLS at a proxy and forward to the
container. The proxy host MUST be in `FLUXSWARM_TRUSTED_PROXIES` so the backend
trusts the `X-Forwarded-For` it sets (otherwise per-IP rate limiting can be
spoofed).

- **Caddy** (`Caddyfile`): automatic HTTPS via ACME. Run `caddy run --config Caddyfile`.
  For a single host, add a `caddy` service to compose that depends on `flxswarm`.
- **Nginx** (`nginx.conf`): bring your own certs (e.g. `certbot --nginx -d app.fluxswarm.ai`).
  HTTP is redirected to HTTPS; WebSockets are upgraded.

Both files set `X-Forwarded-For` / `X-Real-IP` and forward the real client IP.

## 5. CORS

Set `FLUXSWARM_CORS_ORIGINS` to the exact frontend origin(s), comma-separated,
**https** in production. A `*` is never accepted (the loader strips/ rejects
wildcards and the middleware only ever emits an explicit allow-list). Unset =
same-origin only.

## 6. Billing (Stage 3)

Paid plans return HTTP 402 while `FLUXSWARM_PAYMENTS=0` (the dev gate). The
pluggable `PaymentGateway` interface in `backend/payments.py` is the integration
point: implement a real provider (Stripe/Paddle/…) and return it from
`get_gateway()` once `FLUXSWARM_PAYMENTS=1`. The default `StubGateway` is a
no-op that never reports a paid session, so the 402 gate cannot be bypassed.

## 7. Verification before promoting

```bash
# From repo root, with the project venv python:
python -m py_compile backend/*.py backend/tests/*.py
python -m pytest backend/tests -q
```

All tests must pass and `py_compile` must be clean.
