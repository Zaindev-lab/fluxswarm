# FluxSwarm — PRODUCTION RUNBOOK

- **Protocol:** MASTER AUDIT (5-Gate). This runbook accompanies **Gate 4 (Production
  Hardening + Performance + Operations)**.
- **Date:** 2026-08-31
- **Status:** Procedures below carry an explicit **VERIFIED** or **NOT VERIFIED** tag.
  A **NOT VERIFIED** tag means the procedure has NOT been demonstrated on the live
  server in this gate (usually because it is external: TLS, mailer, Paddle LIVE,
  Redis cluster) — treat it as unproven until exercised in `:PROD`.
- **Run env:** backend venv `C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`,
  working dir `C:\Users\DELL\fluxswarm\backend`. Live app on `http://127.0.0.1:8787`.

> **Read this first (Gate-4 headline finding):** a swarm launch does **NOT** yet
> reliably converge to a final result. Root cause is external to FluxSwarm: the
> installed Hermes runtime's shared gateway event loop **stalls for minutes-to-hours**
> under concurrency (`hermes_cli.web_server: event loop stalled \d+.?s (GIL pressure
> suspected)`; 14 stall events in the Aug 30–31 window, several >10,000 s). When the
> loop stalls, a finished worker's completion handshake never lands, the task stays
> `running`, and the verifier/synthesizer never start. **See GATE-4-PRODUCTION.md §1/§3/§21.**
> Until that is resolved, treat "converges to final result" as **BROKEN** for
> operational purposes and do not scale concurrency up blindly.

---

## 1. Start the server

**VERIFIED** (performed repeatedly this gate; live pid observed, /health green).

From the repo root (Windows dev host):

```powershell
$env:HERMES_HOME = "$env:LOCALAPPDATA\hermes"      # Hermes runtime location
# load backend\.env keys into the process environment (run.ps1 does this)
python -m uvicorn main:app --host 127.0.0.1 --port 8787
```

Health check (must return `"ok":true` and `hermes_bin_ok:true`):

```powershell
Invoke-RestMethod http://127.0.0.1:8787/health
```

Expected: `db:true`, `hermes_bin_ok:true`, `limiter_backend:"memory"`.

On Linux/Docker point `FLUXSWARM_HERMES_BIN` at the binary and `HERMES_HOME` at the
Hermes profile dir; the same code path resolves them (`hermes_client.py:30-33`).

## 2. Stop / restart

**VERIFIED.** Port owner is `uvicorn main:app`. Stop the port owner (and its parent
venv python if present), confirm the port is free, then start again (see §1). A
shutdown/restart cycles the in-process rate-limiter and resets `uptime_s`; SQLite
data persists across restart (see §6).

```powershell
# find owner and stop it
$owner = (Get-NetTCPConnection -LocalPort 8787 -State Listen).OwningProcess
Stop-Process -Id $owner -Force
# confirm free, then start (see §1)
```

## 3. Launch a swarm project (live E2E) — **PARTIALLY VERIFIED**

**VERIFIED up to the point that all four workers reach `done` and write real outputs
(e.g. a `hello.txt` artifact). NOT VERIFIED that the swarm converges through
verifier → synthesizer to a final result: see the Gate-4 headline finding.**

API flow (as the demo account):

1. `POST /api/auth/login` `{email, password}` → returns `token`.
2. `GET /api/me` → note `credits` (a successful own-board launch debits **1 credit**).
3. `POST /api/projects` `{name, goal}` → `{slug, root_id, workers[], verifier_id, synthesizer_id}`.
4. `GET /api/projects/{slug}/tasks` → poll; terminal when **all** tasks are `done|blocked`.
5. `GET /api/projects/{slug}/workspace` → final synthesized artifacts.
6. WebSocket `ws://host/ws/{slug}?token=...` for live snapshots/updates.

Worker-stage timing measured this gate: ~5.5–11 min for all 4 workers under the free
model (serial, `max_spawn=1`). The verifier/synthesizer stages did **not** complete
because of the Hermes event-loop stall; re-drive `dispatch` (`hermes kanban --board
<slug> dispatch --max 1`) if a run stalls, but expect it may need the gateway to
recover first.

## 4. WebSocket auth behaviour

**VERIFIED.**
- No token → `error "unauthorized"`, connection closed.
- Token for a slug that is not the caller's (`u{uid}-*` or `flux-demo-*`) → `error "forbidden"`, closed.
- Valid owner token → `snapshot` then `update` frames (live task states).

## 5. Backup / restore

**VERIFIED** (live round-trip this gate: `backed-up → restored → verify`).

From `backend/`:

```powershell
python backup.py backup                                   # writes <repo>/backups/backup_<ns>.zip
python backup.py backup --out <DIR> --source backend/data # custom out/source
python backup.py restore <archive.zip> [--target DIR] [--no-verify]
python backup.py verify <data-dir>
```

- Backs up: `users.db` (consistent SQLite snapshot via the Backup API, safe while
  live), `audit.jsonl`, `byok.json`, `.jwt_secret`. Retention `KEEP=7` (env
  `FLUXSWARM_BACKUP_KEEP`).
- Restore is containment-checked (only flat allowed basenames, never escapes target).
- **VERIFIED sample:** restored archive → `users=2, projects=4, audit_lines=77`.
- **Caveat:** DAO board workspaces live under `HERMES_HOME/kanban/boards` and are NOT
  part of this backup (they are re-runnable artifacts). If you must preserve them,
  back up `HERMES_HOME` separately. **NOT VERIFIED** (not exercised this gate).

