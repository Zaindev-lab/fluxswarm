# 11 — US Launch Readiness

Phase: 11 / 19 — Assembled from Phases 1–10 evidence. A US go-live needs revenue.
Billing/domain/TLS remain the hard blockers; everything auditable is now verified.

## Readiness items
| Item | Status | Evidence |
|------|--------|----------|
| US sales-tax / VAT handling | **Handled by design** — Paddle MoR collects & remits | payments.py MoR note; terms |
| Live payments (checkout + webhook) | **BLOCKED** — no LIVE Paddle keys, no domain, `FLUXSWARM_PAYMENTS` off | PADDLE_LIVE_CHECKLIST.md; RT-E1 |
| Terms of Service (US-governed) | PRESENT | /terms-en (live 200) |
| Privacy (CCPA/CPRA access+erase) | PRESENT + implemented | /privacy-en; /api/account/export + DELETE (RT-E3) |
| Refund policy | **MISSING in app terms** (Paddle handles billing T&Cs) | LEG-2 |
| Data residency claim | BLOCKED — "North America" assumes a US host; currently localhost | Leg-1 note; DEPLOY.md |
| A11y litigation surface | **MEDIUM risk** — no ARIA/skip/focus (Phase 8) | 08 report |
| Security posture | Strong core verified (auth/isolation/webhook) | 05, CLAIM-VERIFICATION |
| Abuse/cost caps (demo/anon) | **HIGH gap** — FLX-DEMO-1, FLX-TG-1 unbounded cost | RT-T8, RT-E2 |
| OFAC/sanctions screening | Via Paddle (process payments) | MoR |
| Invoicing receipts | Via Paddle emails | pipeline design |
| Live domain+TLS (ACME) | **BLOCKED** (no VPS/domain) | 07 |
| First-party license notice | absent | LICENSE-AUDIT |
| 402 gate in production | must be OFF at launch (gate is dev-only) | main.py:154 |

## Launch decision input (technical)
- Fix-before-launch (Phase 15 → 16): FLX-DEMO-1 caps (ECON-1), FLX-TG-1 quotas,
  FLX-AUTH-1 max-password, FLX-CI-1, refund/credit-expiry terms, a11y minimum
  (skip-link + focus + ARIA landmarks), Hermes-artifact deletion or privacy re-scope.
- Blockers (external, must complete before go-live): LIVE Paddle keys, domain+DNS,
  TLS certificates, US-host deployment + backup job, money-path E2E on sandbox then live.
- Approvable-as-is after repairs: tenant isolation, auth, webhook integrity,
  marketplace math, referral rewards, rate limiting, health/ops wiring.

## Verdict
**CONDITIONAL GO — pending the external blockers.** Code-level readiness for US is
high (no Critical severity); the launch date is gated by money/domain, and two
HIGH-cost-abuse findings must be capped first. Phase 19 will fold this with
post-repair re-verification.