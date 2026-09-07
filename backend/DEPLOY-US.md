# FluxSwarm — US Launch (hosting + Paddle + compliance)

Goal of this doc: the exact decisions and checklist to run FluxSwarm in the
United States with **Paddle** as the payment provider and the minimum legal
compliance posture for a US launch.

## 1. US hosting — recommended stack

FluxSwarm is a long-running FastAPI server that spawns Hermes CLI subprocesses
(CPU/RAM bursts), writes SQLite + JSON, and can talk to Redis. It is NOT a fit
for serverless/function pricing. A plain VPS with a decent CPU is the best value.

| Provider | Region used | Why | Baseline node |
|---|---|---|---|
| **DigitalOcean (Droplet)** | NYC1 / NYC3 (Newark, NJ) | simplest API + predictable $; US-East | 4 vCPU / 8 GB RAM / 160 GB SSD — **~$48/mo** |
| Vultr | New Jersey / Dallas | cheaper per vCPU, fast NVMe | High Frequency 4 vCPU / 8 GB — ~$40/mo |
| Hetzner Cloud US | Ashburn (VA) | best raw price per core | CX42 (8 vCPU/16 GB) — ~$20/mo, extra reliability focus |
| Akamai/Linode | Newark, NJ | mature, good docs | 4 vCPU / 8 GB — ~$48/mo |

**Recommendation:** DigitalOcean Droplet 4 vCPU / 8 GB in **NYC1** for US data
residency + mainstream support, with Caddy (auto-TLS) in front. If cost is the
driver, Hetzner Cloud US (Ashburn) is the same story for ~half the price.

Scale rule: each parallel swarm lane burns ~0.5–1 vCPU; 4 vCPU comfortably runs
the demo plan (1–2 lanes). For Pro/Scale (4–6 parallel) scale to 8 vCPU/16 GB.

### DNS + TLS
- Register the domain, point `A` record to the Droplet's public IP.
- Caddy (already in the repo: `Caddyfile`) provisions Let's Encrypt HTTPS
  automatically. No manual certificate management.

### What ships as-is (Stage-4 work from the swarm)
- `Dockerfile` — image build.
- `docker-compose.yml` — app + **redis** service (rate limiting) + Caddy optional
  profile; `REDIS_URL`/`FLUXSWARM_REDIS_URL` are wired.
- `Caddyfile` — reverse proxy + automatic TLS.
- `.env.example` — all secrets/knobs (JWT, Fernet, Redis, Paddle, CORS,
  trusted proxies, contact email).
- `backend/run.ps1` / `run.sh` — process supervisors with health-check respawn.

## 2. Paddle adoption (implemented)

`backend/payments.py` now ships a production `PaddleGateway`:

- `create_checkout` → server-side checkout (`POST /transactions` on the Paddle
  Billing API), bearer `PADDLE_API_KEY`,
  one `PADDLE_PRICE_<PLAN>` price id per plan, returns the hosted checkout URL.
- `handle_webhook` → verifies **both** Paddle signature schemes:
  - V1 classic: `base64(hmac_sha256(raw_body))` compared against
    `Paddle-Signature`;
  - V2 transaction: `ts=N;h1=hex(hmac_sha256(f"paddle-{N};{body}"))` **with
    ±300 s replay window**.
- Credits are granted **only** on a verified `transaction.completed` webhook —
  a client redirecting back proves nothing. Refunds downgrade the plan.
- Idempotency: `db.record_payment_event()` stores `event_id` (UNIQUE); replayed
  webhooks return 200 without double-granting.
- `get_gateway()` resolves `FLUXSWARM_PAYMENT_PROVIDER=paddle`; if keys are
  missing it returns the stub → the 402 gate stays closed (never charge by
  accident).

Endpoints:
- `GET /api/subscribe/{plan}` → returns `checkout_url` (open checkout).
- `POST /api/payments/webhook` → signature-verified, idempotent.

**Activation steps (you)**:
1. Create a **Paddle Sandbox** account → Catalog → price ids for
   starter/pro/scale (optionally the $9/10-credit Top-up refill → `PADDLE_PRICE_TOPUP`).
2. Set env: `FLUXSWARM_PAYMENT_PROVIDER=paddle`, `PADDLE_API_BASE=https://sandbox-api.paddle.com`,
   sandbox `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET` (from the webhook you
   register), `PADDLE_PRICE_*`.
3. Register the webhook URL `https://<domain>/api/payments/webhook`.
4. Sandbox test → confirm a `transaction.completed` lands in
   `backend/data/audit.jsonl` and the plan changes.
5. Switch to **live** Paddle keys, flip `FLUXSWARM_PAYMENTS=1`.

## 3. Legal compliance (US baseline)

