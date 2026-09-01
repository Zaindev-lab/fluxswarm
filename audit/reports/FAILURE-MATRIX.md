# FAILURE-MATRIX — FluxSwarm

Component failure behavior, per CODE inspection + RUNTIME probes (Phase 14).

| # | Failure | Observed/code behavior | Severity | Note / Phase-15 |
|---|---------|------------------------|----------|-----------------|
| F1 | Payment provider down / not configured | StubGateway → **503 "مدفوعات الخدمة معطلة مؤقتاً"**; gateway HTTP error → 502 + audit `payout.failure` (RT-E1). | LOW | graceful |
| F2 | Paddle webhook bad/expired signature | 400 rejected; no credit grant (RT-W1/W3/W4). | LOW | correct |
| F3 | DB unwritable / locked (no WAL) | sqlite3 raises → 500; single-writer BEGIN IMMEDIATE for money ops; no automatic recovery. | MEDIUM | add WAL + backup job (pre-launch); DB-4 |
| F4 | Hermes CLI missing / launch crash | Err surfaced as 500 with readable message; launch credit **refunded atomically** (`main.py:444`); template purchase auto-refund. | LOW | INFRA-2 graceful |
| F5 | Redis absent (docker-less) | limiter falls back to in-memory (INFRA-3, `limiter_backend: memory` live-verified); rate-cap resets on restart. | MEDIUM | enable Redis in prod |
| F6 | Vault key corrupted / decrypt fails | `vault.decrypt` fails **fail-open** → empty key sent upstream → launch error; no crash, silent weirdness possible. | MEDIUM | log decrypt failure + surface to user (Phase 15) |
| F7 | Audit file unwritable | `audit()` wrapped in try/except — never takes the app down (fail-open). | LOW | deliberate |
| F8 | Board poll (WS) user gone / error | error message sent, socket kept alive; 4 s poll (INFRA-4) — cheap-stop remains | LOW | ok |
| F9 | Server restart mid-dispatch | subprocess ownership/state not persisted; board paths on disk; restart state **UNVERIFIED** (needs infra doc). | MEDIUM | document + supervisor pattern |
| F10 | Second server instance | `serverlock.py` pid-lock (Phase 7) prevents double boot. | LOW–OK | verified? code only — mark code-verified |
| F11 | Telegram bot down | link/list fail on transport; existing tests only — live failure path **NOT probed** (no bot creds). | LOW | pre-launch check |
| F12 | Disk full | uncaught → 500 on writes; backup job would be first casualty. | MEDIUM | monitoring + space alerts |
| F13 | Malicious big payload (64KB pw) | accepted (FLX-AUTH-1) — DoS-ish, semi-orphan; 20/min limiter partially guards. | MEDIUM | max-length in Phase 15 |
| F14 | OOM of a squad worker | payload errors surface in board; no supervisor restart (single-node). | MEDIUM | scale-repair doc |

## Verified-in-class
- Money ops isolated: purchase/refund atomic single-writer; webhook replay-safe; gate-402 closed (RT-E1).
- Identity/tenant: cross-tenant isolation 403 (RT-T1..T3); auth tamper-proof (RT-03..06).
- Lifecycle E2E trace (phase14-e2e.txt): register→BYOK(allowlist)→2 projects(+credit dedup)→tasks→dispatch→security scan→plans→subscribe-402 gate→template→self-buy-blocked→export(7 sections)→delete→gone. All pass.

## Not-yet-verified (pre-launch checklist)
Hermes sandbox timeout enforcement, Redis real limiter, HTTP-triggered-eventing of
Paddle webhook at a public domain, DB backup/restore drill, disk-monitor.