# 12 — UK Launch Readiness

Phase: 12 / 19 — UK-specific gaps versus the current (US-oriented) posture.

## Items specific to the UK market
| Item | Status | Notes |
|------|--------|-------|
| VAT on digital services | **Handled** — Paddle MoR collects UK VAT at point of sale | payments.py |
| UK GDPR / Data Protection Act 2018 | **GAP (LEG-1)** — policy is CCPA-only; needs UK GDPR addendum (lawful basis, rights beyond access/erase, retention period, subprocessors, DP channel) | Phase 10 |
| Data transfer (UK→third country) | GAP — data stored in North America; add UK appropriate-safeguards clause + representative (Art 27 UK GDPR) given operator is in Algeria (non-UK, non-EEA) | Phase 10 |
| UK consumer cancellation (14-day cooling-off, digital content exemption) | GAP — refund policy not in app terms; Paddle T&Cs govern, but app should disclose | LEG-2 |
| EAA / WCAG accessibility (UK public bodies only; voluntary for private) | PARTIAL — a11y gaps CODE-verified (Phase 8); treat as quality, not statutory | 08 |
| Distance-selling / Online Safety Act (service-provider duties) | out of scope for a B2C tool at this scale; note for counsel | — |
| Money path live (Paddle GBP checkout) | **BLOCKED** (same external blockers as US) | 11 |

## Verdict
UK is a **delayed launch target**: same money/domain blockers as the US, plus a
UK-GDPR addendum and a UK Article-27 representative decision before serving UK
customers. Recommend: launch US first with the UK-specific docs drafted
(Phase 15 wording task), then flip the region when live keys/domain + legal docs
are ready. **CONDITIONAL GO — pending UK GDPR addendum + external blockers.**

Phase 13 — Marketing / SEO.