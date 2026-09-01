# PHASE 0 — REPOSITORY INVENTORY (GATE 0)

- **Protocol:** MASTER AUDIT, HARDENING & PRODUCTION READINESS (41 sections)
- **Status:** INVENTORY ONLY — NO PRODUCTION CODE MODIFIED
- **Executed:** 2026-08-30
- **Method:** every item below is labelled with the evidence that supports it
  (code file:line · on-disk check · live runtime probe · test run). Items without
  evidence are `NOT VERIFIED`; nothing here was copied from prior reports into a
  claim — prior reports are treated as HISTORY only and their facts were
  re-derived from source today.
- **Classification scale:** VERIFIED / INFERRED / UNKNOWN / NOT IMPLEMENTED /
  BROKEN / PARTIALLY IMPLEMENTED / BLOCKED BY EXTERNAL DEPENDENCY

---

## 1. Repository top level

| Path | Kind | Notes (VERIFIED) |
|---|---|---|
| `.git/` | git repo | branch `master`, NO remote (see §19) |
| `.github/workflows/ci.yml` | CI | defined (see §17) |
| `audit/` | prior-protocol artifacts (58 files) | untracked, history-only |
| `backend/` | FastAPI app | see §2 |
| `deploy/` | scripts | `backup.sh bootstrap.sh Caddyfile.us monitor.sh restore.sh` — present on disk, runtime NOT VERIFIED |
| `.env.example` | config template | see §18 |
| `.gitignore` | ignore rules | see §19 |
| `Caddyfile`, `nginx.conf`, `Dockerfile`, `docker-compose.yml` | deploy manifests | present; **no container ever run/verified in this environment** |
| `GTM_LAUNCH_PLAN.md`, `README.md` | docs | present |
| `pyerr.txt` | debug leftover | ignored by git; stale capture |
| `fluxswarm-docs.zip` | doc archive | untracked; user requested earlier |

`backend/` additionally contains: `requirements.txt`, `.env` (secrets — see §18),
`DEPLOY.md`, `DEPLOY-US.md`, `PADDLE_LIVE_CHECKLIST.md`, `REVIEW_OUTCOME.md`,
`STAGE_REPORT.md`, `STAGE_REPORT_STAGE5.md`, `run.ps1`, `run.sh`,
`fluxswarm.db` (orphan SQLite **not** used by `db.py` — `db.py` uses
`backend/data/users.db`; flagged as housekeeping risk), and 23 modules + tests.

## 2. Backend module map (VERIFIED from source)

| Module | Lines | Role | Latent dependencies |
|---|---|---|---|
| `main.py` | 1439 | FastAPI app: routes, CORS, proxy-trust, CSP headers, webhook, checkout, marketing/legal pages, WS | — |
| `db.py` | 758 | SQLite persistence, plans, credits, referrals, telegram link, CCPA payloads, marketplace | `argon2-cffi` (optional-import, guarded) |
| `auth.py` | 64 | JWT HS256 (7-day), secret from env → `data/.jwt_secret` | `PyJWT` |
| `payments.py` | 451 | Gateway abstraction + Paddle MoR client + signature verify (v1 classic / v2 ts-h1) | stdlib only |
| `billing.py` | 15 | thin shim re-exporting `payments` | — |
| `vault.py` | 111 | Fernet-encrypted BYOK store (`data/byok.json`), key host at `~/.fluxswarm/fernet.key` | `cryptography` |
| `hermes_client.py` | 353 | Hermes CLI bridge (kanban boards, swarm launch, dispatch loop, BYOK env injection) | external `hermes` binary |
| `security.py` | 99 | AgentShield: static regex scan + optional `hermes skills run security-scan` | external `hermes` |
| `ratelimit.py` | 185 | IP/login/register/purchase limiters; memory backend now (`health`), Redis optional | `redis` (optional) |
| `audit.py` | 74 | append-only JSONL trail `data/audit.jsonl`, secret-key redaction | — |
| `telegram_bot.py` | 182 | bot: `/start`, `/link`, goal→swarm; reads `TELEGRAM_BOT_TOKEN` (falls back to Hermes `.env`) | `python-telegram-bot`, external Hermes |
| `serverlock.py` | 70 | single-instance lock `data/.server.lock` (stale reclaim) | — |
| `validate_paddle.py`, `rotate_secrets.py`, `test_ratelimit.py` | tools/tests | helper scripts (periphery) | — |

## 3. Stack & dependencies (VERIFIED)

