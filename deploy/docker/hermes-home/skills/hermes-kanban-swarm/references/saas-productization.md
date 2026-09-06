# FluxSwarm SaaS productization layer (verified patterns)

Beyond the raw CLI bridge, turning a swarm into a multi-tenant SaaS needs:
auth, per-user board isolation, credit/plan gating, referrals, a demo mode,
and a chat entry point. All verified working on the FluxSwarm build.

## Per-user isolation
- Prefix every board slug with the user id: `slug = f"u{user_id}-{int(time.time())}"`.
- Guard every project/WS endpoint: reject if `slug` does not start with
  `f"u{uid}-"` (and allow `flux-demo-*` for the demo user).
- This keeps the kanban `--board` isolation clean — each user's swarms never
  collide, and one dispatcher per board still works.

## Auth + credits (no external deps)
- Store users in SQLite. Hash passwords with `hashlib.sha256(salt$password)`,
  store as `"salt$hash"` (no passlib/bcrypt needed on this host).
- JWT via `PyJWT` (HS256, 7-day exp). Put token in `Authorization: Bearer`
  header AND accept `?token=` on the WebSocket (browsers can't set WS headers).
- Each project launch costs 1 credit: `deduct_credit(uid)`; refund on failure.
- Plans = dict of `{demo, starter, pro, scale}` with `price/credits/parallel`.
  `parallel` caps `dispatch --max`.

## Referral program
- Each user gets `ref_code = "FLX-" + token`. Link: `/?ref=<code>`.
- On register with a `ref`, record `referred_by`. When that user subscribes to a
  paid plan, `reward_referrer(code, 25)` grants the referrer 25 credits.

## Demo mode
- Pre-seed a `demo@fluxswarm.ai` / `demo1234` account (credits=3).
- `/api/demo/launch` creates a `flux-demo-<ts>` board and launches a fixed
  sample swarm so visitors try instantly without registering.

## Telegram bot entry point
`python-telegram-bot==22.x`. Reuse Hermes' own bot token (already in
`HERMES_HOME/.env`) by loading it before importing the bot:

```python
def _load_hermes_env():
    p = os.path.join("C:/Users/DELL/AppData/Local/hermes", ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_hermes_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
```

- `Application.builder().token(TOKEN).build()`, register `CommandHandler("start")`
  and a `MessageHandler(filters.TEXT & ~filters.COMMAND, handle_goal)`.
- In `handle_goal`: `slug = f"tg-{chat_id}-{int(time.time())}"`,
  `hc.ensure_board(slug)`, `hc.launch_swarm(slug, goal)`, `hc.dispatch(slug, 8)`,
  then poll `hc.list_tasks(slug)` every ~10s and `send_message` on completion.
- Verified: bot connected as `aiforsaashermes_bot` and polled live.

## Pitfall: worker auto-block when profile lacks keys
Observed: `hermes kanban dispatch --board <slug>` reported
`Crashed: 4 ... Auto-blocked: 4` and `ecc-planner` went `blocked`, while
`ecc-architect`/`ecc-devops`/`ecc-tdd` spawned and ran `running`.
Cause: a bot profile with no API key / model configured blocks instead of
running. Fix: run `<bot> setup` (or ensure the profile inherits API keys from
the shell env / parent profile) before dispatching. Other workers still run, so
a swarm can partially progress.
