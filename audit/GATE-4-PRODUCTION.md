# GATE 4 — PRODUCTION HARDENING + PERFORMANCE + OPERATIONS

- **Protocol:** MASTER AUDIT (5-Gate). Prior: Gate 1 = PASS, Gate 2 = PASS,
  Gate 3 = PASS. This is **Gate 4**.
- **Date:** 2026-08-31
- **Scope:** prove the current architecture operates reliably as a SaaS
  (FluxSwarm → AgentRuntime/Hermes → Kanban → ECC → Agents → Verifier →
  Synthesizer → final result), then write `audit/PRODUCTION-RUNBOOK.md` and this
  report with an evidence-based **GATE 4 = PASS/FAIL**.
- **Evidence rules:** every claim carries evidence (code file:line — on-disk —
  live runtime — executed tests). Nothing asserted from old reports alone.
  **Never report PASS unless the test actually ran.**
- **Run env:** backend venv `C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`,
  cwd `C:\Users\DELL\fluxswarm\backend`; live app `http://127.0.0.1:8787`.
- **Code changed this gate:** `hermes_client.py` (FREE_MODEL hy3-free→nemotron-3-ultra-free,
  verifier/synthesizer assignee now profile-only), `main.py` (dispatch
  `timeout_s` 600→1800), `tests/test_dispatch_completion.py` (pin 1800).

---

## 0. VERDICT

> **GATE 4 = FAIL** (verified production blocker remains: the swarm does **not**
> reliably converge to a final result on the current Hermes runtime).
>
> - **Live, on-disk, reproducible finding:** a fresh production-path launch reaches
>   the point where all four workers run and produce **real artifacts** (e.g.
>   `hello.txt` with `hello world`), but the **verifier and synthesizer stages never
>   run** — the board is left with 4 tasks `running`, reviewer/build-fixer `todo`,
>   until the app's dispatch thread times out
>   (`audit.jsonl`: `dispatch.fire outcome=error reason=TimeoutExpired`).
> - **Root cause is external to FluxSwarm** (confirmed by reading the Hermes runtime
>   source + logs): the installed Hermes gateway's shared event loop **stalls for
>   minutes-to-hours** under swarm concurrency —
>   `hermes_cli.web_server: event loop stalled 10796.2s … (GIL pressure suspected)`.
>   14 stall events were logged in the Aug 30–31 window, several >10,000 s. When the
>   loop stalls, a finished worker's completion handshake never lands, the task stays
>   `running`, and the downstream verifier/synthesizer never start. This is a **P0
>   external/operational blocker** that makes convergence unreliable — **GATE 4 = FAIL**.
> - Three config defects found this gate and fixed in-repo (tests kept green, 140 passed):
>   (a) `hy3-free` is 401-unsupported on the `opencode-free` provider → `FREE_MODEL`
>   changed to `nemotron-3-ultra-free` (proven working); (b) verifier/synthesizer were
>   passed as `profile:title:skills` which the dispatcher treats as a non-profile
>   "terminal lane" and refuses to spawn → now profile-only (`hermes_client.py:212-213,280-283`);
>   (c) dispatch budget 600→1800s. These fixes are NECESSARY but NOT SUFFICIENT for
>   convergence because of the event-loop stall.
> - **PASS elements verified this gate:** full regression **140 passed**; live
>   health/restart/persistence; WS auth fail-closed; backup→restore→verify round-trip;
>   audit logging + no-secret-leak in logs; git/secrets hygiene; security headers;
>   error/lifecycle handling; resource behaviour measured.
> - **GATE 4 = FAIL** is issued because a **verified production blocker** (reliable
>   swarm convergence) remains. It must be resolved and re-tested before any
>   production-ready/launch claim. **STOP RULE**: Gate 5 must not start.

