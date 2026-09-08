# FluxSwarm — Master Pre-Launch Review (2026-09-07)

**Scope**: Architecture/tech, application & infra security, US/UK digital compliance,
pricing/marketing/sales. Status: verified against current `master` (`9d947d5`).
All findings below confirmed by direct read/grep on the working tree.

---

## Priority 0 — Blocks rendering or shipping AT ALL (fix first, in this order)

| # | Sev | Location | Problem | Fix |
|---|-----|----------|---------|-----|
| P0.1 | **CRIT** | `deploy/docker/docker-compose.beta.yml:41-44` | `deploy.resources.limits.memory: 8G` (and `4G` reservations) vs Render free tier = **512MB**. Container will be **OOM-killed instantly** or refused. | Drop to `memory: 512M`, `reservations: 128M`. Reconcile `FLUXSWARM_MEM_TOTAL_MB` (currently `8192`) in the same file — set to `~450`. Mem-derived concurrency (`MAX_IN_PROGRESS`) recalcs automatically. |
| P0.2 | **CRIT** | `backend/security.py:22-23` | `HERMES_BIN = "C:/Users/DELL/AppData/Local/hermes/bin/hermes.exe"`, `HERMES_HOME = "C:/Users/DELL/..."` — **hardcoded Windows dev paths**. AgentShield scan breaks on every Linux/Docker/Render deployment; worse, on Render `hermes.exe` path = false-negative security scan (CM-2). | Import from canonical `hermes_client` module: `from hermes_client import HERMES_BIN, HERMES_HOME` (overridable via env `FLUXSWARM_HERMES_BIN`). |
| P0.3 | **CRIT** | `backend/db.py:1181` | Demo user **hardcoded password `demo1234`** (weak, guessable; demo user is the sole target of anonymous demo launches). | Generate at seed time: `pw = secrets.token_urlsafe(12)` stored nowhere (demo account is headless). |
| P0.4 | **CRIT** | `backend/main.py:2768` | `launch_error = str(e)` returned raw to client on **template purchase/launch**. Leaks internal paths/versions. (Other `str(e)` leaks were already fixed — this one remains.) | Map to generic category: `"launch_error": "provider_unavailable"`; keep full detail server-side + audit only. |
| P0.5 | **CRIT** | `backend/main.py:997-1012` | Demo launch **mutates process-global `os.environ["FLUXSWARM_DEFAULT_PROVIDER"]`** temporarily. Concurrent demo launches race → provider/model mismatch, wrong-account dispatch. | Thread `provider`/`model` through `_fire_dispatch` → `hc.launch_swarm(..., provider=..., model=...)` (pass explicit kwargs). Never mutate process env. |

---

## Priority 1 — Publish-blocking (security & DELL-borne paths neutralized, not yet correct/repo-dirty)

| # | Sev | Location | Problem | Fix |
|---|-----|----------|---------|-----|
| P1.1 | **CRIT** | `backend/db.py:40-43`, `db_postgres.py:59-62` | **Arabic `desc` strings still in PLANS** (`"تجربة مجانية محدودة"` …). These are served by `/api/plans` → shown raw to EN users.  English-only project is incomplete. | Replace with EN descriptions in BOTH db.py and db_postgres.py. |
| P1.2 | **CRIT** | `backend/db.py:301,507`, `db_postgres.py:276,476`, `backend/hermes_client.py:549` | **Arabic `ValueError` messages** (`"البريد مسجّل مسبقاً"`, `"باقة غير صالحة"`, `"القالب لا يحتوي وكلاء صالحين"`) — reach users via API. | English messages. |
| P1.3 | **CRIT** | `backend/telegram_bot.py` (37 strings) | Entire bot is **Arabic-only**. Telegram users get Arabic commands/messages. | English prompts (optionally bilingual but EN-first). |
| P1.4 | **HIGH** | `backend/main.py:2841,2847` | WebSocket handler calls **sync `hc.list_tasks(slug)` (subprocess) inside async** — blocks the entire event loop every 4s per connection. Free-tier OOM/event-loop starvation. | `tasks = await asyncio.to_thread(hc.list_tasks, slug)`. Add per-user WS connection cap (e.g. 5). |
| P1.5 | **HIGH** | `backend/db.py:_conn()` | **Fresh SQLite connection per call** — no `PRAGMA foreign_keys=ON` on connections except at init (`db.py:55`); every request opens 3-4 conns. FKs unenforced. | Thread-local persistent conn + `PRAGMA foreign_keys=ON` on every connect helper. |
| P1.6 | **HIGH** | `backend/main.py:1373-1377` | Launch-failure credit refund **bypasses `db.refund_launch_credit()`** — direct SQL `UPDATE ... credits=credits+1` + raw str leak = double-refund risk. | Use `db.refund_launch_credit(user["id"])` (idempotent, audited) + generic error body. |
| P1.7 | **HIGH** | `backend/main.py:2533` | `/admt-notice` hardcodes `lang="ar"` (content is EN-only now). Serve `lang="en"`. | Change default lang to `"en"`; keep `/admt-notice-en` alias. |
| P1.8 | **MED** | `backend/hermes_client.py:1490` | `read_workspace()` uses `board` in filesystem path **without `_SAFE_SLUG_RE` validation** (delete_boards has it; this is missing). | Add slug regex validation inside `read_workspace()` (defense-in-depth). |

