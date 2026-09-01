# PHASE 19 — LOSS PREVENTION (risk → controls)

| Risk | Impact | Current protection (FACT) | Recommended |
|------|--------|---------------------------|-------------|
| Credit drain via free tier | high | Demo 3 credits total; daily cap 25 launchers; kill-switch env (FLUXSWARM_KILL_SWITCH) | keep; telemetry on demo burn |
| Launch cost exceeds credit value | MED | 1 c/launch flat; no time metering (ECON-2) | cap CPU-minutes/launch; flag burst sessions |
| Refund→re-entry double use | HIGH | refund downgrades to Demo + balance kept; webhook dedup; 300s window | strike-through: no re-upgrade within X days (POLICY, not code) — decided display-only pre-launch |
| Referral farming | MED | 25 cr reward only for ≥Starter payer; once per email; race-safe | monitor ref↔sale ratio |
| Marketplace template piracy / self-buy | LOW | self-buy blocked; atomic purchase; template agent allowlist | none |
| Provider outage blocks payout | MED | launch-failure credit refund automatic | status page + retry backoff (queued) |
| Paddle chargeback/decline | MED | no credit grant without verified signature (RT-W1) | MoR absorbs chargeback; reconcile monthly |
| Account takeover | MED | Argon2id; lockout; JWT 7d; CORS allowlist | enable 2FA (P3) + session revocation |
| Key leakage (in-process) | MED | keys only in subprocess env, cleaned after run | deploy on isolated VPS (DEPLOY.md) |
| Data-loss (SFTP/disk) | MED | WAL + snapshot guidance | automated backup+restore drill pre-launch |
| Single-tenant SPOF | MED | none beyond node | migrate to VPS (INFRA-1) |

All current protections are implemented and test-backed (see TEST-LEDGER rows).
The top three priority hardening items before launch: ECON-2 metering,
pip-audit (DEPS-1), backup/restore drill (INFRA-1).