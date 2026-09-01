# DATA MAP — FluxSwarm

Phase 6 artifact. Every table, column, location, retention and flow. Evidence:
`audit/evidence/phase6-schema.txt` (runtime PRAGMA/schema dump on isolated DB).

## Physical locations
| Store | Path | Sensitive | Encrypted at rest |
|-------|------|-----------|-------------------|
| Users/business DB (SQLite) | `backend/data/users.db` | emails, names, Argon2id hashes, plan/credits | no (file perms only; NOT in repo) |
| BYOK provider keys | `backend/data/byok.json` | API keys (OpenRouter, etc.) | yes (Fernet, key in `~/.fluxswarm/fernet.key`) |
| Fernet master key | `~/.fluxswarm/fernet.key` | master secret | key file (0600); legacy `backend/data/.fernet_key` documented as removed |
| Audit JSONL | `backend/data/audit.jsonl` | emails, uids, ips, events | no (append-only, excludes secrets) |
| JWT secret | `backend/data/.jwt_secret` / env | token signing | file perms |
| Hermes boards/profiles | `C:/Users/DELL/AppData/Local/hermes/{profiles,boards}` | user goals, agent runs, tool outputs | no (host Hermes namespace; outside repo) |
| Telegram state | in DB tables + Hermes bot profile | chat ids | no |

## Tables (SQLite, `init_db`)

### users
| col | type | notes |
|-----|------|-------|
| id | int PK autoincrement | tenant key behind `u{id}-` slug prefix |
| email | text UNIQUE NOT NULL | PII; lower-cased |
| name | text | PII |
| pw_hash | text | Argon2id (legacy `sha256$salt` upgraded in place on login) |
| plan | text default demo | starter/pro/scale/demo |
| credits | int default 3 | money-equivalent |
| ref_code | text UNIQUE | `FLX-` + 8 hex |
| referred_by | text nullable | referrer's ref_code (dangling when referrer deleted — Phase 6 gap) |
| created_at | real | unix |

### projects
| col | notes |
|-----|-------|
| id int PK | |
| user_id | FK(users) NOT ENFORCED (PRAGMA foreign_keys=0) — app-layer check `u{id}-` prefix |
| board_slug | `u{uid}-{ts}-{token}`; `flux-demo-*` shared pseudo-projects |
| name, goal | goal drives subprocess launch (PII-ish) |
| created_at | |

### referrals
| col | notes |
|-----|-------|
| id int PK, referrer_code, referred_email | referred email PII; rewarded 0/1 claim guard |
| created_at | single reward per referred email (reward_referrer_once, BEGIN IMMEDIATE + rowcount check) |

### squad_templates | template_purchases
author_id / buyer_id FKs (not enforced), price_credits, agents (json list), created_at;
purchases record template_id, buyer_id, created_at — marketplace ledger for the
50% author split (author earn `max(1, price//2)`).

### payment_events
event_id TEXT UNIQUE (webhook replay dedup), gateway, kind, user_id nullable,
detail JSON default {} — stores Paddle event envelope (incl. raw webhook JSON).

### telegram_codes | telegram_links
Pairing codes (expires_at, used) and chat_id↔user_id link rows.

## Retention / lifecycle
- **Delete account** (`db.delete_user`): deletes rows from projects, squad_templates
  (author), template_purchases (buyer), referrals (referrer_code), payment_events,
  telegram_links, users. **Does NOT**: Hermes on-disk boards/artifacts, audit.jsonl
  rows, `users.referred_by` values that pointed at the deleted ref_code, or BYOK
  vault rows for that user. → right-to-erasure is PARTIAL (CCPA/CPRA gap).
- **Nothing auto-rotates** audit.jsonl (single file; potential growth).

## Multi-tenant model
- Tenant = user row. Namespace = board slug prefix `u{id}-`. Access control is
  app-layer: owner guard `slug.startswith(f"u{user['id']}-")`
  (`main.py:462-463,472-473`), cross-tenant ≡ 403 (RT-T1..T3 VERIFIED).
- `flux-demo-*` bypasses the guard → shared/tenantly-mixed surface (FLX-DEMO-1,
  Phase 5 VERIFIED HIGH).
- Isolation in SQLite is per-row ownership, not separate DBs → inferred by
  queries; no row-level security. Adequate at current scale; re-evaluate at
  multi-node.

## Integrity & concurrency
- Single SQLite file, `journal_mode=delete` (no WAL), single node = ONE writer.
- Credit/money mutations use `BEGIN IMMEDIATE` + rowcount-result checks
  (`deduct_credit`, `buy_template`, `refund_template_purchase`,
  `reward_referrer_once`, `add_credit`) — race-safe (backend tests cover refund
  race and referral race).
- **foreign_keys PRAGMA = 0** (verified) — FKs are documentation, not enforced.
- No `CREATE INDEX` statements; only UNIQUE/PK autoids. Missing for scale:
  projects(user_id), template_purchases(buyer_id/template_id),
  payment_events(user_id), referrals(referrer_code), telegram_links(user_id).
- No WAL → concurrent reads blocked during write txn on busy connections
  (single-user app OK; multi-api no).

## Backups / DR
- No scheduled backup or export task; single point of data loss is
  `backend/data/`. Recommend nightly `sqlite3 .backup` + off-site copy (Phase 7/15).

## Gaps raised (Phase 6)
- DB-1: FK enforcement off; relies on app-layer integrity.
- DB-2: no indexes on hot FK columns (scale).
- DB-3: right-to-erasure partial (Hermes artifacts, audit.jsonl, vault rows).
- DB-4: no WAL, no backup automation, audit.jsonl unbounded.
- DB-5: cryptographic keys split correctly (Fernet key off-repo) — strength; do not regress.