| # | Section | Result |
|---|---------|--------|
| 1 | Executive summary | **FAIL — convergence blocked (Hemes event-loop stall)** |
| 2 | Live runtime verification | **VERIFIED** (health/restart/persistence/WS) |
| 3 | Launch E2E | **PARTIAL — workers done w/ artifacts, verifier/synth stuck → FAIL** |
| 4 | Hermes | **VERIFIED + 3 config fixes; stall = external P0** |
| 5 | ECC | **VERIFIED presence; NO version/commit traceability → LEGAL REVIEW** |
| 6 | Swarm convergence | **FAIL (stall); worker stage verified** |
| 7 | Concurrency | **MEASURED; stall amplified by concurrency** |
| 8 | Resource usage | **VERIFIED** (app ~27MB WS; fleet ~1.2GB) |
| 9 | Cost model | **VERIFIED** (free LLM = $0; infra tiny; table below) |
| 10 | Database | **VERIFIED** (SQLite, persists, atomic snapshot) |
| 11 | Backup/restore | **VERIFIED** (live round-trip) |
| 12 | Redis | **NOT VERIFIED** (in-memory limiter; pre-multi-instance) |
| 13 | Observability | **VERIFIED** (/health + audit.jsonl + no secret leak) |
| 14 | Error handling | **VERIFIED** (fail-closed, bounded, audited) |
| 15 | Deployment | **PARTIAL** (dev host proven; TLS not performed) |
| 16 | Mailer | **NOT VERIFIED** (no SMTP in install) |
| 17 | Paddle | **VERIFIED sandbox only; LIVE NOT VERIFIED** |
| 18 | Security regression | **VERIFIED** (35 tests re-run; headers; no secrets) |
| 19 | Supply chain | **VERIFIED pins (+ one soft pin)** |
| 20 | Git / secrets | **VERIFIED** (no remote; .env/db ignored; no push) |
| 21 | Production runbook | **WRITTEN; procedures tagged VERIFIED/NOT VERIFIED** |
| 22 | Tests & evidence | **140 passed** (+ security rerun 35 passed) |
| 23 | P0–P4 | P0 = convergence (external); P1-P3 = noted below |
| 24 | External blockers | **3 confirmed** (Hermes stall; Hermes/ECC licence+ver; Paddle ownership) |
| 25 | Remaining risks | **LISTED** (mailer, TLS, Redis, free-model latency) |

---

## 1. Executive summary

GATE 4 is issued **FAIL** because the one duty of this gate — prove the production
path produces a **final result** — could not be met: the swarm converges through the
worker stage (real files written) but **never runs the verifier/synthesizer** because
the Hermes runtime's shared gateway event loop stalls for minutes-to-hours under load.
This is a **verified, reproducible P0 production blocker** located in the external
Hermes runtime (with clear on-disk/log evidence), not a FluxSwarm code defect. Three
FluxSwarm config defects that would ALSO have blocked convergence regardless were
found and fixed this gate. Every runbook/deliverable now carries the explicit caveat.
Per the protocol's FAIL rules (a verified production blocker remains), GATE 4 = FAIL.

## 2. Live runtime verification

**VERIFIED.**
- `/health` returns `{ok:true, version, pid, uptime_s, db:true, hermes_bin_ok:true,
  limiter_backend:"memory"}` (live).
- Clean shutdown/restart cycle performed repeatedly: stale pid stopped, port freed,
  fresh uvicorn pid observed with early `uptime_s`, `/health` green.
- **Persistence across restart VERIFIED:** `data/users.db` (98 KB, 2 users) and the
  Hermes kanban board set were unchanged after restart (`length`/`LastWriteTime` and
  board list identical).
- WS auth **VERIFIED** (§4): no-token → `unauthorized`; foreign slug → `forbidden`;
  valid owner → `snapshot`+`update` frames. (Live probes via `websockets` 15.0.1.)

## 3. Launch E2E (the decisive test)

**PARTIAL — fails at convergence.**
Three live launches were run via the real HTTP path as `demo@fluxswarm.ai`
(1 credit each; plan pro, `max_spawn=1`):

1. **Run A (pre-fix baseline):** all 4 workers immediately `running→blocked` —
   worker spawn fails. Diagnosis: `HTTP 401: Model hy3-free is not supported`
   + dispatcher refusing verifier/synth as non-spawnable `terminal lane`.
