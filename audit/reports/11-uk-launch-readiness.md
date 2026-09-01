# PHASE — UK LAUNCH READINESS (final)

UK gaps previously open are now closed (docs live), leaving external blockers
plus two UK-decisions.

## Items specific to UK (VERIFIED)
| Item | Status | Notes |
|------|--------|-------|
| VAT on digital services | Handled — Paddle MoR collects UK VAT at POS | MoR |
| UK GDPR / DPA 2018 | **NOW PRESENT** — privacy page includes UK/EU addendum: lawful bases (contract / legitimate interests / legal obligation), rights incl. ICO complaint, transfers clause, subprocessors (Paddle + user-chosen AI providers) | /privacy-en (live 200) |
| Data transfers UK↔third country | Clause present; storage North America (launch posture). Art-27 UK representative decision (operator non-UK) → **DECISION NEEDED**, counsel | /privacy-en, 07 |
| Consumer cancellation (14-day, digital-content) | Refund page: 14-day discretionary monetary review net of consumed work; statutory rights not waived | /refund-en (live) |
| PECR cookies/tracking | No tracking cookies (code-verified); /cookies page explains JWT-localStorage only → no prior-consent banner needed | /cookies-en (live) |
| GDPR gap: referred_email erasure (D3) + Hermes artifacts (D12) | Disclosed honestly; live parity pending — **LEGAL REVIEW REQUIRED** for the erasure-gap exposure | 07 |
| EAA / WCAG | Voluntary for private-sector B2B; quality bar met (FIX-8) | 04 |
| Online Safety Act | Out of scope for this tool at this scale; note for counsel | — |
| Money path GBP | **BLOCKED** — same external blockers as US | 10 |
| Currency | UI presents USD ($) via /api/plans; for UK we may surface GBP later via Paddle price API — **DECISION: USD-first acceptable at launch** (documented) | index.html |

## Verdict
UK has gone from GAP-state to launch-ready *docs*, but keep as the **secondary
roll-out market after US** (per old Phase-12 recommendation): same external
blockers, plus UK-GDPR representative decision and the erasure/artifact legal
review before serving UK customers. **CONDITIONAL GO for US; UK = follow-on.**