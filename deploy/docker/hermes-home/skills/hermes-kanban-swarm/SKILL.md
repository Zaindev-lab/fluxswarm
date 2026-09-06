---
name: hermes-kanban-swarm
version: 1.0.0
author: hermes-agent
license: MIT
description: "Drive Hermes kanban swarms (multi-agent squads)."
metadata:
  hermes:
    tags: [hermes, kanban, swarm, multi-agent, squad, saas, devops, orchestration]
---

# Building on Hermes Kanban Swarms

## When to use
- The user wants a platform / SaaS / web UI that **launches and monitors a team of Hermes bot-profiles** (e.g. the ECC devops squad) on demand.
- You need to **orchestrate several bot profiles in parallel + a verifier + a synthesizer** and show live progress.
- You are wrapping `hermes kanban` as a subprocess backend for an external service.

This is distinct from `hermes-bot-profiles` (which is about *creating* bot profiles). Here the profiles already exist; you are *driving* them as a squad.

## Core concept: one board = one isolated project
Every Hermes kanban **board** (`--board <slug>`) has its own DB, workspaces, and dispatcher. A swarm launched on `board A` never collides with `board B`. **Use one board per user project.** This is the isolation primitive — do not share one board across unrelated goals.

## Verified CLI structure (Hermes CLI at HERMES_HOME/bin/hermes.exe)
```bash
# 1) Board MUST exist before swarm (swarm does NOT auto-create it)
hermes kanban boards create <slug> --description "..."

# 2) Launch a squad. Repeat --worker; quote each one as a SINGLE arg:
#    "PROFILE:TITLE:SKILL,SKILL"
#    ⚠️ SKILL names MUST exist under HERMES_HOME/skills/ecc/skills. Verified-good
#    names (the older `planning`/`tdd-guide`/`code-review` DO NOT EXIST and make
#    the worker fail with "Unknown skill(s): <name>" — see Pitfalls):
hermes kanban swarm "<GOAL>" \
  --worker "ecc-planner:Plan the feature breakdown:plan-orchestrate" \
  --worker "ecc-architect:Design service architecture:api-design,fastapi-patterns" \
  --worker "ecc-devops:Set up CI/CD and containerization:docker-patterns,deployment-patterns" \
  --worker "ecc-tdd:Write the test suite:tdd-workflow" \
  --verifier "ecc-reviewer:Review code quality:agent-self-evaluation,verification-loop" \
  --synthesizer "ecc-build-fixer:Assemble and make it green:orch-build-mvp" \
  --created-by fluxswarm --json

# 3) Start the dispatcher so the workers actually spawn/run:
hermes kanban dispatch --board <slug> --max 8

# 4) Observe:
hermes kanban --board <slug> list --json
hermes kanban --board <slug> show <task_id> --json
```
The swarm command prints JSON: `{root_id, worker_ids[], verifier_id, synthesizer_id}`. The root card is set `done` immediately and acts as the shared blackboard.

## Squad topology (reference ECC devops squad)
6 bots: 4 parallel workers (`ecc-planner`, `ecc-architect`, `ecc-devops`, `ecc-tdd`) → verifier (`ecc-reviewer`) → synthesizer (`ecc-build-fixer`). All wired to the ECC skill pack via `skills.external_dirs` (see `hermes-bot-profiles`). Each bot profile lives at `HERMES_HOME/profiles/<name>/`.

## Backend bridge pattern (FastAPI + WebSocket)
Drive the CLI via `subprocess` from a Python web server. Key points (verified working):
- Set `env["HERMES_HOME"]` and pass it to every subprocess call — without it the CLI reads the wrong home.
- Use `python` (the agent venv interpreter), **not** `python3` (absent on this Windows host).
- Poll `list --json` on a timer and push deltas over a WebSocket (`/ws/{slug}`) for a live board.
- Normalize statuses: `done|running|ready→queued|todo→queued|blocked`.
- A ready-to-use bridge skeleton lives in `templates/hermes_swarm_bridge.py`.

## Productizing the swarm into a SaaS (auth, Telegram, plans)
The raw bridge drives one swarm. To ship it as a multi-tenant product add the
layer in `references/saas-productization.md`: per-user board-prefix isolation
(`u<id>-<ts>`), JWT auth with `?token=` on the WS, credit/plan gating that caps
`dispatch --max`, a referral program (`FLX-<code>` + reward on paid subscribe),
a pre-seeded demo account + `/api/demo/launch`, and a **Telegram bot** entry
point that reuses Hermes' own bot token from `HERMES_HOME/.env`.

