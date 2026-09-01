# 06 — Database / Storage / Multi-tenant Audit

Phase: 6 / 19 — Companion artifact: `reports/DATA-MAP.md`, evidence
`audit/evidence/phase6-schema.txt`.

## Runtime evidence (isolated temp DB via `db.init_db()`)
- `PRAGMA foreign_keys = 0` — FKs are decorative.
- `journal_mode = delete` (rollback journal, no WAL); `page_size=4096`;
  `auto_vacuum=0`.
- Tables: users, projects, referrals, squad_templates, template_purchases,
  payment_events, telegram_codes, telegram_links (+sqlite_sequence).
- Implicit unique indexes only (users.email, users.ref_code,
  payment_events.event_id, telegram_codes.code). **No explicit indexes.**

## Endorsements (strengths — CODE)
1. Money mutations are atomic via `BEGIN IMMEDIATE` + rowcount semantics
   (`db.py`: deduct_credit 267-281, buy_template 616-647,
   refund_template_purchase 650-689, reward_referrer_once 284+,
   add_credit 703-713) → no double-debit/double-refund/double-reward.
2. Referral double-credit race is closed (claim via rowcount, dedicated
   `test_db_referral_race.py`).
3. Webhook replay dedup is structural: `payment_events.event_id UNIQUE`
   (RT-W2 runtime-verified).
4. Secret split is correct: BYOK ciphertext in repo `data/byok.json`, Fernet
   master key OUT of repo at `~/.fluxswarm/fernet.key` (never beside ciphertext),
   credentials gitignored.
5. Legacy sha256+salt hashes upgrade in place on login (`db.py:167-223`) —
   forward-compatible migration without downtime.
6. CCPA/CPRA-style delete path exists and cascades the SQLite rows
   (`db.delete_user`) — RT-E3 verified (user + projects rows removed).

## Findings (verified)

### DB-1 — Foreign keys not enforced (MEDIUM)
`PRAGMA foreign_keys=0` at runtime; app relies on code discipline for
referential integrity. No orphan path found TODAY (delete_user cascades by hand),
so this is a defense-in-depth/regression-risk item, not an active leak.

### DB-2 — Missing indexes on hot FK columns (MEDIUM, scale)
Users → projects (user_id), purchases (buyer_id, template_id), payment_events
(user_id), referrals (referrer_code), telegram_links (user_id). Fine at current
scale (thousands of rows); becomes a latency problem at 10×+. Cheap fix, no risk.

### DB-3 — Right-to-erasure is PARTIAL (MEDIUM, privacy)
`delete_user` removes SQLite rows but leaves:
- Hermes boards/artifacts on disk for that user's slugs (goals/content persist);
- `audit.jsonl` event rows mentioning the email/uid (retention policy = never);
- the BYOK vault rows for that user's provider keys;
- `users.referred_by` values that still reference the deleted ref_code.
CCOA granularity is "erasure" — grep-apart → **PARTIAL**. Needs Phase 15 decision
(hard-delete vs anon tokens + documented retention limits).

### DB-4 — No WAL, no backup automation, unbounded audit log (MEDIUM, ops)
Single-writer rollback journal serializes writers; no `sqlite3 .backup` job or
export/restore runbook → single point of data loss (seed `data/`). Growth of
`audit.jsonl` unbounded (audit.py appends forever).
Recommend (Phase 15): nightly backup + retention + WAL (evaluate single-node
implications) — do NOT enable WAL blindly with the current BEGIN IMMEDIATE
single-writer pattern without load tests.

### DB-5 — Multi-node rate limiter not durable (MEDIUM, scale)
Limits live in process memory (ratelimit.py); two API workers double the budget.
Fine for the single `uvicorn` worker this repo documents; flag now for Phase 7.

## Multi-tenant verdict
Tenant isolation is app-layer and **runtime-verified** (RT-T1..T5: read/dispatch/
scan/list all 403 across users; owner passes). No shared-namespace SQL here; the
only shared surface is the intentional `flux-demo-*` pseudo-tenant (FLX-DEMO-1,
tracked HIGH from Phase 5). Schema design is per-row ownership → sound for the
target scale.

## Passed checks (evidence)
- Cross-tenant read/dispatch/scan → 403 (RT-T1..T3)
- Project list isolation (RT-T4); owner access (RT-T5)
- Credit/debit atomicity (RT-T6 + backend tests 75-pass + test_db_refund.py)
- Webhook dedup structural (RT-W2; payment_events UNIQUE)
- Delete removes owned rows (RT-E3)

## Phase 6 ledger
| Run | Result |
|-----|--------|
| isolation schema dump | captured → phase6-schema.txt |
| backend:75 / redteam:29 | both green (re-ran at Phase 5 close) |

Next: Phase 7 — Infrastructure / DevOps / Reliability.