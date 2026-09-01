# PHASE — OPERATING COST PROJECTION & BREAK-EVEN

FACT = code/config-verified. **ASSUMPTION** = market estimate, unmeasured.
This projection uses a 1-node Hermes-compute architecture (the launch design).

## Cost model (monthly)
| Item | Fixed/Variable | A – 100 users | B – 1,000 users | C – 10,000 users |
|------|----------------|---------------|-----------------|------------------|
| VPS nodes (US) | fixed | $40 (1×) | $150 (3×) | $1,200 (multi + queue) |
| Backup + storage | fixed | $10 | $40 | $200 |
| Domain + copilot ops | fixed | $5 | $15 | $60 |
| Monitoring/support | fixed | $0 (in bare) | $50 | $400 |
| Compute per launch (variable) | var | $150 | $600 | $4,500 |
| Paddle MoR + payouts | var | ~$120 (5%+fee) | ~$600 | ~$4,500 |
| **Total monthly cost** | | **~$325** | **~$1,455** | **~$10,860** |

Assumptions: paid-active share 40% of 100 / 20% of 1,000 / 15% of 10,000;
blended $60 paid-user/month; ~20 launches per paid user/month; compute
**ASSUMPTION $0.15/launch** (1 credit), single-node at A, multi-node B/C;
Paddle fee ASSUMPTION 5%+flat.

## Revenue & margin
| | A | B | C |
|--|---|---|---|
| Paying users (ASSUMPTION mix) | 40 | 200 | 1,500 |
| Monthly revenue (FACT price × assumed pack cadence) | ~$2,400 | ~$12,000 | ~$90,000 |
| Total cost | ~$325 | ~$1,455 | ~$10,860 |
| **Net /month** | **~$2,075** | **~$10,545** | **~$79,140** |
| Net margin | ~86% | ~88% | ~88% |

The 85%+ margin is structural: BYOK means the platform never pays model tokens;
the binding cost is compute time (ECON-2 to meter and cap).

## Break-even (FACT margin × ASSUMPTION cost)
- Per-launch revenue by plan: Starter $1.16 / Pro $0.83 / Scale $0.60 (FACT);
  per-launch cost $0.15 (ASSUMPTION) → contribution $0.45–1.01.
- Fixed base ≈ $55/mo (1 node). Break-even ≈ **2–4 active paying users**
  (Starter-equivalent $29 packs) → ~$60–90/mo revenue.
- With referral reward (25 cr ≈ $1.25–6.25 cost, ASSUMPTION) and refund risk
  (~5%), effective break-even ≈ **3–5 paying users**.
- Conclusion: economically launchable from the first paid customer; the scarcity
  is **launch-compute headroom**, not money → gate growth on ECON-2 metering.

## Confidence
All plan prices/credit semantics are FACT. Compute costs per launch are the one
ASSUMPTION that can move the model 5× in either direction → **ECON-2 is a
pre-scale hard gate** (measure wall-time/agents, set cap, then trust the P&L).