- Python 3.11 target (`ci.yml`), FastAPI 0.133.1, uvicorn 0.41.0, pydantic 2,
  Jinja2, PyJWT, argon2-cffi, redis (optional), python-telegram-bot, aiohttp,
  httpx, cryptography, websockets.
- `backend/requirements.txt` exists and is the single dependency manifest.
- Runtime probe of live server `GET /health` →
  `{"ok":true,"version":"0.2.0","db":true,"hermes_bin_ok":true,"limiter_backend":"memory"}` (pid 10116, uptime ~5.5 h).

## 4. Frontend (VERIFIED)

- `backend/templates/index.html` — single SPA page, full EN/AR i18n (`t()`/`fmt()`,
  persisted `flux-lang`), premium dark/indigo theme (`--accent:#6d7cfa`,`--accent2:#9d8bff`),
  Telegram linking panel, marketplace, credits UI. Live `GET /` → 200.
- `backend/static/robots.txt`, `backend/static/sitemap.xml` — served at root via
  explicit routes (`main.py:1422`,`main.py:1427`); both probed 200.
- Live 200 probes (read-only): `/pricing`, `/privacy`, `/robots.txt`, `/sitemap.xml`, `/api/plans`.

## 5. Route inventory (VERIFIED — grep of `main.py`)

**Public / marketing / legal:** `GET /` · `/health` · `/pricing` · `/how-it-works`
· `/faq` · `/privacy` (+`-en`) · `/terms` (+`-en`) · `/refund` (+`-en`) ·
`/cookies` (+`-en`) · `/acceptable-use` (+`-en`) · `/robots.txt` · `/sitemap.xml`
· `/checkout` (Paddle.js overlay; renders a "not ready" page without
`PADDLE_CLIENT_TOKEN`, which is now set) · `/mock-checkout/{uid}/{plan}` (404 unless
`FLUXSWARM_PADDLE_MOCK=1` — flag absent → effectively disabled).

**Auth:** `POST /api/auth/register` · `POST /api/auth/login` · `GET /api/me`.

**Squad/demo:** `GET /api/plans` · `GET /api/squad` · `GET /api/demo/launch`
(cost-bearing; guarded by kill-switch + per-IP + daily caps).

**Projects (auth + ownership prefix `u{uid}-` guard):** `GET/POST /api/projects`
· `GET /api/projects/{slug}/tasks` (boards of others → 403) ·
`POST /api/projects/{slug}/dispatch` · `GET /api/projects/{slug}/security`.

**BYOK:** `GET/POST /api/keys`.

**Referral/billing:** `GET /api/referrals` · `POST /api/subscribe/{plan}` ·
`POST/GET /api/payments/webhook` (GET refuses 405) · `GET /api/payments/dev-complete/{uid}/{plan}`
(mock-only).

**Compliance:** `GET /api/account/export` · `DELETE /api/account`.

**Telegram:** `GET /api/telegram/link` · `GET /api/telegram/status` · `DELETE /api/telegram/link`.

**Marketplace:** `POST/GET /api/templates` · `GET /api/templates/mine` ·
`POST /api/templates/{tid}/buy`.

**Realtime:** `WS /ws/{slug}` (token in query, strict fail-closed auth: no token /
bad token / deleted user / unowned slug all rejected).

## 6. Data model (VERIFIED — `db.py:59`)

SQLite `backend/data/users.db` — tables: `users`, `projects`, `referrals`,
`squad_templates`, `template_purchases`, `payment_events`, `telegram_links`,
`telegram_codes`, `demo_usage` + 4 indexes; FKs enabled via `PRAGMA`.

## 7. Auth & sessions (VERIFIED)

- Argon2id passwords with in-place legacy sha256+salt upgrade (`db.py:146-187,219-237`).
- JWT HS256, 7-day expiry, secret priority env → `data/.jwt_secret` (present, 64 bytes).
- Route auth via Bearer or `fs_token` cookie; demo boards shareable, owned boards 403.

## 8. Billing, credits & Paddle — CURRENT STATE (RE-VERIFIED)

**Plans** (`db.py:39`): demo $0/3cr·×1 | starter $29/25cr·×2 | pro $99/120cr·×4 |
scale $299/500cr·×6. 1 credit = 1 launch (`main.py:455`, `db.py:302`), atomic
`BEGIN IMMEDIATE` debit, automatic refund on failed launch, referral 25 credits
once per email (race-safe, `db.py:319`).