## 6. Data / persistence

**VERIFIED:** `data/users.db` persists across server restart (same row count post-restart).
Also persisted: `data/audit.jsonl`, `data/byok.json`, `data/.jwt_secret`, `data/.server.lock`.
`backups/` (repo root) currently holds archives from manual runs only — configure a
scheduled backup (see §13) before any reliance on recovery.

## 7. Redis / distributed readiness

**NOT VERIFIED.** The rate-limiter backend is in-process **memory**
(`limiter_backend:"memory"`), single-instance. `redis==5.2.1` is present in
`requirements.txt` and the limiter supports a Redis backend, but no Redis is
configured/running and **no multi-worker horizontal scaling is configured**. Do NOT
run more than one app instance against the same `data/` without introducing Redis +
file/board locking; the Hermes boards are currently single-writer via the local
CLI. **Redis is RECOMMENDED before multi-instance** (after the Gate-4 convergence
blocker is resolved). Not wired this gate (out of scope / not required for single node).

## 8. Observability

**VERIFIED:**
- `/health` = `{ok, version, pid, uptime_s, db, hermes_bin, hermes_bin_ok, limiter_backend}`.
- Structured audit log `data/audit.jsonl` (append-only; auth/project/dispatch events)
  — e.g. `project.create` and `dispatch.fire outcome=error reason=TimeoutExpired`
  observed this gate.
- Uvicorn access + error logs to `%TEMP%\opencode\fluxswarm_gate4.log` / `_err_gate4.log`
  (no secrets present — verified no `PADDLE_CLIENT/JWT/Fernet/API_KEY` strings in logs).

**VERIFIED (no secret leakage):** process logs and audit lines do not contain
credential material.

## 9. Credentials / secrets

**VERIFIED (handling):** `.env`, `data/*` are git-ignored; provider keys are injected
only into each agent subprocess environment (never written to profile `.env` —
`cleanup_profile_keys` actively strips any that leaked; `hermes_client.py:115`).
`data/.jwt_secret` is a 64-byte secret. Paddle values in `.env` are sandbox-only.

**NOT VERIFIED:** Paddle LIVE credentials, and who OWNS the Paddle account (see
external §Gate-4 §24). No LIVE keys are present and none should be added here.

## 10. Mailer

**NOT VERIFIED.** There is **no SMTP/mailer** in this install. Email-dependent flows
mint their token and return it in-band only in dev/test (`main.py:508,532`:
"dev/test channel only"). Production email delivery (reset links, invoices,
notifications) is **NOT operational**. Any procedure relying on emailed links is
**NOT VERIFIED** and must be deployed + tested before :PROD.

## 11. Paddle / payments

**VERIFIED (sandbox path only):** webhook signature verification
(`Paddle-Signature`, replay window), idempotent transaction/refund handlers, and a
local mock that drives the same signed path are tested (Gate-2 paddle tests green).
`.env` uses `https://sandbox-api.paddle.com` only; the mock is gated behind
`FLUXSWARM_PADDLE_MOCK=1`.

**NOT VERIFIED:** any LIVE Paddle transaction. Do not switch to LIVE or create LIVE
credentials without explicit authorization.

## 12. TLS / HTTPS

**NOT VERIFIED.** The app runs plain HTTP on `127.0.0.1:8787` for dev/testing;
`.env` advertises a public base URL of a trycloudflare (tunnel) origin. **No real
deployed TLS certificate or known-origin HTTPS termination has been exercised.**
Terminate TLS at a reverse proxy (with a managed cert) before any public traffic.
Not performed in this gate (external deploy).

## 13. Scheduled backups (ops)

**NOT VERIFIED.** No scheduler is configured. Recommended cron/systemd example (not
run here):

```powershell
# nightly backup, keep 14
$env:FLUXSWARM_BACKUP_KEEP=14; cd backend; python backup.py backup
```

Until a scheduler is attached, recovery depends on manual runs in §5.

## 14. Resource behaviour

**VERIFIED (single-node observations):**
- App (uvicorn) working set ~27 MB; whole `python.exe` fleet ~1.2 GB (dominated by
  the Hermes gateway + per-profile agent runtimes).
- Free-model swarm: 8 gateway subprocesses observed under 4-profile load.
- **VERIFIED failure mode:** under concurrent swarm load the Hermes gateway event
  loop stalls for minutes-to-hours (GIL pressure) — see headline finding. Budget
  your dispatch window accordingly and monitor `agent.log` for
  `event loop stalled`.

## 15. Cost model

See `GATE-4-PRODUCTION.md §7` for the full per-launch / per-100 / per-1,000 table
and stated assumptions. Headline: on the **free** hosted model, direct LLM cost per
launch = **$0**; infra cost is tiny at low volume; dominant real cost on BYOK keys
is the chosen provider's model usage.

---

## Operational decision gates

- **P0 (now):** swarm convergence blocked by Hermes event-loop stall → do not
  declare production-ready, do not scale concurrency, do not market
  "converges to final result" until re-tested green.
- **External follow-ups (§Gate-4 §24–26):** Paddle LIVE ownership, Hermes/ECC
  licence + version traceability (ECC has **no `.git`**), jurisdiction review, mailer,
  TLS,+ Redis for multi-instance.
- **STOP RULE:** Gate 5 (launch/scale) must not start without explicit authorization.
