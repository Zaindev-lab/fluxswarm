# GATE 1 — ARCHITECTURE + RECONCILIATION

- **Protocol:** MASTER AUDIT (5-Gate). Gate 0 = PASS (inventory). This is Gate 1.
- **Date:** 2026-08-30
- **Scope:** Reconcile prior audits with current source/runtime. NO production code modified.
- **Evidence rules:** every claim below carries EVIDENCE (code file:line · on-disk · live runtime ·
  executed tests · git). Nothing is copied from old reports as a claim; facts re-derived today.

---

## 1. Verified architecture (AS-BUILT, from current source)

```
User (Browser / Telegram)
   │  JWT (Bearer | fs_token cookie) | WS token?query
   ▼
FastAPI single process (main.py:1439 lines)
   ├─ Routes: auth, projects, tasks, dispatch, keys(BYOK), subscribe, webhook,
   │          checkout, legal/marketing pages, account(export/delete), telegram,
   │          templates/marketplace, /ws/{slug}
   ├─ Middleware: security headers+CSP, CORS allow-list, trusted-proxy XFF gate
   ├─ db.py ──> SQLite backend/data/users.db (9 tables, FKs)
   ├─ payments.py ──> Paddle (MoR) sandbox/live API + webhook signing (v1/v2)
   ├─ vault.py ──> Fernet-encrypted BYOK store backend/data/byok.json
   ├─ ratelimit.py ──> memory backend (Redis optional, currently inactive)
   ├─ audit.py ──> append-only backend/data/audit.jsonl
   └─ hermes_client.py ──> subprocess: hermes.exe kanban {boards|swarm|dispatch|list|set-model}
         ▼
   Hermes Kanban (HERMES_HOME/kanban/boards/<slug>)
         ▼
   ecc-* profiles (external Hermes install): planner·architect·devops·tdd (+reviewer+synthesizer)
         ▼
   ECC skills (/skills/ecc/skills): plan-orchestrate, api-design, … , orch-build-mvp
         ▼
   Workspace output files → read back by read_workspace() → UI/WebSocket
```

**VERIFIED** by: full source read (all 12 modules), 80-test green run, live `/health`+route probes,
on-disk Hermes profiles/skills/boards inspection, and a real prior API launch re-parsed from the
Kanban data (see §4).

## 2. Dependency graph (as-built) vs intended