**Payment gate & .env (NEW STATE — differs from earlier sessions):**
- `backend/.env` **now contains Paddle values** (names verified, values never
  printed): `FLUXSWARM_PAYMENTS=1` (**gate OPEN**), `FLUXSWARM_PAYMENT_PROVIDER=paddle`,
  `PADDLE_API_BASE` = sandbox URL (`*sandbox*`), `PADDLE_API_KEY` SET (prefix =
  plain `pdl_` bearer form — NOT `pdl_live`/`pdl_sbox`/`pdl_tb`; so live/sandbox
  classification by prefix is `UNKNOWN`), `PADDLE_WEBHOOK_SECRET` SET,
  `PADDLE_PRICE_STARTER/PRO/SCALE` SET, `PADDLE_CLIENT_TOKEN` SET,
  `FLUXSWARM_PADDLE_MOCK` ABSENT.
- Implication (INFERRED from code + env): `get_gateway()` returns an operative
  `PaddleGateway` (configured=True) → `/api/subscribe/{paid}` would attempt a
  real (sandbox) checkout creation against `sandbox-api.paddle.com/transactions`
  with those credentials. Because base is sandbox, **no live charge is possible
  from this state**; but the CREDENTIAL OWNERSHIP in `.env` is **NOT VERIFIED**
  and must be confirmed against a dedicated FluxSwarm org before any go-live.
  The earlier session established that the only Paddle live key the user held
  belonged to **Mizan Alhaytham's org**; whether the `.env` key is that key, a
  sandbox key, or a new org key is something only the user/API can confirm,
  not the repo.
- Webhook grant path (VERIFIED): signature-verified (v1 base64-HMAC and v2
  `ts;h1` schemes, 300 s replay window, `payments.py:151`); `payment.succeeded`
  without `user_id`+`plan` → 422 `incomplete_receipt` (`main.py:661-666`);
  idle replay dedup via `payment_events.event_id`; `payment.refunded` maps
  txn→user (`db.payment_user_by_txn`) and downgrades to demo. This is a safe path
  even if events from other products of the same org arrive (they fail the
  `custom_data` check → 422, no state change).

## 9. Providers & BYOK (VERIFIED)

- Supported BYOK providers: `anthropic` (ANTHROPIC_API_KEY), `openai`,
  `gemini`, `kimi` + free hosted `opencode-free`/`hy3-free` default
  (`hermes_client.py:56-89`). Keys Fernet-encrypted at rest (`vault.py`),
  injected to the subprocess env ONLY (`hermes_client.py:147-158`), never written
  to profile `.env` on disk (with a cleanup de-fang for legacy plaintext,
  `hermes_client.py:108-137`). API returns masked values only (`main.py:507`).
- **No external provider-specific libraries** are used — Hermes CLI resolves the
  model. Provider behavior/pricing beyond token pass-through is `BLOCKED BY
  EXTERNAL DEPENDENCY` (Hermes).

## 10. Agent integration — Hermes (VERIFIED from source)

- FluxSwarm shells out to the **external Hermes CLI** (`hermes kanban <cmd>`),
  binary at `C:/Users/DELL/AppData/Local/hermes/bin/hermes.exe` (overridable via
  `FLUXSWARM_HERMES_BIN`). On-disk check: **binary exists** (`Test-Path`=True).
- Board isolation: each project = `u{uid}-…` board; demo = `flux-demo-…`.
  On-disk kanban boards: `flux-demo-*` (4), `fluxswarm-main`, `fluxswarm-stage5`, `_archived`.
- Flow: `ensure_board()` → `launch_swarm()` / `launch_from_template()` →
  `_pin_runtime()` (forces model/provider per task) → `dispatch()` blocking loop
  in a daemon thread (`main.py:226-242`) → `list_tasks()` normalized JSON
  (`hermes_client.py:292-314`) → `read_workspace()` collects generated `.py/.md/…`.

## 11. Agent integration — ECC (VERIFIED + BLOCKED)

- **FluxSwarm contains ZERO vendored/imported ECC code.** No `import ecc`, no
  package in `requirements.txt`, no ECC source in the repo (grep across py/yml/
  json/md/html/sh/ps1 = only name strings + comments, cited below).
