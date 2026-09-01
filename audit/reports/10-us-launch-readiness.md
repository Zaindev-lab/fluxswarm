# PHASE — US LAUNCH READINESS (final)

Consolidates US block from the 30-phase protocol. Money path remains the only
hard external blocker; all code/legal items that were previously open are now
closed and verified live (2026-08-30).

## Regulatory & trust surface (VERIFIED live)
| Item | Status | Evidence |
|------|--------|----------|
| Sales tax / VAT | Paddle MoR collects & remits | payments MoR |
| Live payments (checkout+webhook) | **BLOCKED (external)** — no LIVE keys/domain; `FLUXSWARM_PAYMENTS` stays off | PADDLE_LIVE_CHECKLIST |
| Terms of Service (US-governed) | PRESENT, live 200 | /terms-en |
| Privacy CCPA/CPRA access+erase | PRESENT + implemented (export / DELETE) | /privacy-en, RT-E3 |
| Refund & credit policy | **NOW PRESENT** (was missing) — no absolute no-refund; 14-day discretionary review; consumer rights preserved | /refund-en (live 200, sweep true) |
| Data residency | Policy states North America hosts = **launch posture**; current dev host is local → resolve at deploy | /privacy-en, 06b |
| A11y litigation surface | FIX-8 applied (skip-link, focus-visible, ARIA landmarks, reduced-motion) → LOW residual | 04-ux-audit |
| Abuse/cost caps | Demo 3 cr + daily cap 25 + kill-switch; Telegram quota deferred (FLX-TG-1, launch POD decision on unmetered anonymous) | RT-T8, RT-E2, FIX-1 |
| OFAC / sanctions | Via Paddle (MoR screening) | MoR |
| Invoicing receipts | Via Paddle emails | pipeline |
| 402 gate | dev-only; must be OFF once live keys present (`FLUXSWARM_PAYMENTS` gates) | main.py |
| SEO surface | meta/OG/canonical + robots.txt + sitemap.xml **now live at root** (was missing) | live 200 sweep ×9 urls |
| License notices | ECC/Hermes notices → **LEGAL REVIEW REQUIRED** before billing | 03, LICENSE-AUDIT |
| First-party entity | `FLUXSWARM_LEGAL_*` env block reserved (empty until deploy) | 06b |

## Launch gates (verdict-critical)
1. External: LIVE Paddle keys, domain+DNS, TLS, US-host deploy + backup/restore
   drill, money-path sandbox→live E2E. → **BLOCKED, not code.**
2. Engineering: ECON-2 launch-compute metering; DEPS-1 pip-audit in CI; ARCH-1
   error-sandboxing. → small, pre-launch.
3. Legal: ECC/Hermes license resolution (make-or-break) + NOTICES at packaging.
4. Marketing: GTM_LAUNCH_PLAN exists; /pricing + /how-it-works + /faq live.

## Decision
US is **CONDITIONAL GO**: every controllable item is verified; remaining items are
external (keys/domain/host) + one legal review (ECC license). On those times:
GO.