**Intended (protocol's approved direction):**
`FluxSwarm → AgentRuntime (THIN INTERNAL ABSTRACTION) → HermesAdapter → Hermes → Kanban → ECC profiles/skills`

**Actual (source):**
`FluxSwarm (main/telegram/security) →import→ hermes_client → subprocess hermes CLI → kanban → ecc-*`

> **CONTRADICTION #1 (P2):** The **`AgentRuntime`/`HermesAdapter` abstraction is NOT implemented.**
> Current dependants `main.py:31`, `security.py:20`, `telegram_bot.py:22` import `hermes_client`
> directly. The approved architecture is a direction, not yet shipped. Fix (thin, save change, Gate 4):
> introduce a small `AgentRuntime` module re-exporting a narrow, stable interface
> (`launch(board, goal, agents, provider_keys)` / `dispatch(...)` / `tasks(board)` / `read_workspace(...)`)
> backed by `hermes_client` (HermesAdapter), and switch the three import sites. No behaviour change.
> Do NOT build a native agent runtime.

## 3. FastAPI component verification table (VERIFIED from source)

| Component | Status | Evidence |
|---|---|---|
| Frontend (index.html SPA, EN/AR i18n, dark/indigo) | VERIFIED | template present; live `/` = 200; `ecc` occurrences in index.html = **0** (grep) |
| Backend (12 modules) | VERIFIED | py_compile sweep: 0 failures |
| API routes | VERIFIED | 47 route decorators indexed (see Phase-0 inventory §5) |
| Database (SQLite, 9 tables, FK on) | VERIFIED | db.py:59-143 |
| Auth (Argon2id + JWT HS256, 7d) | VERIFIED | db.py:146-237, auth.py — **password reset: NOT IMPLEMENTED** (grep) |
| Authorization (ownership `u{uid}-` prefix + WS strict) | VERIFIED | main.py:481-503, 1356-1419 |
| WebSocket (`/ws/{slug}`, 4s poll, fail-closed) | VERIFIED | main.py:1374-1419 |
| Agents/skills wiring | VERIFIED | hermes_client.py:36-53; on-disk profiles+skills exist |
| Hermes | VERIFIED external binary | `hermes.exe` exists; kanban boards listed live |
| ECC | VERIFIED external layer | no vendored code; profiles/skills under HERMES_HOME |
| ecc-devops | VERIFIED profile-invoked | see §6 |
| Providers/BYOK (anthropic/openai/gemini/kimi + free) | VERIFIED | hermes_client.py:56-89, vault.py, main.py:518-558 |
| Paddle (MoR) sandbox config | VERIFIED (credential **ownership NOT VERIFIED**) | see §7 |
| Credits (atomic debit, refund-on-failure, referral once) | VERIFIED | db.py:302-359, main.py:455-470, telegram_bot.py:90-110 |
| Redis | NOT REQUIRED now | /health `limiter_backend:memory`; no REDIS_URL; see §11 |
| Storage (SQLite+JSONL+boards on disk) | VERIFIED | backend/data + HERMES_HOME/kanban |
| Git | VERIFIED, no remote | see §8 |
| Deployment manifests | VERIFIED presence, runtime NOT VERIFIED | Dockerfile/docker-compose/Caddyfile/nginx/deploy/*; no container ever run here |
| Legal pages EN+AR | VERIFIED live | /privacy /terms /refund /cookies /acceptable-use (+ -en) all 200 |
| UX | PARTIALLY VERIFIED (deep audit = Gate 3) | pages render; interactive UX matrix deferred |

## 4. Trace of ONE complete real Launch (VERIFIED, live artifact)

Board `flux-demo-1787966004` — created earlier by the app itself (its swarm ran on the default
free runtime). Re-parsed today via `hermes kanban --board flux-demo-1787966004 list --json`:

```
root        fluxswarm                        done   hy3-free / opencode-free  (Swarm: Build a sample FastAPI notes API)   <- created_by=fluxswarm
worker 1    ecc-planner                      done   hy3-free / opencode-free  (Plan the feature breakdown)
worker 2    ecc-architect                    done   hy3-free / opencode-free  (Design system architecture)
worker 3    ecc-devops                       done   hy3-free / opencode-free  (Set up CI/CD and containerization)
worker 4    ecc-tdd                          done   hy3-free / opencode-free  (Write the test suite)
verifier    ecc-reviewer:reviewer:…          ready  hy3-free / opencode-free  (Verify swarm outputs)
synthesizer ecc-build-fixer:builder:…        todo   hy3-free / opencode-free  (Synthesize swarm outputs)
```

Workspace produced artifacts: `PLAN.md`, `README.md`, `requirements.txt`, `.venv/`, `.ruff_cache/`
(real generated code). This confirms end-to-end: **API→hermes_client→hermes.exe kanban→kanban→
ecc-* profiles→skill execution→on-disk result**, with `_pin_runtime()`'s model/provider pinning
visible on every card. Honest note: this particular demo run did not reach "builder done" (reviewer
stalled at `ready`, builder `todo`) — real runs can be partial; that is precisely the runtime
behaviour later Gates will harden (dispatch completion, cancellation, failure semantics).

## 5. Hermes verdict

**REQUIRED — runtime dependency for the core product.**
- The entire swarm feature is a subprocess against `hermes.exe` (`hermes_client.py:140-158`);
  `/health` even reports `hermes_bin_ok` (`main.py:214`). No fallback agent runtime exists.
- Without Hermes/`HERMES_HOME`: launch fails. Missing-profile/skill handling is today only a
  generic `RuntimeError` (`hermes_client.py:180-181`) → **Gate 2 fix (P1): fail early with clear
  diagnostic when binary/profiles/skills missing.**

## 6. ECC verdict + ecc-devops relationship (RESOLVED)

- **ECC:** `EXTERNAL RUNTIME DEPENDENCY` — REQUIRED for the swarm feature. Not vendored, not
  imported: referenced **by name only** and forwarded to the local Hermes CLI
  (`hermes_client.py:36-53`, `security.py:40-55`). Its own implementation/license lives outside
  the repo → **LEGAL REVIEW REQUIRED** (unresolved, impacts final GO).
- **ecc-devops:** FluxSwarm invokes the **`ecc-devops` profile** via Hermes CLI/Kanban as the DevOps
  worker (`hermes_client.py:39`), driving ECC skills `docker-patterns,deployment-patterns`. The
  "Ecc Devops" Bot shown in Hermes' UI is the same Hermes-internal profile — **FluxSwarm does NOT
  invoke any UI Bot/chat interface, does not use an ECC hosted SaaS, and has no ECC API key.** The
  chain in reality is: **FluxSwarm → Hermes CLI → kanban → ecc-devops profile → ECC skills/files**
  (verified by source + the live board list above, where `assignee=ecc-devops` on a FluxSwarm
  created board).
- The protocol's rule is satisfied: production orchestration does **not** depend on the manually
  created UI Bot.

## 7. Paddle reconciliation (no secrets exposed)

- `.env` state (names only): `FLUXSWARM_PAYMENTS=1` (**gate OPEN**), provider `paddle`,
  `PADDLE_API_BASE` **contains `sandbox`**, `PADDLE_API_KEY` SET (prefix NOT `pdl_live`/`pdl_sbox`/
  `pdl_tb` — class `UNKNOWN`), `PADDLE_WEBHOOK_SECRET` SET, `PADDLE_PRICE_STARTER/PRO/SCALE` SET,
  `PADDLE_CLIENT_TOKEN` SET, `FLUXSWARM_PADDLE_MOCK` ABSENT (mock disabled).
- Reconciliation: matches the documented **Sandbox-staging posture** (`payments.py`, `.env.example`,
  `PADDLE_LIVE_CHECKLIST.md` order: sandbox keys → gate 1 → webhook test → live promotion).
  Consistent with the checklist step "sandbox…→ FLUXSWARM_PAYMENTS=1".
- **Blocks (NOT VERIFIED):** (a) credential **ownership** — repo cannot prove whether this key
  belongs to a dedicated FluxSwarm org, a personal sandbox, or the previously identified Mizan org
  (user's earlier live key); (b) whether the webhook secret matches the sandbox org's registered
  webhook. **No live charge is possible while base=sandbox** (PaddleGateway uses configured base;
  `_use_mock()` off). Gate-2/4 items: never promote base to live without replacing key+prices+
  webhook secret with dedicated FluxSwarm org values.
- No Paddle secret was read or printed.

## 8. Git & secret exposure (VERIFIED, clean)

- Branch `master`, **no remotes**. 52 tracked files. Working tree: `M backend/main.py`,
  `M backend/static/sitemap.xml`, `M backend/templates/index.html`; untracked `audit/`,
  `fluxswarm-docs.zip`.
- **Tracked-secret scan (working tree + full history):** only benign hits — `secrets.token_urlsafe`
  code, test fixtures (`AKIA1234567890ABCDEF`, `sk-ant-abc`, `"super-secret"`), CI grep patterns.
  **No real credentials in repo or history.** VERIFIED.
- `git check-ignore` confirms `backend/.env`, `backend/data/users.db`, `backend/fluxswarm.db`
  ignored; `backend/data/` covers `.jwt_secret`/`.fernet_key`/`byok.json`/`audit.jsonl`.
  `audit/` is untracked (by choice) — not ignored → must not be pushed until reviewed.

## 9. Account deletion / data lifecycle (PARTIALLY VERIFIED; gaps)

- Deletes (VERIFIED, db.py:578-602 + vault.py:101 + main.py:1156-1163): user row, projects,
  templates authored, template purchases (buyer), referral rows (as referrer), payment_events,
  telegram_links, and the user's encrypted BYOK keys. Export (`/api/account/export`) returns full
  payload (CCPA).
- **Gaps:** (P1) Hermes board workspaces under `HERMES_HOME/kanban/boards/u{uid}-*` are **NOT
  deleted** on account deletion (documents/artifacts remain on disk). (P2) `telegram_codes` rows of
  a deleted user persist until TTL/expiry sweep. (By-design) append-only `audit.jsonl` is not
  erasable (explicitly disclosed in privacy page).

## 10. Backup / restore (NOT VERIFIED — NO BACKUP EXISTS)

- `deploy/backup.sh`, `deploy/restore.sh` exist (presence VERIFIED).
- **No `backups/` directory exists anywhere** and no restore drill has ever been run.
  ⇒ By protocol rule: **backup is NOT VERIFIED until restore is tested.** P1 for Gate 4.

## 11. Redis verdict

- **NOT REQUIRED for the current single-process deployment.** `ratelimit.py:172-182` uses the
  in-process memory backend when no reachable Redis URL is set; live `/health` =
  `limiter_backend:memory`; `.env` has no `REDIS_URL`. docker-compose defines a redis service for
  multi-worker setups, but there is no evidence of multiple app workers today. Do **not** add Redis
  as a production requirement unless scaling to ≥2 workers (per protocol, avoid unnecessary infra).

## 12. Test suite (VERIFIED — executed today)

- `python -m pytest tests -q` → **80 passed** (breakdown 21/2/2/5/9/22/10/2/7 across 9 files).
- `py_compile` on every backend module → **0 failures**. CI (`.github/workflows/ci.yml`) mirrors
  this + a secret-leak scan; CI job itself NOT executed here (no remote).

## 13. Contradictions ledger (reconciliation output)

| # | Contradiction / drift | Classification | Action |
|---|---|---|---|
| C1 | Intended `AgentRuntime→HermesAdapter` vs actual direct `import hermes_client` (3 sites) | P2 | Gate 4 thin abstraction; no behaviour change |
| C2 | Billing gate: earlier sessions had `FLUXSWARM_PAYMENTS` off; **`.env` now has gate = 1 + sandbox creds** (state changed externally) | P1 (must pin dedicated org before live) | §7; Gate 2/4 |
| C3 | Backup scripts exist but **no backup has ever been created** | P1 | Gate 4 (create + restore drill) |
| C4 | Docs (`DEPLOY.md`/`README`) describe env-driven launch; confirmed `run.ps1` loads `.env` manually (no python-dotenv) — loader is supervisor-dependent | P3 | Keep; document in DEPLOY |
| C5 | `fluxswarm.db` (orphan SQLite at backend root, ignored) not used by `db.py` (`data/users.db` used) | P3 | Housekeeping; remove when safe |
| C6 | `login` lacks explicit `logout` endpoint and there is **no password reset / email infra** | P2/P1 | Gate 2 (document as scope decision or add) |
| C7 | demo swarm runs can end non-terminal (reviewer `ready`, builder `todo` on a live demo board) — dispatcher completion semantics | P2 | Gate 4 (termination/cancel/timeout) |

## 14. Verified vs unverified claims (required by protocol)

**VERIFIED:** architecture (source+runtime), all routes/auth/authz/ws gating, credit atomicity and
referral-once, BYOK encryption + env-only injection, Paddle signature schemes + idempotency +
incomplete-receipt rejection, single-instance lock, trusted-proxy XFF, CSP headers, CORS strict,
demo caps + kill switch, 80 tests green, secret-free git tree+history, Hermes/ecc/ecc-devops
pipeline real-trace, legal pages live, `.env` Paddle state (names/booleans).

**NOT VERIFIED / BLOCKED:** Paddle credential ownership & org match (§7) · webhook secret ↔ org ·
ECC/Hermes license & exact upstream version/commit (external) · backup→restore (§10) ·
deployment manifests runtime (no container) · CI pass on CI infra · multi-worker/Redis safety.

## 15. Finding priority (recommended fixes; NO fixes applied in Gate 1)

- **P0:** none discovered.
- **P1:** (1) Paddle creds→dedicated FluxSwarm org + do not promote to live base until then;
  (2) account deletion leaves Hermes board artifacts on disk; (3) no backup/restore exists;
  (4) missing-Hermes/skills failure diagnostics.
- **P2:** AgentRuntime/HermesAdapter abstraction (approved architecture); WS/authz edge coverage;
  demo non-terminal completion semantics; password-reset decision; purge stale `telegram_codes`.
- **P3:** orphan `fluxswarm.db`; supervisor-dependent env loading docs; housekeeping.
- **P4:** none material.

## 16. GATE 1 verdict

**GATE 1 = PASS** — Reconciliation complete. Architecture, dependency graph, Hermes/ECC/ecc-devops
relationships, Paddle sandbox state, git/secret posture, data lifecycle, backup status, Redis
necessity and test suite were re-verified from current source/runtime. No production code modified.
P0/P1/P2 findings above are queued for Gate 2 (security/business fixes) and Gate 4 (production
hardening), per explicit authorization.

**STOP. Awaiting user authorization for GATE 2.**