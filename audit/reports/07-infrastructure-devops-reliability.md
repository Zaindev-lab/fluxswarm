# 07 — Infrastructure / DevOps / Reliability Audit

Phase: 7 / 19 — Live probe: `/health` → `{"ok":true,"version":"0.2.0","pid":19628,
"db":true,"hermes_bin_ok":true,"limiter_backend":"memory"}` (single worker).
Compile gate (DEPLOY.md §7): `py_compile` OK on all 12 app modules.

## Topology (as designed)
- App: FastAPI + uvicorn, single worker, bound `127.0.0.1:8787` (`main.py` `__main__`).
- Reverse proxy: Caddy (ACME auto-TLS) or Nginx (certbot) → TLS terminated at proxy,
  `X-Forwarded-For` trusted via `FLUXSWARM_TRUSTED_PROXIES`.
- Container: `Dockerfile` (python:3.11-slim) + `docker-compose.yml`:
  redis:7-alpine (appendonly, healthchecked) for the multi-worker limiter,
  kanban volume persists boards, `restart: unless-stopped`, app healthcheck on
  `/health`. Hermes CLI mounted via `HERMES_HOST_DIR`.
- Single-instance guard `serverlock.py` (O_EXCL pid lock, stale reclaim,
  `FLUXSWARM_ALLOW_MULTI` bypass) — prevents port/DB contention.
- Secrets: env-driven, with `backend/data/.jwt_secret` + `~/.fluxswarm/fernet.key`
  fallbacks; `.gitignore`d (git-status evidence: `.env` never in repo).

## Strengths (verified)
1. **Defense-in-depth defaults**: billing gate closed (`FLUXSWARM_PAYMENTS=0` →
   402, RT-E1), CORS deny-by-default with explicit allow-list (wildcard never
   emitted), proxy trust list required for rate-limit integrity, no port exposure
   outside 127.0.0.1 in compose.
2. Monorepo tooling: CI runs tests on Python 3.11 + secret-leak grep scan + docker
   image build (`ci.yml`). Would catch most regressions *if triggered*.
3. `/health` exposes runtime wiring (pid, uptime, DB reachability, hermes-bin
   presence, limiter backend) — ops-friendly.
4. Pin-to-major deps (`requirements.txt`) → reproducible-ish builds;
   `cryptography>=42` is the only loose pin (acceptable, security-fix cadence).
5. `rotate_secrets.py` + `validate_paddle.py` exist as operational playbooks for
   key rotation and Paddle setup validation.
6. DEPLOY.md documents secret generation, proxy trust, CORS and promotion gates.

## Findings (verified)

### FLX-CI-1 — CI never runs on this repo's default branch (MEDIUM)
VERIFIED: `ci.yml:4` triggers on `branches: [main]`; repository default branch is
`master` (git). Every `git push origin master` sails past CI. Also the CI sdjb
step `python -c "import main"` runs from `backend/` on ubuntu where the default
`HERMES_BIN` Windows path won't exist (import OK, but the import check does not
exercise the bridge; misleading coverage).
Fix: `branches: [master]` (+ PR trigger already present), or rename branch to
`main`. Phase 15.

### FLX-DEV-1 — Hardcoded Windows host paths as fallbacks (LOW)
`hermes_client.py:30-31` default `HERMES_BIN`/`HERMES_HOME` to the operator's
Windows paths; env-overridable (`FLUXSWARM_HERMES_BIN`, `HERMES_HOME`) and the
Dockerfile/compose override them. Latent footgun only — any Linux host running
uvicorn directly without env would try `C:/…` paths. Phase 15: detect-none→fail
fast with a clear error.

### INFRA-2 — Production is BLOCKED (not a code defect — an environment gap)
No VPS/host, no domain, no DNS, no live TLS, no live Paddle keys. Per protocol
this is **BLOCKED** (state + reason + follow-up):
- container build/push workflow (image registry) — none;
- IaC for the proxy/certs — files exist but no running deployment;
- backup job for `data/` (DB-4) — none yet;
- central logs / error alerting — none (audit.jsonl local only);
- canary/rollback runbook — none.

### INFRA-3 — Single-writer memory limiter (MEDIUM, scale)
`limiter_backend: memory` live. Compose wires Redis (`REDIS_URL`/`FLUXSWARM_REDIS_URL`),
but nothing enforces it; a multi-worker deploy without Redis silently dilutes
rate limits (roughly ×worker count). Phase 15: fail fast unless Redis is
configured when workers>1.

### INFRA-4 — Long-running WS poll is synchronous (MEDIUM)
`main.py` WS loop calls `hc.list_tasks(slug)` synchronously (subprocess) every 4s
in the event loop → blocks ALL requests on that connection during the poll, and
spawns ~15 subprocesses/min for an idle board. Confirmed code-path (RT-T9 only
tests auth; load profile not live-probed). Phase 15: offload to executor +
in-flight dedup. (Already recorded as ARCH-2; promoted to infra-class owner.)

## Reliability posture
| Concern | Status | Evidence |
|---------|--------|----------|
| Single point of failure (DB, boards) | present by design (single node) | data/users.db; kanban volume |
| Restart policy | container `unless-stopped` + app healthcheck | docker-compose.yml |
| Crash recovery (credit races) | BEGIN IMMEDIATE + refunds | db.py; RT-W5 |
| Queue/backpressure for Hermes launches | none (synchronous spawn) | hermes_client.py |
| Observability | /health + audit.jsonl; no metrics/traces | live probe |
| Backups/DR | **missing** | — (DB-4) |

## Phase 7 ledger
- `py_compile` gate: OK (DEPLOY.md §7).
- live `/health`: ok, pid 19628, limiter=memory.
- Lane separation: containers ready, production running **BLOCKED** on VPS/domain/keys.

Phase 8 — UX/UI/A11y (browser tool unavailable → BLOCKED checks documented).