- All ECC contact is **by name only**, forwarded to the external Hermes CLI:
  - Squad/composition: `hermes_client.py:36-53` (profiles `ecc-planner`,
    `ecc-architect`, `ecc-devops`, `ecc-tdd`, `ecc-reviewer`, `ecc-build-fixer`;
    skills `plan-orchestrate, api-design, fastapi-patterns, docker-patterns,
    deployment-patterns, tdd-workflow, agent-self-evaluation, verification-loop,
    orch-build-mvp`).
  - Dispatcher flags: `--worker prof:title:skills`, `--verifier`, `--synthesizer`
    (`hermes_client.py:170-178, 238-246`).
  - Security skill enrichment: `security.py:40-55` (`hermes skills run security-scan`).
  - Display-name stripping of `ecc-`: `hermes_client.py:304-313`.
- **On-disk check (runtime dependency):** Hermes `profiles/` contains all 9
  `ecc-*` profiles; `skills/ecc/skills/` contains the referenced skills
  (e.g. `plan-orchestrate`, `api-design`, `fastapi-patterns`, `docker-patterns`,
  `deployment-patterns`, `tdd-workflow`, `agent-self-evaluation`,
  `verification-loop`, `orch-build-mvp` — all observed in the directory listing).
- Classification: ECC = **external, Hermes-internal agent/skill layer driven by
  subprocess name references**. ECC's own implementation/license/provenance is
  `BLOCKED BY EXTERNAL DEPENDENCY` (out of this repo) and remains **LEGAL REVIEW
  REQUIRED** (prior `05-ecc-license-positioning.md` conclusion unchanged and
  re-affirmed).

### The "Ecc Devops" bot question (candidate relationships A–E) — RESOLVED
- The "Ecc Devops" agent visible in Hermes' own UI is **Hermes' `ecc-devops`
  agent profile**. FluxSwarm references that profile (`hermes_client.py:39`) and
  launches it inside per-project boards via the Hermes CLI.
- There is **NO external "ECC API/service"**, **NO ECC API key**, and **NO
  separate ECC vendor/endpoint** used by FluxSwarm. `security.py` and
  `hermes_client.py` call only the local `hermes` binary.
- Therefore the correct characterisation is: **Ecc Devops = Hermes-internal ECC
  profile which FluxSwarm invokes by name for the DevOps role of each squad** —
  it is not an independent third-party invitation, and its presence in Hermes'
  bot list is not proof of anything FluxSwarm vends. VERIFIED from source + disk.

## 12. WebSocket (VERIFIED)

`/ws/{slug}` polls `list_tasks` every 4 s and streams `snapshot`/`update`
(`main.py:1374-1419`). Auth = `?token=` JWT; fail-closed on all rejection paths.

## 13. Background / async (VERIFIED)

- Dispatch pass in daemon `threading.Thread` (`main.py:226-242`) — HTTP request
  never blocks. No cron, no apscheduler, no message queue. Telegram bots poll the
  board with `asyncio.sleep(10) ×60` (`telegram_bot.py:118-129`).

## 14. Abuse & security controls (VERIFIED)

- CSP/explicit security headers on every response (`main.py:179-203`).
- CORS strict allow-list; `*` is a hard error; empty ⇒ same-origin (`main.py:47-80`).
- Trusted-proxy gate for `X-Forwarded-For` (CIDR-aware); otherwise socket peer
  (`main.py:89-144, 348-365`).
- Rate limits: 5 login failures/15 min·ip+email; 20 auth req/60 s·ip; 10 reg/
  hour·ip; 5 template purchases/hour·user (`ratelimit.py:27-34`); demo daily cap
  25 + operator **kill-switch** `FLUXSWARM_KILL_SWITCH` (`main.py:161-167,327-330`).
- Append-only audit trail with sensitive-key redaction (`audit.py`).
- Input hygiene on template/marketplace payloads (`main.py:1215-1274`).
- ServerLock single-instance.

## 15. Runtime probe (VERIFIED, read-only, live server)

`GET /health` 200 `{ok:true, db:true, hermes_bin_ok:true, limiter_backend:memory}`;
`/api/plans` 200; `/pricing` 200; `/privacy` 200; `/robots.txt` 200; `/sitemap.xml` 200.
(No swarm was launched during Phase 0.)

## 16. Tests (VERIFIED — executed today)

`python -m pytest tests -q` → **80 passed in 8.72 s** (baseline captured, no code
changed). Breakdown: test_backend 21 · test_db_referral_race 2 · test_db_refund 2 ·
test_fix_phase15 5 · test_legal 9 · test_paddle 22 · test_paddle_flow_api 10 ·
test_ratelimit_ip 2 · test_telegram 7.

## 17. CI / supply chain (VERIFIED, not executed)

