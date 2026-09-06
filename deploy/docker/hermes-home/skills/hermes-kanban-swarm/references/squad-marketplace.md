# Squad Marketplace — publish / buy / launch a squad as a template

Extends the basic swarm bridge into a multi-tenant *marketplace*: users publish a
squad (a named lineup of agents) as a template; other users buy it with credits
and the squad is **launched for real** from the stored template. This is the
feature that turns a swarm-SaaS from a "credit ledger" into an actual product
(UGC — user-generated squad templates). Verified working in FluxSwarm v0.8.

## Data model (SQLite)
```
squad_templates(id, author_id, name, description, agents TEXT(json list of DISPLAY names), price_credits, created_at)
template_purchases(id, template_id, buyer_id, created_at)
```
`agents` is a JSON array of **display names** (e.g. `["Planner","Architect","DevOps","TDD","Reviewer","Builder"]`),
NOT internal profile names. Store + read with `json.dumps` / `json.loads` (see Pitfalls).

## AGENT_REGISTRY — map display name -> (profile, skills, role)
The single source of truth that lets a friendly template name resolve to a real
Hermes profile + real ECC skills + its swarm role:
```python
AGENT_REGISTRY = {
    "Planner":   ("ecc-planner",   "plan-orchestrate", "worker"),
    "Architect": ("ecc-architect", "api-design,fastapi-patterns", "worker"),
    "DevOps":    ("ecc-devops",    "docker-patterns,deployment-patterns", "worker"),
    "TDD":       ("ecc-tdd",       "tdd-workflow", "worker"),
    "Reviewer":  ("ecc-reviewer",  "agent-self-evaluation,verification-loop", "verifier"),
    "Builder":   ("ecc-build-fixer", "orch-build-mvp", "synthesizer"),
}
```
Role decides where the agent goes in the swarm graph: `worker` -> `--worker`,
`verifier` -> `--verifier`, `synthesizer` -> `--synthesizer`.

## launch_from_template(board, goal, display_names, provider_keys)
```
workers, verifier, synthesizer = [], VERIFIER, SYNTHESIZER
for name in display_names:
    prof, skills, role = AGENT_REGISTRY[name.strip()]
    if role == "worker":      workers.append((prof, name, f"{name} task", skills))
    elif role == "verifier":  verifier = (prof, name, f"{name} review")
    elif role == "synthesizer": synthesizer = (prof, name, f"{name} assemble")
if not workers: raise ValueError("template has no valid agents")
# build `hermes kanban swarm` exactly like the base launch (see SKILL.md),
# then call _pin_runtime(board, provider_keys) so every task gets a pinned model.
```
This reuses the exact verified launch + pin flow from the base bridge — the only
new step is resolving display names through `AGENT_REGISTRY` first.

## Buy flow (the money → action link)
```
ok = db.buy_template(tid, buyer_id)          # deduct price_credits; author earns 50% (floor 1)
if not ok: raise 402 "insufficient credits"
tpl = db.get_template(tid)
slug = f"u{buyer_id}-t{tid}-{int(time.time())}"
db.add_project(buyer_id, slug, tpl["name"], goal)
hc.ensure_board(slug)
hc.launch_from_template(slug, goal, tpl["agents"], _user_provider_keys(buyer))
return {"ok": True, "slug": slug, "launched": True}
```
Key: buying must **trigger the launch**, not just debit credits. Return `launched`
+ `launch_error` so the UI can tell the user if payment succeeded but the swarm
failed to start (don't swallow it).

## Pitfalls specific to the marketplace
- **Validate agent names at PUBLISH time.** If any display name isn't a key in
  `AGENT_REGISTRY`, filter it out silently and the squad launches with zero workers
  → `ValueError("template has no valid agents")`. Reject the publish (or warn) when
  `workers` would be empty, so a broken template never reaches a buyer.
- **`json` MUST be imported at the top of the db module.** A missing top-level
  `import json` makes `json.dumps`/`json.loads` raise `NameError`, which a broad
  `except Exception` in `get_template` swallows and replaces with `[]` — so the
  template *appears* saved but reads back empty and every launch fails. (This was a
  real, silent bug: see the debugging note in SKILL.md "Silent except masks a
  missing-import NameError".) Always `import json` at module top; never rely on a
  per-function `import json` you might forget to add everywhere.
- **Use `list[str]` not a comma string** for `agents` in the API payload, and
  store it with `json.dumps`. If you later read it as a string you'll iterate
  characters, not names.
- **Don't let the buyer's launch inherit the author's provider keys.** Launch with
  `_user_provider_keys(buyer)` (the buyer's BYOK / free choice), so each run is
  billed to whoever bought it.
- **Author payout**: credit the author a fixed fraction (verified: 50%, floor 1
  credit) on a *successful* purchase only — never on a failed one, and never
  twice (guard against double-payout as you would for referrals).