---

## Priority 2 — UK / US compliance hardening (must-have for paid launch)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| P2.1 | **HIGH** | **No age gate**: US COPPA (13+), UK Age Appropriate Design Code (13+), GDPR-UK (16+ in UK). Signup has no age/ToS consent. | (a) ToS clause: "You must be at least 13, and if resident in the UK/EU at least 16, to use the Service." (b) Registration form: add required **"I accept the Terms & Privacy Policy (and confirm I am 13+/16+)"** checkbox — persist `tos_accepted_at` on the user row. |
| P2.2 | **HIGH** | **No cookies/consent UI**: site sets `flux-lang` + `fs_token` (localStorage). Copy at `/cookies` claims "no banner needed under UK PECR" — localStorage ≠ cookies, but the "strictly necessary" argument is weak without a banner; ICO risk. | Add minimal dismissible banner for EU/UK visitors ("We use strictly necessary storage only") OR gate by `?consent=pending`; either way keep the `/cookies` page honest. |
| P2.3 | **MED** | **Data-retention mismatch**: Privacy page says "90 days after deletion" (`main.py:1866`) but `delete_user()` hard-deletes immediately. | Either implement soft-delete + 90-day purge job, or change policy to "deleted immediately." Cheapest correct: update policy text. |
| P2.4 | **MED** | **JWT in localStorage** (`index.html`). XSS-accessible token; nonce-CSP mitigates but flimsy. | Ship HttpOnly+Secure+SameSite=Lax cookie for `fs_token` (Paddle redirect-safe), drop localStorage copy. Medium effort — schedule post-launch if needed. |
| P2.5 | **MED** | **Refund policy** barely mentions UK Consumer Contracts Regs 2013 (14-day cooling-off for digital content). | Add explicit UK CCR 2013 sentence + FTC/CARD-network note that rights cannot be waived; `as-is` clause already carves out statutory rights — keep that. |
| P2.6 | **LOW** | No DPO/UK representative contact; Paddle is MoR but you remain data controller. | Add DPO email (e.g. privacy@fluxswarm.ai) + ICO address line to privacy page. |
| P2.7 | **LOW** | `security.py` regex `openai_key: sk-[A-Za-z0-9_-]{20,}` — false-positively flags the legit `sk-or-v1-…` OpenRouter header? (grep shows none in tracked files — verify runtime.) | Keep, low priority; fine-tune later. |

---

## Priority 3 — Pricing & marketing corrections (competitive + conversion)

### 3.1 Adopt credit-pack pricing (one-time, no subscription) — recommended table

| Plan | Current | Recommended | Per-launch $/equiv | Note |
|------|---------|-------------|--------------------|------|
| Free / Demo | 3 credits | **5 credits** | $0 | Bigger runway → more word-of-mouth |
| Starter | $29 / 25 | **$19 / 20** | ~$0.95 | Below $20 barrier; competes with $20/mo subs |
| Pro (anchor) | $99 / 120 | **$49 / 60** | ~$0.82 | Mark "Most popular" |
| Scale | $299 / 500 | **$149 / 200** | ~$0.75 | — |
| Top-up (NEW) | — | **$9 / 10** | ~$0.90 | Impulse top-up; anchors per-credit value |

- **Copy fix (H3/M2)**: everywhere say **"One-time credit pack — no monthly fee."** Plan tiers are credit packs + parallel caps, not subscriptions. Keep "credits never expire."
- **Homepage conversion (H6/M1)**: unhide `#pricing` on `/` or add condensed pricing teaser in the hero; currently hidden behind nav click.

### 3.2 Trust-badge honesty (FTC / UK ASA risk — false-advertising)
Badges on `index.html:143-146`:
- "400+ automated tests" → link to test suite or rephrase "test-driven, 380+ CI tests".
- "GDPR & CCPA aligned" → **"designed for GDPR & CCPA compliance"** (stronger claim needs counsel).
- "Prompt-injection hardened" → **"prompt-injection defences"** (regex-based, not comprehensive).

### 3.3 Referral program (virality) — recommended spec
- Referrer: 15 credits (was 25) + referred friend gets **+10 credits on first paid purchase** (already in GTM plan, not implemented).
- Cap 500 credits/referrer (anti-farming). Keep "paid subscription required" gate. Consider 90-day expiry on referral bonus.
- Implement in webhook `record_payment_event` path (on first successful paid event of referred user).

