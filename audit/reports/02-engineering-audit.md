# PHASE 2 — ENGINEERING & ARCHITECTURE AUDIT

Consolidates: `02-how-the-platform-works.md`, `03-architecture-code.md`,
`06-database-storage-multitenant.md`, `07-infrastructure-devops-reliability.md`,
`14-e2e-failure-testing.md`, `CLAIM-VERIFICATION.md`, `CERTIFICATION.md`.

## Stack (FACT, code)
FastAPI (Python 3.11) — SQLite (`WAL`, `foreign_keys=ON`, CHECK credit≥0, 4
indexes) — JWT HS256 — Argon2id — Fernet vault — subprocess Hermes agent driver
(synchronous2 threads + APScheduler worker) — WebSocket status streaming —
pydantic validation.

## Architecture
- Modular monolith: `main.py` (API+legal), `db.py`, `vault.py`, `audit.py`,
  `hermes_client.py`, `security.py`, `telegram_bot.py`, `templates/index.html`
  (SPA-ish, RTL-first, i18n AR/EN), `static/`.
- No ORM/sync framework creep; dependency set small and pinned.

## Reliability (FACT, tested)
- Launch path: 202-accepted → background worker → per-task states (queued →
  running → done/blocked) → WS broadcast; failed launches refund credits
  atomically. E2E: full register→launch→task-stream→workspace harness passed
  (ENDPOINT/LIVE, phase14 log).
- Concurrency: Webhook replay window 300s + event-id dedup; exactly-once reward;
  atomic single-writer purchase — all under race tests.
- Failure matrix: 14 rows covered (provider offline, key revoked, TLS down,
  agent crash, malformed board, mid-purchase crash, kill-switch…) → see
  `FAILURE-MATRIX.md` (halted/retried/refunded/queued).
- Observability: audit.jsonl, structured worker logs, kill-switch env.

## Multitenancy (FACT, tested)
- Isolation by `u{id}-` namespacing + server-side guards; cross-user access →
  403 (RT-T1..T4); marketplace single-writer atoms; WS token-gated.

## Infra/DevOps
- CI: GitHub Actions (`ci.yml`, master) — unit, security-scan, workflow-triggered.
- Deploy: `DEPLOY.md` — one VPS, systemd/uvicorn, TLS via certbot, WAL-backed
  SQLite snapshots (RPO ~momentary write) — **not yet executed (BLOCKED: no host)**.
- No Kubernetes/containers on the critical path (correct for scale 0→10³).

## Known engineering debt
| ID | Severity | Item |
|----|----------|------|
| ECON-2 | HIGH | launch compute metering unmeasured → cap + metric before scale |
| ARCH-1 | MED | Hermes stderr in 500 detail (debuggability) |
| DB-3 | MED | Hermes workspace artifacts not auto-deleted on account erase |
| INFRA-1 | MED | single-node SPOF; no automated restore drill |
| DEPS-1 | MED | no `pip-audit` in CI (pre-launch) |

## Verdict
Engineering posture: **GO** for a bounded paid launch on one node (crash-safe,
tenant-audited, failure-tested), with ECON-2 metering and DEPS-1 scan as the two
hard pre-launch engineering gates.