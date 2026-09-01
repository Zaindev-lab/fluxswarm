# PHASE 8 — DATA / PRIVACY / PERSONAL-INFORMATION INVENTORY

Sources: `db.py` schema + `main.py` endpoints + `vault.py` + `audit.py` +
hermes_client artifacts; deletion behavior verified in `api_account_delete`
(`main.py:963-970`), `db.delete_user` (`db.py:543`), E2E `phase14-e2e.txt`.

## Stores
| # | Store | Personal data | Purpose | Retention | Deletion on account delete | Third-party |
|---|-------|---------------|---------|-----------|------------------------------|-------------|
| D1 | SQLite `users` | email, name, Argon2id hash, credits, plan, ref_code, referred_by, created_at | auth, billing, referral | life of account | `DELETED` | — |
| D2 | SQLite `projects` | board_slug, name, goal (text user authored) | run the squad | life of account | `DELETED` | — |
| D3 | SQLite `referrals` | referrer_code, referred_email | reward attribution | indefinite (referral ledger) | referrer's rows `RETAINED`; **referred_email of a deleted user NOT erased** (gap) | — |
| D4 | SQLite `squad_templates` | author_id, name, description, agents | marketplace | life of account | `DELETED` (unless FK-blocked by other buyers — then `RETAINED` w/ anonymous author rows) | — |
| D5 | SQLite `template_purchases` | template_id, buyer_id | commerce | life of account | buyer rows `DELETED` | — |
| D6 | SQLite `payment_events` | event_id, gateway, kind, user_id, detail JSON (may include email/amount per Paddle webhook) | billing integrity/duplication | indefinite (receipt ledger, Paddle MoR keeps the canonical billing record) | `DELETED` where user_id set; NULL-user rows `RETAINED` (legal/billing) | Paddle |
| D7 | SQLite `telegram_links` | chat_id, user_id | link bot account | life of account | `DELETED` | Telegram |
| D8 | SQLite `telegram_codes` | code (random), user_id, ttl | pairing | TTL-purged | `DELETED` | Telegram |
| D9 | SQLite `demo_usage` | who(`u{id}`), day, count | abuse cap | rolling (per-key, no purge) | rows `RETAINED` (aggregate counters; low sensitivity) | — |
| D10 | `vault byok.json` (Fernet-encrypted) | user_id → provider encrypted key | BYOK runtime | life | `DELETED` (`vault.delete_user_key`) | AI provider (key used by user's chosen provider) |
| D11 | `audit.jsonl` (append-only) | uid, email, IP, event, slug, detail | security/investigation | **indefinite** (disclosed policy) | `RETAINED` (excluded) | — |
| D12 | Hermes board artifacts on disk (`HERMES_HOME/kanban/boards/u{id}-…`) | user's goal, generated code, logs | run the squad | life of project | **NOT deleted** by `/api/account` (gap) | Hermes/ECC (local) |
| D13 | Browser localStorage | `flux-lang`, auth token | UI/session | until clear (token 7-day JWT) | user-revocable | — |
| D14 | Paddle (MoR) | name, email, address, payment data | billing/tax/receipts | Paddle policy | governed by Paddle (legal retention); app asks Paddle on its own schedule | Paddle |

## Categories of personal data
- Identity: email, name, phone (legal entity block only, not user).
- Financial: plan, credits, payment events detail, Paddle transactions.
- Content: project goals, board artifacts, published templates.
- Usage/technical: IP (audit), timestamps, telegram chat ids, demo counters.
- Authentication: Argon2id hashes, JWT, BYOK provider keys (encrypted).
- **No** cookies (ver.), no analytics/trackers (ver.), no third-party ads.

## Data flows
1. User → FluxSwarm (goals, keys). 2. FluxSwarm → AI provider chosen BYOK (goal +
   keys only as provider env; no training clause — FluxSwarm does not train on
   data, code/business model; provider terms govern their use. NOT VERIFIED vs
   provider policies → advise). 3. FluxSwarm → Paddle (billing identity). 4.
   Browser ↔ FluxSwarm (HTTPS in prod; today local HTTP).
- International transfer: data stored on the single-node host (currently this
  Windows machine; privacy page claims North America — true only AFTER US hosting
  migration; flag for consistency, Phase 29).

## AI inputs / outputs
- Inputs: project goal + template description + task text (user-authored).
- Outputs: generated files stored in Hermes workspace; exposed via
  `read_workspace`; users can see project state via board. No training on data by
  the platform (business model = coordination; NOT VERIFIED at provider level).

## Gap list (feed Phase 9/10/29)
- D3/D9/D11/D12: deletion gaps — referred_email, demo_usage rows, audit trail
  (disclosed), Hermes artifacts (undisclosed → policy/impl mismatch).
- Policy claims "stored in North America" while the live host is this machine
  (until migration).