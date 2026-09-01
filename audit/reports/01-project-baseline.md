# PHASE 1 — PROJECT BASELINE (commercial-readiness protocol)

Runs on local app `http://127.0.0.1:8787`. Everything below is from actual
routes/code/tests (evidence: `setup phaseN1-baseline-routes.txt`, prior audit
reports 01–18, TEST-LEDGER). No assumptions.

## What the product is (VERIFIED)
- FluxSwarm: web SaaS where a user types a product goal; a 6-agent devops squad
  (Planner→Architect→DevOps→TDD→Reviewer→Builder) builds a real codebase live on a
  kanban board streamed over WebSocket. Credits meter launches; BYOK lets users
  pay their own tokens; plans+marketplace+referrals+billing via Paddle MoR.

## Frontend (VERIFIED)
- Single-page Jinja2 template `backend/templates/index.html` (AR-first, full EN
  i18n via `I18N`/`setLang`, persisted `flux-lang`), no build step, inline CSS.
  Server-rendered landing, JS state via DOM APIs, no framework, no routing lib.
- Routes serving pages: `/`, `/checkout`, `/privacy[/-en]`, `/terms[/-en]` (+
  `/static`). WebSocket `/ws/{slug}` live board.
- Accessibility: skip-link, roles, focus-visible, reduced-motion added (Phase 16);
  no browser tooling → visual checks BLOCKED, code-level only.
- Responsive: flex/grid viewport-based layout (CODE) — device-level BLOCKED.

## Backend (VERIFIED)
- FastAPI (Python 3.11+, single process, uvicorn, `127.0.0.1:8787`), 36 routes
  (list captured): auth, projects, dispatch, security, keys(BYOK), plans,
  subscribe, payments webhook, demo, referrals, templates/buy, telegram link,
  account export/delete, legal pages, health, ws. `/docs`+`/redoc` served in dev.
- Background squad dispatch via daemon threads (`_bg_dispatch`, non-blocking);
  Paddle gateway abstraction (StubGateway fail-safe); argon2id auth; JWT 7-day.
- No queues/workers beyond dispatch threads; no cache layer (memory limiter).

## Database (VERIFIED)
- SQLite single file via `db.py`; 9 tables (users, projects, referrals,
  squad_templates, template_purchases, payment_events, telegram_links,
  telegram_codes, demo_usage) + 4 indexes; FKs now enforced per-connection
  (PRAGMA ON). No migrations framework; `init_db()` idempotent CREATE TABLE IF
  NOT EXISTS. Tenant isolation = board-slug prefix (`u{id}-*`) enforced server-side.
  Backups: NOT configured (launch checklist).

## AI system (VERIFIED, code)
- External orchestration: `hermes` CLI subprocess (`hermes kanban ... swarm/
  dispatch`) with Hermes agents `ecc-*` (planner/architect/devops/tdd/reviewer/
  build-fixer) and ECC skills (`skills/ecc/skills`). Model resolution: BYOK
  (anthropic/openai/gemini/kimi) or free `hy3-free`/`opencode-free` default.
  Timeouts: subprocess 300 s; blocking dispatch 600 s poll loop, 8 s sleep.
- No in-repo prompts/tools/memory layer — agent behavior lives in Hermes/ECC
  (out of repo → sub-system NOT auditable here, see Phase 3 report).

## Infrastructure / business (current state)
- Hosting: dev Windows host only; docker-compose (redis optional) in repo;
  no VPS/domain/TLS (BLOCKED). Plans: demo $0/3cr, starter $29/25cr(×2),
  pro $99/120cr(×4), scale $299/500cr(×6); subscribe gate 402 when payments off;
  referral +25 credits once; marketplace earn 50%; refund → downgrade keeps
  credits; credits never expire. Live money path BLOCKED (no LIVE Paddle keys).

## Security (headline; Phase 7/06-report for detail)
Argon2id, JWT tamper-proof, per-IP limiter + login lockout, CSP + security
headers, tenant 403 isolation, webhook signature v1/v2 + replay dedup,
password ≤4096 (post-fix), demo daily cap + operator kill-switch (post-fix).

## Test posture (VERIFIED this protocol start)
red-team 29/29 · baseline 80/80 · fix suite 5/5 (evidence phase18-final.txt).

## Key unknowns / blocked
UK-GDPR addendum · US live compliance review · unit/AI operating costs · ECC
license (Phase 6) · visual QA · production infra · GitHub push.