2. **Run B (free-model fixed):** all 4 workers reached `done`; **real artifacts
   written** (workspace 4772 B incl. `hello.txt`, docker-compose, package.json).
   BUT verifier + synthesizer stayed `queued` forever — the `profile:title:skills`
   assignee bug.
3. **Run C (both fixed; verifier/synth = profiles):** workers produced real outputs
   (`hello.txt` = `hello world`, `package.json`) **again verified**, reviewer/build-fixer
   correctly `queued` with clean profiles — **the config fixes hold live**. But the
   4 workers then stalled `running` (event-loop stall; see §4/§6) and the app's
   dispatch thread timed out:
   `audit.jsonl`: `{"event":"dispatch.fire","outcome":"error","reason":"TimeoutExpired"}`.

**Measured:** `POST /api/projects` returns in ~18–21 s (dispatch runs in background
thread); worker-stage completion ~5.5–11 min serial; the full convergence was NOT
reached in Run C (workers stuck `running` >25 min, then dispatch TimeoutExpired).
**GATE 4 = FAIL on convergence.**

## 4. Hermes

**VERIFIED + config fixes; one external P0.**
- Recon confirmed: `HERMES_BIN`/`HERMES_HOME`/`PROFILES_DIR`; `_run` timeout=300;
  dispatch multi-pass `sleep 8s`; `preflight()` checks binary+profiles (not skills).
- **Fix (a) — free model 401:** live CLI proved `hy3-free` → `HTTP 401: Model hy3-free
  is not supported` while `nemotron-3-ultra-free` and `laguna-s-2.1-free` return `OK`.
  `FREE_MODEL` changed to `nemotron-3-ultra-free` (`hermes_client.py:74`). Model pin
  observed on live cards (`provider_override:"opencode-free"`, `model_override:"nemotron-3-ultra-free"`).
- **Fix (b) — verifier/synth assignee:** Hermes `kanban_swarm.create_swarm` stores
  `--verifier/--synthesizer` verbatim as `assignee`, and the dispatcher refuses to
  spawn tasks whose `assignee` is not a real profile (`kanban_db.py:10182-10194`
  `skipped_nonspawnable … terminal lane`). We now pass `VERIFIER[0]`/`SYNTHESIZER[0]`
  (profile-only) — `hermes_client.py:212-213,280-283`.
- **Fix (c):** dispatch `timeout_s` 600→1800 (`main.py:238`) to fit a full 6-agent
  serial run (pro `max_spawn=1`); measured worker stage ~5.5–11 min.
- **External P0 (blocker):** Hermes gateway event-loop stall. `agent.log`:
  `WARNING hermes_cli.web_server: event loop stalled 10796.2s (GIL pressure
  suspected)` — 14 such events Aug 30–31, incl. `14393.8s`, `12498.6s`, `6223.5s`.
  A finished worker's handshake can't land while the loop is frozen → task stuck
  `running`. Confirmed live (worker wrappers idle at **0 CPU**, runtime subprocesses
  alive, agent.log silent ~8 min). This is in the external Hermes binary/venv, **not**
  a FluxSwarm code fix.

## 5. ECC

**Presence VERIFIED; traceability NOT AVAILABLE → LEGAL REVIEW REQUIRED.**
- All 9 referenced ECC skills exist on disk under `skills/ecc/skills`
  (plan-orchestrate, api-design, fastapi-patterns, docker-patterns,
  deployment-patterns, tdd-workflow, agent-self-evaluation, verification-loop,
  orch-build-mvp). Squad profiles present (ecc-planner/architect/devops/tdd/
  reviewer/build-fixer).
- **ECC has no `.git` and no version marker** → commit/version **traceability NOT
  AVAILABLE**. Per directive: do not modify/bundle ECC, do not fabricate licence info;
  keep **LEGAL REVIEW REQUIRED** (external blocker, §24). Not bundled into the ship
  artifact; referenced only via Hermes profiles.

## 6. Swarm convergence

