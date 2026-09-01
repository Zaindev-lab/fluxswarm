# PHASE 16–18 — UNIT ECONOMICS, PRICING REFINEMENT, REFERRAL ECONOMICS

FACTS = code/config-verified · ASSUMPTION = unmeasured estimate (labeled).
FIXED unit: **1 credit = 1 swarm launch** (`main.py:455`, `db.py:303`); credits
never expire; launch-failure refunds the credit (`telegram_bot.py:108`).

## 16 — Unit economics (per launch)
| Item | Value | Type |
|------|-------|------|
| Platform revenue per credit | $1.16 (Starter) / $0.83 (Pro) / $0.60 (Scale) | FACT (price÷credits) |
| Platform marginal cost per launch | unmeasured → **ASSUMPTION $0.05–0.25** (single-node CPU/RAM for Hermes session; no cloud per-seat cost at current 1-node model) | ASSUMPTION, ECON-2 open |
| Model token cost | **$0 for platform** (BYOK — user pays provider directly) | FACT (BYOK architecture) |
| Paddle MoR fee | ~5% + flat → on $29 ≈ $1.5–1.9 | ASSUMPTION (verify contract) |
| Gross margin/launch | Starter ~$0.9–1.1; Pro ~$0.6–0.75; Scale ~$0.35–0.55 (after Paddle + compute) | derived (ASSUMPTION in cost) |
| Refund risk | refund→Demo keeps balance (webhook) — platform eats the credit + Paddle fee | FACT (behavior) |

Verdict: BYOK makes the platform nearly pure-margin on tokens; the real variable
cost is **compute minutes** → must meter (ECON-2): log launch wall-time + agent
count (`parallel` 1/2/4/6) at P30 and set a per-launch compute cap before scale.

## 17 — Pricing refinement (advisory, no blind change)
- Entry anchor: market floor $20–25 → test Starter $25 vs $29 (ASSUMPTION: -1.6c
  margin/launch; unknown demo→paid conversion → gate behind ECON-2).
- Top: Scale $299/500 at $0.60/launch is inside the $200–400 sibling band → keep.
- Missing wedge: no per-launch top-up pack. **Recommend** `1 credit = $1.50`,
  `10-credit pack = $12` (Pro parity) as a low-CAC purchase path (ASSUMPTION:
  reduces upgrade friction; do-not-ship-until Paddle live). Flag: pack must
  reconcile with the `max()` upgrade floor logic.
- Free tier: 3 credits total intentionally tiny vs Lovable 30/mo → keeps churn
  cost ~0; revisit at 500-signup mark (ECON-2).

## 18 — Referral economics
- Mechanism: referrer earns **25 credits when referred user pays (≥ Starter)**,
  exactly-once per email (`db.py:47`, `reward_referrer_once` race-safe).
- Cost to platform: 25 credits × marginal cost per launch
  (ASSUMPTION $0.05–0.25) = **$1.25–6.25** + share of credit slippage.
- Revenue per referred user (LTV₀): $29–299 minus Paddle. Reward = ≤ ~20% of LTV₀
  at Starter ($1.25–6.25 of $27+ margin) → **margin-positive at any plan**.
- Double-count risk: upgrade to a higher plan rewards only once (rewarded=1);
  self-referral impossible (distinct paid account); no mass-registration abuse
  (free reward requires a paying user).
- Flag: reward is metered in *launches*, not cash — a power referrer can spend
  credits the platform pays tokens for-free (BYOK) → cost ceiling is compute only.

## Consolidated
P16–18 COMPLETE. All monetary figures that are not code-verified are labeled
ASSUMPTION; ECON-2 (launch metering) is the one input that turns the economy
from assumption-based to fact-based before public scaling.