### CRITICAL: token economics — default to BYOK, never flat-rate-with-your-keys
This is the single most common way a swarm-SaaS loses money. A swarm runs **6+
agents per launch**; each burns real LLM tokens. If YOU fund the tokens and sell
"1 credit = 1 launch", margin goes negative (one launch can cost $3–9 vs the
$1.16 you charged on Starter). **Default to BYOK (Bring Your Own Key):** the user
supplies their provider key, *they* pay for tokens, you charge only a coordination
fee → token loss becomes impossible (~93–96% margin). Full pattern (encrypted
vault + subprocess env injection + ECC AgentShield security scoring + i18n) is in
`references/byok-and-security.md`. Keep YOUR keys only for a tightly-capped Demo
(≈3 units/user). Unit of sale should be **agent-units**, not launches (a swarm ≈
6 units).

## Pitfalls (learned the hard way)
- **Workers block because (a) no model key AND/OR (b) no model pinned — fix BOTH.**
  The two verified causes of a worker going `blocked`/`running`-forever-with-no-output:
  1. **Missing model key** in the agent profile `.env` (`HERMES_HOME/profiles/<name>/.env` is empty by default). Fix: inject provider env vars (ANTHROPIC_API_KEY etc.) or the free-provider defaults (see next pitfall).
  2. **No model/provider pinned on the task.** Even with a key present, `dispatch`
     spawns the worker using the *profile's default model*, which for `ecc-*` profiles
     is a keyed model (Claude) → fails → card marked `blocked`. **The definitive fix
     (verified this session): after `swarm` launches, run `kanban set-model <task_id>
     <model> --provider <provider>` for EVERY task** (root + workers + verifier +
     synthesizer). Then dispatch. This pins the worker to a runnable model and it
     actually spawns (`spawned {'pid': ...}` in `logs/<task_id>.log`). The
     `_pin_runtime()` helper in `templates/hermes_swarm_bridge.py` does this in a loop.
  **NOTE (current deployment):** the `opencode-free` hosted provider was retired —
  `HERMES_DEFAULT_PROVIDER=opencode-free` / `HERMES_DEFAULT_MODEL=hy3-free` no longer
  exist and pinning them produces instant 401/429 failures. Do NOT write `opencode-free`
  into any profile `.env` — including `HERMES_HOME/.env`. The operator runtime env sets
  `HERMES_DEFAULT_PROVIDER` (`openrouter`) and `HERMES_DEFAULT_MODEL`
  (`z-ai/glm-5.2:free`) for the whole swarm; keep per-task pins
  (`set-model ... --provider openrouter --model z-ai/glm-5.2:free`) as the ONLY
  override mechanism.
- **Zero-cost swarms run on the OpenRouter free tier (no user key needed).** The
  runtime uses provider `openrouter`, model `z-ai/glm-5.2:free`, authenticated by the
  operator's `OPENROUTER_API_KEY` (injected for every profile). Verify a working path
  with: `hermes --profile ecc-planner -m z-ai/glm-5.2:free -z "Say PLANNER_OK" --provider openrouter`.
  Free endpoints rate-limit aggressively (~20 rpm / 50 daily): a burst of parallel
  workers can exhaust the window — workers retry and resume after it clears. The global
  default model does NOT auto-apply to spawned worker profiles — pin per-task.
- **Skill names in `--worker` MUST be real ECC skills or the worker dies instantly.**
  `hermes kanban` does NOT validate skill names at launch; the worker just logs
  `Error: Unknown skill(s): planning` and hangs in `running` with empty output.
  Verified-existing names: `plan-orchestrate`, `api-design`, `fastapi-patterns`,
  `docker-patterns`, `deployment-patterns`, `tdd-workflow`, `agent-self-evaluation`,
  `verification-loop`, `orch-build-mvp`. Names that DO NOT EXIST (never use):
  `planning`, `tdd-guide`, `code-review`. To verify a name: `test -d
  HERMES_HOME/skills/ecc/skills/<name>` (or `ls` the dir).
- **Worker failure is invisible in `list`/`show` — read the task LOG.** When a worker
  is `running` but produces nothing, inspect `HERMES_HOME/kanban/boards/<slug>/logs/<task_id>.log`
  (and `workspaces/<task_id>/`). The `show --json` `status` field is often `None`
  while the real state is in the log. The planner's deliverables land in
  `workspaces/<task_id>/BREAKDOWN.md` and a shared `swarm_deliverables/` dir.
