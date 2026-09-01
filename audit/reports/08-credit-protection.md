# PHASE 14 — CUSTOMER CREDIT PROTECTION

Goal: prove the customer never loses prepaid credits on failure, and the upgrade
path always credits correctly (anti-scam/anti-loss).

## Mechanisms (code + tests VERIFIED)
| Protection | Trigger | Behavior | Evidence |
|------------|---------|----------|----------|
| Launch-failure auto-refund | swarm launch raises before any consumed work | credits returned to account automatically | launch dispatch (main) + leak-tested; red-team RT-W5 |
| Refund downgrade keeps balance | Paddle `payment.refunded` webhook | plan → Demo, current credit balance **kept** | webhook handler, test `test_fix_phase15`-era billing logic |
| Upgrade credit floor | plan change to higher tier | credits = `max(current, allowance)` — never reduced/net-negative | db update path + tests (upgrade keeps surplus) |
| Migrated-down credit floor | downgrade after using credits | `max(current, min_allowance_demo)` floors | tested; no negative balance possible at DB CHECK |
| Atomicity | mixed payment+tier writes | single-row CAS-style updates; errors roll back | code: update cofl > 0 guard |
| No silent loss | crash mid-purchase | kill-switch/daily cap never touch owned credits; webhook dedup prevents double-charge reversal | FIX-1 + webhook replay guard |

## Payment-edge integrity (VERIFIED)
- Signature failure → no grant; replay → 409 dedupe; stale (>300s) → rejected; v1+v2
  formats both handled (RT-W1..W5).
- Self-buy blocked (RT-T6a); template agent allowlist prevents unknown-skills
  purchases (RT-P2).
- Credit inventory is not double-counted on upgrade (max() not +).

## Independent check vs policy claims
Every credit claim on `/refund` and `/terms` is implemented and tested — no
promise exceeding behavior (matches 06b evidence). Free-tier users cannot go
negative; demo cap limits trial abuse without touching real balances.

## Residual
- Paddle-side refund amount vs app balance reconciliation is manual → automate
  before scale (P3); UK/EU distance-selling right decision flagged for Phase 29.