**FAIL (external stall).**
- **Worker stage VERIFIED repeatedly:** all 4 workers run with real, on-disk outputs
  (`hello.txt` = `hello world`, `package.json`, `docker-compose.yml`) in Runs B and C.
- **Verifier/synthesizer stage NOT reached in any run** because the prior stage never
  transitions to `done` while the gateway event loop is stalled (§4).
- Partial-failure/retry behaviour: `crashed → retry_status:ready` observed in Run A
  (old bug); no runaway process accumulation observed (worker gateways are bounded);
  dispatch thread bounded by `timeout_s=1800` then audited as `dispatch.fire/error/
  TimeoutExpired`. **No infinite loop, no credential leak** — but no convergence either.
- **Decision:** the "complete Launch convergence (final result)" criterion is **NOT met**.
  This alone forces **GATE 4 = FAIL** (§PASS rules: "complete Launch convergence verified").

## 7. Concurrency

**MEASURED.** Two concurrent live board runs were present in Run C (Run C workers +
a prior leftover); the event-loop-stall warnings increased with concurrent agent load
(GIL pressure). Plan `parallel` caps: demo=1, starter=2, pro=4, scale=6 (`db.py:39-43`);
the app passes `max_spawn=PLANS[...]["parallel"]`. **Isolation:** each project maps
1:1 to its own `--board` slug (`hermes_client.py`), so board data is isolated; the
single-host Hermes gateway is the shared bottleneck. **Concurrency is the amplifier
of the stall** — do not raise `max_spawn` until the blocker is resolved.

## 8. Resource usage

**VERIFIED (single-node).**
- App (uvicorn) working set ~27 MB; whole `python.exe` fleet ~1.2 GB, dominated by the
  Hermes gateway + per-profile agent runtimes (8 alive gateway subprocesses under the
  ～4-profile free model).
- Free-model worker session ~110–120 MB per agent process; after a run the boards and
  workspaces persist on disk (re-runnable).
- **Resource failure mode understood:** the constraint is the shared Hermes gateway's
  event loop (GIL/CPU), not RAM; RAM headroom on the dev host is ample.

## 9. Cost model

Assumptions stated: free hosted model → **$0 direct LLM cost**; app runs on the dev
host at negligible infra (one uvicorn); dominate cost on BYOK keys is the provider's
model usage; one **credit per own-board launch**; plan price/credits from `db.PLANS`.
Credit cost per launch = plan price ÷ plan credits (Starter/Pro/Scale). Free model =
**$0 LLM per launch regardless of volume**.

| Metric | Starter $29/25cr | Pro $99/120cr | Scale $299/500cr |
|---|---|---|---|
| Credits / launch | 1 | 1 | 1 |
| $ / launch (credit) | $1.16 | $0.825 | $0.598 |
| $ / 100 launches | ≈$116 | ≈$82.50 | ≈$59.80 |
| $ / 1,000 launches | ≈$1,160 | ≈$825 | ≈$598 |
| Free LLM (per-launch) | $0 | $0 | $0 |

BYOK note: with a paid key the per-launch LLM cost = tokens × provider rate (not
bounded above here); on the free route LLM cost is the dominant-time-but-not-$
component. Infra: at ≤1 node, essentially flat (a few $/mo at most); the real scaling
cost is Hermes runtime latency + the convergence risk in §4/§6. No invented provider
prices used.

## 10. Database

**VERIFIED.** SQLite `data/users.db` (98 KB, 2 users, persists across restart).
Backups use the SQLite Backup API → atomic consistent snapshot safe against the live
file (`backup.py:43-66`). `db.py` covers users/projects/credits/plans/audit. Safe
restore re-verified via round-trip in §11.

## 11. Backup / restore

**VERIFIED (live round-trip).**
```
python backup.py backup …  → backup_<ns>.zip (users.db, audit.jsonl, byok.json, .jwt_secret)  10,051 B
python backup.py restore <zip> --target <tmp> …  → restored: .jwt_secret, audit.jsonl, byok.json, users.db
python backup.py verify <target> → {"users":2,"projects":4,"audit_lines":77}
```
Plus 6 unit tests (`test_backup_restore.py`) PASS. Restore is containment-checked
(only flat allowed basenames; hostile archives ignored/refused). **VERIFIED.**
DAO board workspaces are NOT in the backup (re-runnable artifacts); back up
`HERMES_HOME` separately if needed (documented in runbook §5, NOT VERIFIED).

