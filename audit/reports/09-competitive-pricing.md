# PHASE 15 — COMPETITIVE PRICING RESEARCH

All competitor figures verified against vendor pricing pages / dated reports
2026-08 (search snapshots); re-check before launch. FluxSwarm facts from live
config: 1 credit per swarm launch (`main.py:455`, `db.py:303`), never expires.

## Category A — app/squad builders (closest substitutes)
| Tool | Free | Entry | Mid | Top | Model |
|------|------|-------|-----|-----|-------|
| Lovable | 5 credits/day (≤30/mo) | Pro $25/mo (100 cr, $0.25/cr, ≤800cr) | Business $50 | to $400/mo | credit packs, no per-seat |
| Bolt.new | 1M tok/mo | Pro ~$20–25 (10M tok) | Teams $30/user | — | token-based |
| v0 (Vercel) | 200 cr/mo | Premium $20 (5K cr) | — | — | metered credits |
| Replit Core | 1–3 apps | $25 ($25 Agent cr) | — | Scale $60 | agent credits + hosting |
| Campfire | free | $20 class | — | — | team-bound sessions |
| **FluxSwarm** | **Demo $0 (3 cr)** | **Starter $29 (25 cr)** | **Pro $99 (120 cr)** | **Scale $299 (500 cr)** | BYOK + orchestration fee |

## Category B — agent CLIs/IDEs (habit/alternative, some BYOK)
| Tool | Free | Entry | Heavy |
|------|------|-------|-------|
| Cursor | limited | Pro $20 | Pro+ $60 → Ultra $200 |
| Windsurf | generous | Pro $15–20 | Max $200 |
| Claude Code | no | via Claude Pro $20 | Max $100 → $200 (+ API) |
| GitHub Copilot | yes | Pro $10 | Business $19–39 |
| OpenAI Codex | via ChatGPT | $20 | $100 → $200 |
| Cline / Roo | BYOK | API only, 0 fee | — |
| OpenCode | BYOK | free core (open source) | — |
- Raw API floors (Aug 2026): Claude Sonnet 5 $2/$10 MTok (permanent), Haiku $1/$5,
  Opus $5/$25; Gemini CLI free tier exists.

## Position assessment
1. BYOK differentiator is real: competitors bake a markup into credits/tokens;
   FluxSwarm charges a **flat orchestration fee** and passes tokens at the user's
   own provider rate — cheaper for power users (facts above; the "effective per
   requested run" math below).
2. Effective price per launch: Demo $0 (3 mc), Starter $1.16/mc, Pro $0.83/mc,
   Scale $0.60/mc — vs app-builders' $2–20 per generated app (Lovable/Bolt/v0
   estimates) → FluxSwarm is *at/under* the low end before user's token cost.
3. Entry anchor: the market floor is $20–25 (Lovable, Bolt, v0, Cursor, Claude).
   Our $29 sits slightly above; **recommend testing a $25 Starter anchor**
   (ASSUMPTION label: preserves margin of ~$1.16 vs $1.00 per mc; demo→starter
   conversion is unmeasured).
4. Top tier: $299 vs $200–$400 shares with 6× parallel cap and 500 mc → justified.
5. Freemium trap: Lovable free (5 cr/day) vs our Demo 3 mc total (FIX-1 daily cap
   25 launchers, kill-switch) → we are intentionally restrictive; revisit if
   activation data demands (ECON-2 ref; open).

## Adjacency risk (open flags)
- No hosted runtime (our delivery = source repo on disk) vs Lovable/Bolt one-click
  deploy → the strongest consumer expectation. Launch POD: `drive-to-GitHub` guide
  + template decks to close the gap (see 20-how-it-works / product FAQ).
- Devin/Campfire class (full autonomy agents) overlap our "squad does the work".

## Verdict
Ladder is defensible and under-indexed on the low end. Recommended test:
`Starter $25–29`, ad-hoc top-up packs at Pro credit price, and a "0 owed to
model" BYOK explainer as the wedge (assumes conversion tracking lands — P18).