# PHASE 26 — PRODUCTION CONFIGURATION (deploy-time)

Source of truth: `DEPLOY.md` + `PADDLE_LIVE_CHECKLIST.md` + code env reads
(grep-verified list below). This is the go-live runbook mapping env to effect.

## Env matrix (green = verified in code)
| Env | Effect |
|-----|--------|
| `FLUXSWARM_JWT_SECRET` | session signing (required; rotate) |
| Fernet key file / `vault.py` | BYOK encryption (no secret in repo) |
| `FLUXSWARM_PAYMENTS=1` | **enables** live Paddle path (dev 402 gate off) |
| `FLUXSWARM_PADDLE_MOCK=0` | use real gateway, not mock |
| `PADDLE_API_KEY`, `PADDLE_CLIENT_TOKEN`, `PADDLE_WEBHOOK_SECRET` | live checkout + webhook; webhook secret = signature verify |
| `PADDLE_ORIGINS` | authorizes vendor-side origins (CSP bypass for iframe) |
| `FLUXSWARM_PUBLIC_BASE_URL` | canonical/absolute links (site + sitemap domain swap) |
| `FLUXSWARM_CORS_ORIGINS` | API allowlist (no wildcard) |
| `FLUXSWARM_TRUSTED_PROXIES` | correct client-IP under reverse proxy |
| `FLUXSWARM_KILL_SWITCH` / `FLUXSWARM_DEMO_DAILY_CAP` | fail-closed + demo abuse cap at 25 |
| `FLUXSWARM_LEGAL_*` (entity/registry/tax/address/phone) | operating-entity block on legal pages (empty until set) |
| `FLUXSWARM_CONTACT_EMAIL` | support link everywhere |
| `HERMES_BIN`/`FLUXSWARM_HERMES_BIN`, `HERMES_HOME` | agent/spawn config |
| `ANTHROPIC/OPENAI/GEMINI_API_KEY`, `PROVIDER_*`, `HERMES_DEFAULT_*` | provider defaults for BYOK flow |
| `TELEGRAM_BOT_TOKEN/USERNAME`, `TELEGRAM_LINK_TTL` | bot pairing (gate delegation) |
| `FLUXSWARM_ALLOW_MULTI` | multi concurrency toggle |

## Deploy runbook (from DEPLOY.md, not yet executed → BLOCKED item)
1. Provision VPS (US region), lock firewall, Ubuntu LTS, non-root deploy user.
2. systemd unit → uvicorn `main:app`; TLS via ACME certbot; Nginx reverse proxy.
3. Set env above from secret store; persist WAL SQLite + vault + audit to
   encrypted disk; schedule snapshot/backup; document restore drill (INFRA-1).
4. Swap `YOUR_DOMAIN` in `static/sitemap.xml` + `robots.txt` Sitemap line.
5. Money path: sandbox E2E → LIVE keys → `FLUXSWARM_PAYMENTS=1`.
6. Post-go checks: `pip-audit` clean, `/health` upstream-alert, backup verified.

## Hard pre-launch gaps (from earlier phases)
ECON-2 metering · DEPS-1 scan in CI · ARCH-1 error sandbox · backup drill run.
All small; None blocks config correctness.