- **Board must be created first.** `hermes kanban swarm --board X` errors with "board X does not exist" if you skip `boards create X`.
- **Quote each `--worker` fully** as one shell arg: `"profile:Title:skill,skill"`. Splitting on spaces sends the title words as separate phantom args and breaks parsing.
- **Starlette `TemplateResponse` signature changed** in recent versions: it is now `TemplateResponse(request=request, name="index.html", context={...})`, NOT `TemplateResponse("index.html", {"request":...})`. The old form raises `TypeError: unhashable type: 'dict'`.
- **`dispatch` with `--max 0` reclaims/marks ready tasks but does not spawn** — use a positive cap (e.g. 8) to actually run workers. `dispatch --dry-run` validates wiring without spawning.
- **`--board` is an option on `hermes kanban` BEFORE the subcommand**: correct form is
  `hermes kanban --board <slug> <subcommand> [args]`. Passing `--board` after the
  subcommand (e.g. `hermes kanban list --board X`) raises `unrecognized arguments:
  --board X`. In a subprocess bridge, build the cmd as `[BIN, "kanban", "--board", slug, *args]`.
- **Strip the `ecc-` prefix from agent names in any USER-facing output.** Internally the
  Hermes profiles are named `ecc-planner`, `ecc-architect`, etc., but expose them as
  `Planner`, `Architect`, `DevOps`, `TDD`, `Reviewer`, `Builder`. Keep the real profile
  name (`profile` field) for CLI calls; use a separate `display` field for the UI. In
  `list_tasks` normalization, compute `assignee_display` by dropping the leading `ecc-`
  (and only the prefix, preserving the `verifier:...` suffix form). Verified: an
  `assert not assignee_display.startswith('ecc-')` passes after this normalization.
- **Silent `except Exception` masks a missing-import `NameError`.** A broad
  `try/except Exception` around `json.loads` (or any decode) will swallow a
  `NameError: name 'json' is not defined` if the module forgot `import json` at the top
  level — the function then returns a wrong default (e.g. `[]`) with NO error, so the
  caller sees "saved fine, reads back empty" and every downstream launch fails with a
  confusing "template has no valid agents". **Always `import json` (and every other
  stdlib you decode with) at module top level**, never only inside one function. Test the
  round-trip (publish → read-back) explicitly before wiring the launch path. The
  marketplace reference (`references/squad-marketplace.md`) carries the full fix.

## Squad Marketplace — publish/buy/launch a squad template (UGC)
Beyond launching *your* fixed squad, let users publish a named lineup of agents as
a template and have a buyer's purchase **launch that squad for real**. The reusable
pattern (data model, `AGENT_REGISTRY` mapping display name → profile/skills/role,
`launch_from_template()`, and the buy→launch link) is in `references/squad-marketplace.md`.
Key idea: store templates as **display names** (`Planner`, `Architect`, …), resolve
them through an `AGENT_REGISTRY` to real Hermes profiles + ECC skills at launch time,
and make the purchase *trigger* `launch_from_template()` (not just debit credits).

## Verify
- `hermes kanban --board <slug> list` shows: 1 root `done`, 4 workers `running`/`ready`, verifier + synthesizer `todo`/`queued`.
- For a web UI: open `/`, confirm `/api/squad` returns the topology, POST `/api/projects`, then watch `/ws/{slug}` emit a `snapshot` then periodic `update` frames.

## Related
- `hermes-bot-profiles` — creating the bot profiles this squad is built from.
- `references/debugging-swarm.md` — **how to debug a stuck/blocked swarm**: read `logs/<task_id>.log`, pin a model per task with `set-model`, the OpenRouter free tier path (`openrouter` / `z-ai/glm-5.2:free`), and verified ECC skill names. Read this the moment a worker is `blocked` or produces no output.
- `references/verified-commands.md` — exact command transcript that succeeded this session.
- `references/saas-productization.md` — auth/board isolation, plans, referrals, demo mode, Telegram bot entry point.
- `references/squad-marketplace.md` — **publish/buy/launch a squad as a UGC template**: data model, `AGENT_REGISTRY` (display→profile/skills/role), `launch_from_template()`, and the buy→launch link. Read this to productize the swarm into a marketplace.
- `references/byok-and-security.md` — **BYOK token economics (loss-proof pricing), encrypted vault + subprocess env injection, and ECC AgentShield security scoring.** Read this before pricing or taking the product live.
- `templates/hermes_swarm_bridge.py` — drop-in Python bridge (hermes_client.py) for an external driver.
