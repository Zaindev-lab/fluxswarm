# 14 — Full E2E & Failure Testing

Phase: 14 / 19 — Evidence: `phase14-e2e.txt`, `FAILURE-MATRIX.md`, prior RT/baseline.

## E2E lifecycle — RUNTIME (isolated DB, TestClient)
Full happy-path walk from signup through deletion, all 200/expected:
register → `/api/me` (demo, 3 credits) → set BYOK key (400 on unknown provider =
allowlist working; list-keys redacts) → create project (credit 3→2; project create
costs exactly once) → tasks → dispatch → security scan → plan list → subscribe →
**402 gate (payments closed)** → publish template → **buy-own → 400 (blocked)** →
export (7 sections incl. payment_events, telegram_links) → delete account → gone.

## Failure matrix — summary (see FAILURE-MATRIX.md)
- Graceful under provider/DB/Hermes/poll failures (fail-open audit, atomic refunds,
  closed payment gate, PID lock). No unhandled exceptions observed in probes.
- MEDIUM flags for Phase 15/launch: no WAL+backups (F3/F12), memory-only limiter
  (F5), fail-open vault decrypt (F6), restart-state of subprocesses (F9), missing
  infra monitoring (F12/F14).
- UNVERIFIED-before-launch: Hermes sandbox timeout, live-keyed webhook at a public
  domain, Redis-capped limiter, backup restore drill.

## Verdict
Behavioral coverage is strong: every user-facing path in scope exercises clean
success or clean rejection. Everything left is an operations/infra test that
cannot run on this machine (no keys/hosts) — captured as a pre-launch checklist,
not a code gap.

Phase 15 — FIX-PLAN (P0/P1/HIGH first).