### 3.4 Missing marketing must-haves
- ``og:image`` (1200x630) missing on all pages — add brand card.
- **No analytics** (no Plausible/Fathom). Add single-line Plausible.js (cookieless, no consent banner needed) — essential from day 1.
- **No post-signup email / no "ran out of credits" nudge** (GTM plan mentions Resend; nothing built). Add minimal Resend welcome + depletion email.
- `GTM_LAUNCH_PLAN.md` is **Arabic** — translate to EN before sharing with any US/UK stakeholder.
- `/pricing`, `/how-it-works`, `/faq` missing `<link rel="canonical">` (homepage has it).

---

## Priority 4 — Architecture / ops debt (post-launch, non-blocking)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| P4.1 | MED | `deploy/backup.sh` backs up only SQLite `backend/data`. Render/Prod uses Postgres+Neon. | Add `pg_dump` branch when `FLUXSWARM_DATABASE_URL` starts with `postgres`. |
| P4.2 | MED | Dockerfile has no `alembic upgrade head` step; PG schema applied manually → fragile deploys. | Add migration step (entrypoint pre-start -- idempotent). |
| P4.3 | MED | `deploy/monitor.sh` checks `"ok":true` but `snapshot_health()` reports `ok` only if hermes bin exists → false-unhealthy. | Check `"db_ok":true` and `"hermes_bin_ok":true` explicitly. |
| P4.4 | MED | **`/metrics` absent** (no Prometheus). | Add simple `/metrics` (requests, boards, credits). Nice-to-have. |
| P4.5 | MED | `db_postgres.py` (asyncpg) has **zero automated coverage** — tests all skipped (need live PG). | Add CI service Postgres job + unskip. |
| P4.6 | LOW | PLANS / legal handlers duplicated (`/privacy-en` vs `/privacy` etc.). | Extract shared helper; keep routes. |
| P4.7 | LOW | `render.yaml` — confirm `FLUXSWARM_ENV=production`, `FLUXSWARM_PAYMENTS=1`, disk mount for `HERMES_HOME` boards (kanban SQLite must survive restart; PG stores auth/credits). Ensure `preDeployCommand` validates secrets only, not full migration. |

---

## Confirmed already correct (do not regress)
- CSP **nonce-only** scripts (`main.py:326-338`), no `unsafe-inline` for JS. ✔
- HSTS + X-Frame-Options DENY + nosniff + Referrer-Policy + Permissions-Policy + book-only Cache-Control. ✔
- Argon2id pw hashing; SHA256 legacy auto-upgrade; session invalidated on logout/pw-change/reset. ✔
- Password-reset tokens single-use, hashed at rest, 15-min TTL; **no account enumeration** (identical response). ✔
- Login rate limit (5/15min/IP+email) + global IP (20/60s); reg (10/h/IP); demo (1/h/IP + 20/day global + 25 daily). 429 = structured JSON. ✔
- KMS envelope encryption for BYOK; keys never returned by API (masked only); plaintext keys scrubbed from disk post-launch. ✔
- `public_user()` strips all sensitive fields; data export excludes `pw_hash`/keys/logged_out_at. ✔
- No hardcoded secrets in tracked code (grep `sk-or-v1` & `sk-…` across yml = none). ✔ Hardcoded OpenRouter key already removed → `${OPENROUTER_API_KEY:-}`. ✔
- SQL fully parameterized; slug charset allowlist on filesystem ops; prompt-injection redaction on goals; subprocess without `shell=True`. ✔
- Webhook idempotence via UNIQUE `event_id` + `record_payment_event` dedupe; 300s replay window; dual sig (v1+v2). ✔
- Credit refund on fail-before-work, idempotent via `launch_refunded`; kill-switch; circuit-broken reaper with backoff. ✔
- Missing-`await` register bug **already fixed** (`index.html:460` → `setUser((await r.json()).user)`). ✔
- English-only **templates & main.py** (iron: templates/main.py 0 Arabic; remaining finds are db.py/db_postgres.py/telegram_bot.py — P1.1-1.3). ✔
- `.gitignore` + `.dockerignore` now exclude `hermes-src/`; favicon added; `cryptography==50.0.0` pinned. ✔
- Tests: **381 passed / 7 skipped** on Local-SQLite path. ✔

## Suggested commit sequencing
1. `fix(critical): render OOM + security.py hardcoded paths + demo pwd + str(e) leak + env race` — P0.* + P1.6
2. `fix(i18n): purge remaining Arabic from db/db_postgres/telegram_bot; admt default en` — P1.1-1.3, P1.7
3. `fix(async): non-blocking WS list_tasks + read_workspace slug guard + conn/PRAGMA` — P1.4-1.5, P1.8
4. `feat(compliance): age/TOS gate, PECR banner, refund wording, retention text` — P2.*
5. `feat(pricing): new credit packs + pricing copy + trust badges + og:image + analytics` — P3.*

Each commit: full `FLUXSWARM_DEMO_MODE=1` pytest run (381 target). Then one `docker compose --build` + Render Blueprint deploy.