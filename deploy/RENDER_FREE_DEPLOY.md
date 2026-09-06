# FluxSwarm — Free Hosting Deployment (0$/month)

Walkthrough for the report's Plan: **Render + Neon + Upstash + Cloudflare**.
Render free has no persistent disk -> all durable state lives in Neon Postgres;
everything else (kanban boards, memories, backups) can be re-seeded and the
`deploy/backup.sh` script pushes archives to external storage.

## Stack (0$/month)

| Component  | Service  | Free quota |
|------------|----------|------------|
| Web/Docker | Render            | 1 instance, 512MB, sleeps after 15 min idle |
| Postgres   | Neon              | 0.5GB, 100 compute-hours, scale-to-zero |
| Redis      | Upstash           | 256MB, 500K commands/month |
| CDN/SSL    | Cloudflare        | Free plan |
| Email      | Resend (optional) | 100 emails/day |
| Uptime     | UptimeRobot       | 50 monitors |

Why Neon and not Supabase: Neon scales to zero and never pauses; Supabase stops
after a week of inactivity.

## Step 1 — Neon PostgreSQL

1. https://neon.tech -> free project.
2. Copy the pooled connection string:
   `postgresql://user:pass@ep-xxx.us-east-1.aws.neon.tech/fluxswarm?sslmode=require`
3. The app auto-applies Alembic migrations on boot (`db_postgres.init_db()`);
   optionally run them ahead of deploy:
   `FLUXSWARM_DATABASE_URL="<neon-url>" python -m alembic upgrade head` (from `backend/`).

## Step 2 — Upstash Redis

1. https://upstash.com -> create Redis database (0.5GB free region, TLS).
2. Copy the REST/connection URL. It makes rate limiting multi-worker safe;
   without it the app falls back to in-process memory (single worker).

## Step 3 — Render

1. https://render.com -> New -> Blueprint, pick this GitHub repo.
   `deploy/render.yaml` is auto-detected (free Docker web service, `/health` probe).
2. In the dashboard set the two `sync: false` secrets:
   - `FLUXSWARM_DATABASE_URL` = Neon URL from Step 1
   - `REDIS_URL` = Upstash URL from Step 2
3. Deploy. First request after idle wakes the service (cold start ~10-30s).

## Step 4 — Cloudflare

1. https://cloudflare.com -> add the Render subdomain/domain.
2. Enable **Always Use HTTPS** and cache `/static/*` (assets are immutable).

## Step 5 — Verify (Part 4 checklist, code-side)

Code-side items that are now DONE in the repo and will be exercised by the checklist:

- [x] CSP nonce enabled (bullet 16) — backend/main.py `_csp_for()` + nonce attr on all templates; script-src strict nonce-only (inline `onclick` migrated to `data-onclick` delegation); style-src allows `unsafe-inline` since CSP3 ignores it next to a nonce and the dashboard sets layout via `style=""`
- [x] HSTS header (bullet 17) — `Strict-Transport-Security` on HTTPS
- [x] Webhook rate limit (bullet 18) — `/api/payments/webhook` 10 req/min/IP
- [x] Goal input sanitization (bullet 19) — `sanitize_goal()` redacts injection directives

- [ ] Neon DB running with migrations            (needs your Neon account)
- [ ] Upstash Redis running                      (needs your Upstash account)
- [ ] Render deploy successful                    (needs your GitHub + Render)
- [ ] Cloudflare SSL/DNS active                   (needs your domain/Cloudflare)
- [ ] `/health` returns 200                       (Render health check passes)
- [ ] `/api/account/admt-notice` returns JSON     (via curl after deploy)
- [ ] Demo launch works                           (click "جرب الآن" -> agent run)
- [ ] Micro-Demo works                            (goal -> micro plan, no credits)
- [ ] Paddle checkout sandbox works              (FLUXSWARM_PAYMENTS=1 + sandbox keys)
- [ ] Webhook signature verified                  (Paddle sandbox event)
- [ ] ADMT opt-out works                          (toggle in account settings)
- [ ] Human review queue works                    (monitoring/queue page)
- [ ] Email notifications send                    (Resend API key)
- [ ] Audit logs rotate                           (`audit.rotate_audit_log` on schedule)
- [ ] Backup automated                           (`deploy/backup.sh` cron/uptime cron)
- [ ] DDoS protection enabled                     (Cloudflare free)

The last 15 lines require your external accounts; nothing on the code side
listed above remains open.

## Monthly cost

| Scenario                 | Total  |
|--------------------------|--------|
| 100% free                | 0$     |
| Small commercial (100 u) | 7$     |
| Medium commercial (1k u) | 54$    |