| Item | Status | Where |
|---|---|---|
| **Sales tax / VAT** | Handled by Paddle (Merchant of Record) — Paddle is the seller of record, so US + international tax collection/remittance is Paddle's obligation, not yours | Paddle ToS/DPA |
| **Privacy Policy page (AR)** | Live | `GET /privacy` |
| **Privacy Policy page (EN)** | Live | `GET /privacy-en` |
| **Terms of Service page (AR)** | Live | `GET /terms` |
| **Terms of Service page (EN)** | Live | `GET /terms-en` |
| **Operating entity disclosure** | Optional at deploy time (env-driven) | `FLUXSWARM_LEGAL_*` in `.env` → rendered on all four legal pages |
| **CCPA/CPRA right to access** | Live | `GET /api/account/export` (returns all user rows) |
| **CCPA/CPRA right to correct** | Live | `PATCH /api/account` (update display name; audited as `account.rectify`) |
| **CCPA/CPRA right to delete** | Live | `DELETE /api/account` (removes user + projects + templates + purchases + payment events + encrypted keys) |
| **Data at rest** | Fernet-encrypted user AI keys; Argon2id passwords; audit log has no secrets | `vault.py`, `auth.py` |
| **Data in transit** | TLS via Caddy (auto), HSTS header already sent by the app | headers middleware |
| **Audit trail** | `backend/data/audit.jsonl` (auth, payments, admin actions) | `audit.py` |
| **Selling into the EU later** | Paddle covers it (its MoR model absorbs EU VAT/IOSS) → no extra billing work; only add the EU data-residency node + DPA then | — |

Pre-launch to-dos for counsel (not code): review the generated policy wording,
file any required state business registrations, and confirm the Paddle DPA is
signed before processing live payments.

## 4. One-command launch (`deploy/`)

`deploy/bootstrap.sh` automates the whole machine bring-up on a fresh Ubuntu VPS:

```bash
sudo bash deploy/bootstrap.sh --domain app.yourdomain.com --email admin@yourdomain.com
```

It installs Docker, generates `.env` with strong JWT/Fernet secrets (never
overwrites existing secrets), builds + starts `fluxswarm` + `redis`, installs
Caddy on the host (auto Let's Encrypt TLS → `127.0.0.1:8787`), enables a
`fluxswarm` systemd unit for boot persistence, then health-checks
`/health` over the domain. It exits early after writing `.env` so you can paste
your Paddle keys before serving.

Companion scripts:
- `deploy/backup.sh` — nightly tarball of DB + BYOK + `.env` + kanban volume, keeps 7 rotations.
- `deploy/restore.sh <backup.tar.gz>` — restore a snapshot.
- `deploy/monitor.sh` — health probe (exit code for cron; `*/5 * * * * … || logger -t fluxswarm DOWN`).
- `deploy/Caddyfile.us` — host-side Caddy config (domain/email templated by bootstrap).
- `backend/rotate_secrets.py` — rotate the JWT secret + Fernet key safely:
  `python backend/rotate_secrets.py` from the `backend/` directory. It snapshots the
  previous `.jwt_secret`, `fernet.key`, `byok.json` and `users.db` under
  `~/.fluxswarm/backups/<ts>/` first (the old Fernet key stays recoverable so existing
  BYOK ciphertexts can still be unwrapped), then writes fresh values. **Refuses to run
  while the BYOK vault is non-empty** (a new Fernet key would strand existing
  ciphertexts), and prints the restart hint (old sessions log out).

### In-app support assistant (ships, no setup needed)

A floating chat helper answers pricing / credits / BYOK / referrals / refunds /
Telegram / account-data questions from a built-in product knowledge base —
`POST /api/support/chat`, open, per-IP rate-limited (20 msgs / 60 s), audited.
No network calls and no keys are required for this tier. For questions the rules
can't match it escalates to `FLUXSWARM_CONTACT_EMAIL`.

Optionally, an AI fallback can answer instead of escalating (system prompt is a
strict product handbook; it sees only the chat message):

```text
FLUXSWARM_SUPPORT_AI_BASE=https://api.openai.com/v1
FLUXSWARM_SUPPORT_AI_MODEL=gpt-4o-mini
FLUXSWARM_SUPPORT_AI_KEY=<your key>   # leave unset to keep rules + escalate only
```

Any OpenAI-compatible endpoint works (OpenAI, Azure OpenAI, OpenRouter, ...);
set `FLUXSWARM_SUPPORT_AI_BASE` accordingly.

## 5. Local sandbox: see the whole billing flow TODAY (no Paddle account)

Run the backend with these env vars and the paid plans open a *sandbox* checkout
on this same server instead of calling Paddle:

```text
FLUXSWARM_PAYMENTS=1
FLUXSWARM_PAYMENT_PROVIDER=paddle
FLUXSWARM_PADDLE_MOCK=1
PADDLE_WEBHOOK_SECRET=mock-secret     # any value; only for local signing
```

Flow: `POST /api/subscribe/pro` → returns a `/mock-checkout/...` URL → the page
auto-"pays" by minting a **correctly signed** Paddle webhook → the
`transaction.completed` handler upgrades the plan and grants credits; replaying
does not double-grant; a signed refund downgrades the plan. The CCPA delete/
export endpoints and `/privacy` `/terms` are live in every mode.

**Safety guards:** sandbox endpoints 404 unless `FLUXSWARM_PADDLE_MOCK=1`, and
they refuse to run the moment a **live** `PADDLE_API_KEY` + the production
`PADDLE_API_BASE` are present — a forgotten mock flag can never mint free
credits on the live site. Tests in `backend/tests/test_paddle_flow_api.py`
prove the full flow (59 total tests green).