# PRODUCT-FAQ — FluxSwarm

Phase 13 artifact. Answers grounded in runtime/code evidence (not marketing copy).

## What is FluxSwarm?
An AI-agent platform: you type a product goal, and a 6-agent ECC squad
(Planner → Architect → DevOps → TDD → Reviewer → Builder) builds it live on a
kanban board. You watch the run in real time.

## Which model do I use? Do I need my own key?
BYOK-first: you supply your own AI provider key (OpenRouter-native; other
providers pluggable). FluxSwarm never touches your key unencrypted — it is
Fernet-encrypted at rest and only decrypted at launch time, and is offered as
"you pay your tokens, FluxSwarm coordinates" (GTM one-liner).

## How does billing work?
Credit-metered: 1 credit per launched project (demo refunds on launch failure).
Paid plans (starter $29/25cr, pro $99/120cr, scale $299/500cr) are processed by
Paddle as merchant of record — Paddle handles sales tax/VAT and remittance.
Upgrades grant credits `max(current, plan)`; a merchant refund downgrades you to
demo but keeps the credits you hold (RT-W5).

## Can I resell my own squad templates?
Yes — the marketplace lets you publish templates with a credit price and you earn
50% of every sale (author share `max(1, price//2)`, atomic transfer).

## Referrals?
Share your code; when a referred account reaches a paid plan you earn 25 credits
once per referred email — exactly once, race-safe (RUNTIME-verified).

## Do I get a free trial?
Demo plan starts with 3 free credits and a shared demo board; no card required.
Note: the money path (live Paddle) is currently gated OFF — paid plans return 402
until the gateway is activated at launch (RT-E1).

## What are my data rights?
CCPA/CPRA access via `GET /api/account/export` and full erasure via
`DELETE /api/account`. Audit log is append-only and excluded from erasure
(document on /privacy). UK GDPR addendum is a launch-doc pending item (Phase 15).

## Is my password safe?
Argon2id (memory-hard) with legacy-sha256 automatic upgrade; brute-force lockout
(5 fails → 429) and a global per-IP limiter; JWT 7-day tokens (alg=none/tampering
rejected — RT03/04/05/06).

## What happens if a launch fails?
Your credit is refunded atomically; error is surfaced server-side (launch UI shows
the run's state). Template purchases auto-refund their credit if the squad can't
launch (db.refund_template_purchase; RT-W5 semantics).

## Is my project private?
Yes — boards are namespaced `u{yourid}-…`; cross-user read/dispatch/scan returns
403 (RT-T1..T3). Only the four `flux-demo-*` boards are shared (team demo boards;
cost-capping is a Phase-15 repair item).

## Availability / ops
Single-node today, `/health` monitored, container healthchecks + restart policy;
backups are a pre-launch task item. See Phase 7 report.

## SEO state (Phase 13 verdict)
- No `<meta name="description">`, no OpenGraph, no canonical, no JSON-LD, no
  `robots.txt`/`sitemap.xml`, no analytics tag in `index.html` (CODE-verified).
- `GTM_LAUNCH_PLAN.md` exists (90-day plan, ICP startup teams EU-MENA, BYOK
  positioning, EN/AR one-liners).
- Task list (Phase 15, LOW): add meta/OG/canonical + robots/sitemap + JSON-LD
  before indexation; add analytics after TLS/domain.