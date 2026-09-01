# 02 — How the Platform Works

Phase: 2 · Evidence mode: CODE VERIFIED (+ RUNTIME where noted) · Date: 2026-08-30

## Product

- **What it is**: FluxSwarm — an agentic SaaS builder. A user describes a build goal; the platform orchestrates a "swarm" of AI agents (ECC devops squad inside Hermes) that plan, architect, code (TDD), review, and assemble a project. Projects are presented as live kanban boards.
- **Target user**: small teams / independent developers who want a full-stack MVP scaffolded by agents without paying for tokens themselves (BYOK) or with a free hosted model.
- **Problem solved**: turn a short goal into a coded, reviewed project with a visible, inspectable pipeline.
- **What it does NOT do** (verified absent): no code review of produced code beyond basic static scan; no sandboxed execution of produced projects; no email/verification/password reset; no team/org/roles; no idea/log-ins outside goals; no content generation other than the swarm pipeline.
- **Money model**: months of SaaS from plans Demo(0)/Starter($29)/Pro($99)/Scale($299) (`db.PLANS`). Credits gate launches (1/project). BYOK lets users pay their own model tokens (`vault` + `hermes_client`). Marketplace sells squad templates for credits (author earns 50%, `db.buy_template`).

## User flow (steps present)

```
Landing (/)  →  [auth modal: Login/Register]  →  Dashboard (same SPA)
  →  /api/projects list  →  launch project (goal, pays 1 credit, creates board)
  →  board shows tasks via WS live updates  →  demo launch (no auth, IP-capped)
  →  BYOK key save  →  Referral link  →  Telegram link panel
  →  Squad marketplace (publish/buy templates)  →  Security score per board
  →  account export (GET /api/account/export)  →  account delete (DELETE /api/account)
```
All integrated in `templates/index.html` SPA calling the JSON API. No multi-page onboarding.

## Technical flow (per critical action)

### Register
```
POST /api/auth/register  (main.py:358)
  client_ip via XFF only from trusted proxy  (main.py:326-343)
  rate: hit_ip + register_allowed  (10/h/IP)  (main.py:361-365)
  validate email/min-8 password  (main.py:366-372)
  db.create_user: Argon2id hash, ref_code, referral row  (db.py:180)
  token minted JWT HS256 7d  (auth.py:43)  → 200 {token,user}
```

### Login
```
POST /api/auth/login  (main.py:385)
  hit_ip (20/min/IP) + login_allowed (5 fails/15min per ip+email)  (main.py:389-394)
  db.authenticate verifies Argon2 (sha256 legacy auto-upgrade)  (db.py:206)
  failure → record_login_failure → 401 (generic msg, no user enum)  (main.py:396-399)
```

### Create project (protected)
```
POST /api/projects  (main.py:424)
  goal validation + length  (main.py:428-432)
  db.deduct_credit (atomic BEGIN IMMEDIATE)  (db.py:267)
  slug = u{uid}-{epoch}-{rand16}  (main.py:346)
  hc.ensure_board + hc.launch_swarm (subprocess hermes, timeout 300)  (main.py:437-447)
  failure → refund credit inline  (main.py:441-447)
  db.add_project → _fire_dispatch(daemon thread, plan capping parallel)  (main.py:448-451)
```

### Board tasks (protected)
```
GET /api/projects/{slug}/tasks  (main.py:459)
  ownership = slug.startswith(f"u{uid}-") OR flux-demo-  (main.py:462)
  else 403.   hc.list_tasks → kanban JSON.
```

### Paid subscription → Paddle checkout
```
POST /api/subscribe/{plan}  (main.py:542)
  paid plan & gate closed → 402  (main.py:555) ; gate open → gateway.create_checkout
  → checkout_url = /checkout?_ptxn=txn (Paddle.js overlay)  (payments.py:277-278)
  Credits granted ONLY from verified webhook (main.py:707; payments.py:287)
```

### Paddle webhook (no auth, signature-verified)
```
POST /api/payments/webhook  (main.py:587)
  gateway.handle_webhook: verify_paddle_signature (v1 base64-HMAC or v2 ts;h1 + 300s window)  (payments.py:151-189)
  idempotent via payment_events UNIQUE(event_id)  (main.py:719-726, db.py:360)
  success → db.upgrade_plan + referral reward once  (main.py:731-741)
  refund → downgrade to demo (credits kept)  (main.py:742-750)
```

### Telegram link
```
GET /api/telegram/link → 6-hex code (TTL 10min)  (main.py:1042; db.py:413)
bot: /link <code> → consume → binds chat↔uid (telegram_bot.py:59; db.py:447)
bot goal: linked → board u{uid}-tg-…, +1 credit, refund on failure; anonymous → tg-… free  (telegram_bot.py:80-167)
```

## Agent flow

```
User goal → launch_swarm → hermes kanban swarm:
 workers: planner/architect/devops/tdd (parallel) → verifier (reviewer) → synth (build-fixer)
 (hermes_client.SQUAD/VERIFIER/SYNTHESIZER)
 runtime pinned per task (free model `hy3-free` or user's BYOK provider)  (hermes_client._pin_runtime)
 dispatcher passes until terminal state or 600 s  (hermes_client.dispatch)
 security scan: static leak/injection regex over workspace + optional ECC skill  (security.scan_project)
```

## Data flow

| Data | Where | Provider | Retention | Deletion |
|------|-------|----------|-----------|----------|
| email/name/password-hash | `users` (SQLite) | — | until delete | `delete_user` removes row |
| projects/goals | `projects` + kanban boards (Hermes home) | Hermes | until delete | SQL row removed; **board files NOT removed** (Phase 6 check) |
| BYOK keys | `byok.json` Fernet-encrypted | — | until delete/overwrite | `vault.delete_user_key` |
| payment events | `payment_events` | Paddle MoR | permanent (ledger) | excluded from erasure (documented) |
| audit jsonl | `audit.jsonl` | — | permanent | excluded (documented) |
| referral rows | `referrals` | — | permanent | `delete_user` deletes rows for own code |
| telegram bindings | `telegram_links` | Telegram | until unlink/delete | deleted on account delete |

## Critical-path observations (deep-dive phases)

1. Ownership is prefix-derived (`u{uid}-`), never from a stored `user_id` the client sends — but `flux-demo-*` is globally shared and unpriced (Phase 5 attacks).
2. Checklist left in index.html line `t("ref_reward")` etc — marketing strings in `db.PLANS.desc` (Arabic) render correctly over HTTP when decoded as UTF-8 (verified via python; PS console mojibake was a display artifact). *Runtime:* `/api/plans` decodes cleanly.
3. Refund-on-launch-failure in `api_create_project` uses a manual inline UPDATE instead of `db.add_credit` (main.py:441-447) — duplicates logic (Phase 3).
4. Anonymous Telegram swarm launch has NO quota/rate-limit/cost ceiling (Phase 5/9).