## 12. Redis

**NOT VERIFIED / not required for single node.** Rate limiter backend = in-process
`memory` (`/health.limiter_backend`), single instance. `redis==5.2.1` is pinned in
`requirements.txt` and the limiter supports a Redis backend, but nothing is running
and **no multi-instance is configured (no distributed lock/board writer)**. Decision:
do NOT add Redis until (a) the convergence blocker is fixed and (b) horizontal scale
is actually needed. Not added this gate (would be unnecessary infra).

## 13. Observability

**VERIFIED.** `/health` exposes ok/version/pid/uptime/db/hermes_bin/limiter_backend.
Structured append-only audit `data/audit.jsonl` captured this gate
(`auth.login`, `project.create`, `dispatch.fire error TimeoutExpired`). Uvicorn
access+error logs to `%TEMP%\opencode\*.log`. **No secret leakage verified** in logs
or audit files (scan for PADDLE_CLIENT/JWT/Fernet/API_KEY = none).

## 14. Error handling

**VERIFIED.** Fail-closed where it matters: WS auth (reject on no/malformed/foreign
token), preflight before board creation (`preflight()`), webhook signature-verified
+ replay-window + idempotent, credit concurrency tests green, `_run(timeout=300)` and
dispatch `timeout_s=1800` bound runaway, malformed dispatch output degrades to
`{"raw":…}` (test), audit records failures. **Bounded, leak-free, audited.**

## 15. Deployment

**PARTIAL.** Dev-host start/stop/restart proven (§2). Linux/Docker is supported by
`FLUXSWARM_HERMES_BIN`/`HERMES_HOME` path overrides (code, not exercised here).
**TLS/HTTPS = NOT VERIFIED** — app runs plain HTTP; the `.env` public URL is a
trycloudflare tunnel origin; no deployed cert exercised. Not performed (external).

## 16. Mailer

**NOT VERIFIED.** No SMTP/mailer in the install. Reset tokens are returned in-band
only in dev/test (`main.py:508,532`). Production mail delivery is not operational →
**EXTERNAL OPERATIONAL BLOCKER / NOT VERIFIED** (carried from Gate 3).

## 17. Paddle

**VERIFIED (sandbox only).** Webhook signature verification (`Paddle-Signature`,
300 s replay window), idempotent transaction/refund handlers, local mock gated behind
`FLUXSWARM_PADDLE_MOCK=1`, `.env` = `https://sandbox-api.paddle.com` only. Paddle
checkout overlay CSP + `sandbox` env flag verified. **LIVE = NOT VERIFIED**; do not
switch to LIVE or create LIVE credentials (user directive).

## 18. Security regression

**VERIFIED.** Re-ran the security suite: `tests/test_tenant_isolation.py`,
`test_auth_session.py`, `test_password_reset.py`,
`test_account_deletion_extended.py`, `test_gate3_ux.py`, `test_ratelimit_ip.py` =
**35 passed**. Security headers present (`Content-Security-Policy`, `X-Content-Type-
Options:nosniff`, `X-Frame-Options:DENY`, `Referrer-Policy:no-referrer`,
`Permissions-Policy`; CSP allows only Paddle origins; CORS explicit no `*`) —
`main.py:176-203`. No secret leak in logs (§13). **PASS.**

## 19. Supply chain

**VERIFIED.** `requirements.txt` pins exact versions (`==`) for 11 of 12 deps
(fastapi, uvicorn, websockets, pydantic, Jinja2, httpx, PyJWT, argon2-cffi, redis,
python-telegram-bot, aiohttp); **one soft pin** `cryptography>=42.0.0`. No unpinned
transitive-overrides. Reviewer/ecc licence + ECC version traceability remain OUT (see §24).

## 20. Git / secrets