`.github/workflows/ci.yml` runs on push/PR to master: install `backend/requirements`
+ pytest; import check; `py_compile`; `pytest backend/tests`; secret-leak static
scan (guard patterns incl. `pdl_*`, `sk-ant-*`, `AKIA*`, private keys) — plus a
`docker` job defined. **Not executed here** (no Git push/PR available): job
success state = `NOT VERIFIED`.

## 18. Configuration inventory (VERIFIED names only)

- `.env.example` documents: JWT/Fernet secrets, Redis (optional), billing gate,
  provider, Paddle (key/secret/client-token/price ids), public URL,
  plus `FLUXSWARM_*` legal/CORS/proxy vars and `FLUXSWARM_HERMES_BIN`.
- `backend/.env` **present with values** (see §8) — Python sources load env via
  `os.environ`/`python-dotenv`? → **`python-dotenv` is NOT in requirements**;
  env is injected by `run.ps1`/`run.sh`/shell or the supervisor, NOT auto-loaded
  by FastAPI. INFERRED: the running server (pid 10116) inherited its env from a
  shell that sourced `.env`; that is unverifiable from the repo. Flag for Phase 9E.
- `.env` is git-ignored; values never printed during this audit.

## 19. Git state (VERIFIED)

- Branch `master`; **no remotes configured** (push blocked).
- Working tree: `M backend/main.py` (+230/−28 lines: marketing/legal pages, CSP,
  sitemap routes — our last session's work), `M backend/static/sitemap.xml`,
  `M backend/templates/index.html`; untracked: `audit/`, `fluxswarm-docs.zip`.
- `.gitignore` covers `.env`, `backend/data/`, `*.db`, caches, `backups/`,
  `hermes_home/`, logs, `pyerr.txt`. NOTE: legacy `backend/.jwt_secret` and
  `backend/data/.jwt_secret` are covered by `backend/data/`; the orphan
  `backend/fluxswarm.db` is covered by `*.db`.
- Last commit `3241874 Audit Phase 16 fixes + launch hygiene`.

## 20. Deploy artifacts (VERIFIED presence / NOT VERIFIED runtime)

`Dockerfile`, `docker-compose.yml` (mounts Hermes install so the platform can
drive the ECC squad — `docker-compose.yml:39`), `nginx.conf`, `Caddyfile`,
`deploy/{backup,bootstrap,monitor,restore}.sh`, `deploy/Caddyfile.us`. No
container/VM was run in this session → runtime behaviour of these manifests is
`NOT VERIFIED`.

## 21. Known inventory gaps / risks (Phase 0 output)

| # | Risk | Classification |
|---|---|---|
| R1 | **Paddle creds now live in `.env` with gate OPEN (PAYMENTS=1)**; ownership of the key/org NOT VERIFIED; base is sandbox (no live charge possible today), but must be re-pinned to a dedicated FluxSwarm org + live base only at go-live | HIGH · NOT VERIFIED |
| R2 | ECC/Hermes license & provenance (external, out of repo) | HIGH · LEGAL REVIEW REQUIRED |
| R3 | App depends on an external Hermes install being present + correctly provisioned (profiles/skills/models). We verified profiles+skills exist; model availability/provider keys are external | HIGH · BLOCKED (external) |
| R4 | `.env` is not auto-loaded (no python-dotenv); runtime secrecy/provenance of loaded env is inferred, not provable from repo | MED · INFERRED |
| R5 | `redis` limiter backend never verified (health says `memory`); multi-worker rate-limiting untested | MED · NOT VERIFIED |
| R6 | No backup **restore** drill ever executed (scripts present) | MED · NOT VERIFIED |
| R7 | Orphan `backend/fluxswarm.db` + leftover `pyerr.txt` housekeeping | LOW · VERIFIED |
| R8 | No remote/GitHub; CI not exercised post-changes | MED · BLOCKED |
| R9 | Workspace artifacts (`HERMES_HOME/kanban/...`) not deleted by `/api/account` (documented gap in prior data-inventory; re-flagged 07) | MED · NOT VERIFIED |
| R10 | `PADDLE_WEBHOOK_SECRET` set with sandbox base — confirm webhook secret matches the sandbox org before any test payment | MED · NOT VERIFIED |

## 22. GATE 0 — DECISION

**INVENTORY COMPLETE. NO PRODUCTION CODE MODIFIED.** Deliverable written:
`audit/phase-0-inventory.md`. Phase-0 evidence set preserved (git untouched).

**Per protocol, work STOPS after Phase 0. Awaiting next command.**