# 09 — Billing / Pricing / Economics Audit

Phase: 9 / 19 — Evidence: `phase9-economics.txt` (runtime isolated probe),
red-team RT-E1/W2/W5/T6/RT-T6a, `/api/plans` (4 plans, UTF-8 verified).

## Pricing & units (CODE, db.PLANS)
| Plan | $/mo | Credits | Parallel | Cents→credits ratio |
|------|------|---------|----------|---------------------|
| demo | 0 | 3 | 1 | — |
| starter | 29 | 25 | 2 | 1.16 $/credit |
| pro | 99 | 120 | 4 | 0.83 $/credit |
| scale | 299 | 500 | 6 | 0.60 $/credit |

- Credit unit economics: 1 credit = 1 project launch (`deduct_credit`, refund on
  launch failure `main.py:444`). Repeated dispatches on an existing board do NOT
  re-debit — only *new* launches cost. Demo boards cost zero (see findings).

## Payment path (CODE + RUNTIME)
- Gateway select: `FLUXSWARM_PAYMENT_PROVIDER` → PaddleGateway else StubGateway;
  stub or missing creds = fail-safe 402 (`payments.py:431-451`, RT-E1).
- Checkout: Paddle Checkout API (server-side txn), redirect/success URLs from
  `FLUXSWARM_PUBLIC_BASE_URL`; the client's redirect is explicitly NOT proof of
  payment; only a signature-verified webhook grants credits (`payments.py:281-285`).
- Webhook: v1 & v2 signature schemes, 300 s replay window, constant-time compare,
  `payment_events.event_id UNIQUE` dedup (RT-W1..W5 all pass).
- Upgrade semantics: `upgrade_plan` = plan switch + bump credits to the plan's
  allowance (`credit = max(current, plan_credits)`) — never subtracts.
- Refund: downgrade to demo, **credits KEPT** (RT-W5, `credits remain 120`).
  Note: keeping credits on refund has an abuse window (buy pro → refund → keep 120
  credit value) — Paddle refunds are merchant-initiated, so it's a policy choice;
  flag for Phase 15 decision (credit clawback on refund is NOT implemented).

## Marketplace economics (RUNTIME-verified)
- Publish `squad_templates` with `price_credits`; author earns `max(1, price//2)`
  on each purchase (RT-T6); self-purchase blocked (RT-T6a).
- Purchase debits happen once, atomically (BEGIN IMMEDIATE).

## Referral economics (RUNTIME-verified now)
- `reward_referrer_once`: referrer +25 credits when a referred user reaches the
  paid gate; once-per-referred-email, concurrency-safe (rowcount claim).
- Probe: referrer 3→28 on first claim; second claim → False (no double-reward);
  referred→upgraded pro gives 120 credits. 

## Findings

### ECON-1 — Demo-surface economics are unbounded (HIGH, owned by FLX-DEMO-1)
Any authenticated user can dispatch ANY `flux-demo-*` board an unlimited number
of times for zero credits and no quota (RT-T8, RT-E2). Under a real gateway, the
platform pays the model costs behind each dispatch → unlimited cost per account
till an operator notices. Same class as FLX-TG-1 (unmetered anonymous Telegram).
Phase 15: per-user daily dispatch cap on demo/debot surfaces + global concurrency
throttle + admin kill-switch (bypass: none).

### ECON-2 — Long-running projects don't meter worker count (MEDIUM)
Credit is charged per launch, but post-launch dispatches (`/dispatch`) are the
tool that spawns the parallel squad workers per plan (parallel 2/4/6) — those
dispatches carry no charge after the initial debit. A starter user can dispatch
many times and amortize one credit across many worker-compute hours: ratio and
meter accuracy drift. Phase 15: decide meter model (per launch vs cpu-time);
document in pricing page.

### ECON-3 — Money path is UNVERIFIED end-to-end (BLOCKED)
No live Paddle credentials, no public domain, `FLUXSWARM_PAYMENTS` unset. The
sandbox/mock wiring exists and is signature-verified (RT-W*), but a real checkout
(price catalog, `.env` price ids, webhook registered at the live domain, CORS with
Paddle JS, redirects under HTTPS) has never completed. This is the #1 launch
blocker for a paid product. Follow-up: `backend/PADDLE_LIVE_CHECKLIST.md`.

### ECON-4 — Refund keeps full credit value (LOW, policy)
See payment path above. Decide Phase 15: keep (simple, DFSA-friendly) vs clawback.

## Verdict
Money math, dedup, gate closure and referral claims all hold (RUNTIME). The money
**path** is unusable until live keys + domain (BLOCKED). Demo/anon surfaces create
an unbounded-cost hole that must be capped before launch (ECON-1/FLX-DEMO-1).

Phase 10 — Privacy / Legal / License.