**VERIFIED.** Root git repo has **no remote** (no push). Modified: auth.py, db.py,
hermes_client.py, main.py, sitemap.xml, index.html, test_paddle_flow_api.py.
Untracked: `audit/`, backup.py, new tests, fluxswarm-docs.zip. **`.env` and
`data/users.db` are git-ignored** (`git check-ignore` confirmed). No secrets committed.
**No push** (directive). Private keys: `data/.jwt_secret` (64B) present; Fernet key
from vault, not in `.env` (masked).

## 21. Production runbook

**WRITTEN: `audit/PRODUCTION-RUNBOOK.md`** with every section labelled
**VERIFIED** or **NOT VERIFIED**: Start (VERIFIED), Stop/restart (VERIFIED), Launch E2E
(PARTIAL/NOT VERIFIED), WS auth (VERIFIED), Backup/restore (VERIFIED), Data
persistence (VERIFIED), Redis (NOT VERIFIED), Observability (VERIFIED), Secrets
(VERIFIED handling / LIVE NOT VERIFIED), Mailer (NOT VERIFIED), Paddle (sandbox
VERIFIED / LIVE NOT VERIFIED), TLS (NOT VERIFIED), Scheduled backups (NOT VERIFIED),
Resource (VERIFIED), Cost (VERIFIED), plus operational decision gates and STOP RULE.

## 22. Tests & evidence

- **Full suite: 140 passed** (differs from baseline 140 only by the re-pinned
  `test_dispatch_completion.py`; all prior 139 kept green alongside the new pin).
- Security re-run: **35 passed**. Dispatch completion: 4 passed. Backup/restore: 6 passed.
- Live evidence: 3 HTTP launch traces (journaled: slugs, states, timings, credits),
  WS probes, audit.jsonl, restart/persistence, memory/CPU snapshots, backup round-trip.

## 23. P0–P4

- **P0 (OPEN, external):** swarm convergence blocked by Hermes gateway event-loop
  stall → GATE 4 = FAIL. Fix is in the Hermes runtime/host (GIL), not FluxSwarm code.
- **P1 (FIXED this gate):** free-model 401 (`hy3-free` dead); verifier/synthesizer
  non-spawnable assignee; insufficient dispatch budget. All verified live + tests green.
- **P2 (open, benign/ops):** no auto backup scheduler; no Redis for multi-instance;
  soft `cryptography` pin.
- **P3 (open):** none new.
- **P4 (open, backlog):** ECC version marker; optional locking.

## 24. External blockers (not resolvable in-repo)

1. **Hermes gateway event-loop stall** (P0) — verified live + logs; blocks convergence.
2. **Hermes/ECC commercial licence review + ECC version/commit traceability** — ECC has
   no `.git`/version marker → **LEGAL REVIEW REQUIRED**, keep NOT AVAILABLE (do not modify/bundle).
3. **Paddle credential ownership + LIVE** — sandbox only; ownership is **EXTERNAL
   VERIFICATION REQUIRED**; no LIVE keys.

## 25. Remaining risks

- **Convergence risk (P0, external):** as above — primary reason for FAIL.
- **Free-model latency/liveness:** workers can run 5.5–11 min+ serial; free pool
  latency + GIL stall → long tails; a paid/BYOK key reduces latency but not the
  convoy/GIL stall.
- **Mailer, TLS, LIVE Paddle:** each NOT VERIFIED (§16/§15/§17) and required before a
  public launch.
- **Multi-instance** without Redis/board-lock would risk data/board races (§12).
- **Docs/zip** `fluxswarm-docs.zip` is untracked but present — confirm it contains no
  secrets before any future sharing.

---

## STOP RULE

**GATE 4 = FAIL.** The verified P0 convergence blocker must be resolved and re-tested
green (full convergence to a final result through synthesizer) before any
production-ready or launch claim. Per protocol I **STOP and await user authorization** —
**I will NOT start Gate 5, will NOT declare production-ready, will NOT switch Paddle
to LIVE, will NOT create LIVE credentials, and will NOT push to GitHub.**
