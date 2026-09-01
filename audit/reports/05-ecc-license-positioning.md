# PHASE 6 — ECC OPEN-SOURCE AUDIT & BRAND SEPARATION

## How ECC is actually used (VERIFIED, code + files)
- ECC is **not a Python dependency** (absent from `backend/requirements.txt`; no
  vendored ECC source anywhere in this repo).
- ECC = a Hermes-internal agent/skill layer, referenced as subprocess profiles:
  `ecc-planner`, `ecc-architect`, `ecc-devops`, `ecc-tdd`, `ecc-reviewer`,
  `ecc-build-fixer` and ECC skills under `skills/ecc/skills`
  (plan-orchestrate, api-design, fastapi-patterns, docker-patterns,
  deployment-patterns, tdd-workflow, agent-self-evaluation, verification-loop,
  orch-build-mvp) — `hermes_client.py:34-54`, `security.py`.
- Execution boundary: the product shells out to the external `hermes` CLI (a
  separate install at `HERMES_HOME`, outside this repo). FluxSwarm never embeds,
  copies, or redistributes ECC code.
- Display-name layer already strips the `ecc-` prefix from end-user UI
  (`hermes_client.py:9`, `list_tasks` normalization).

## Dependency vs application layer → **application layer / backend integration**
ECC is the underlying technical/backend orchestration layer the product drives;
it is not part of the customer-facing brand or the product's own logic.

## License determination
- No ECC license file/source in this repo → the applicable license lives in the
  external Hermes/ECC install (out of repo, not inspected).
- Because the product does not distribute ECC (no copy, no embedding, hosted use
  only), redistribution obligations are **not triggered by this repo**.
- Modify/unmodified: profiles invoked as-is (maintained upstream) → unmodified use.

**Status: `LEGAL REVIEW REQUIRED`** — obtain the ECC/Hermes license + attribution
requirements from the upstream source and record the advisory before public
launch. No evidence of a restriction prohibiting commercial hosted use was found.

## Brand separation — applied (CODE change, committed-tree)
Customer-facing "ECC" occurrences removed from `index.html` (6 spots: meta
description, og:description, header tagline AR/EN, AgentShield title AR/EN)
→ empty. Product now self-brands as **FluxSwarm** with a generic "AgentShield"
security report name. ECC remains only in internal code comments/docstrings as
technical attribution (kept — do not delete).

## Positioning statement (recommended, matches architecture)
> FluxSwarm is an independent AI product. It uses the ECC open-source agent and
> skill layer as an underlying technical backend (invoked via the Hermes CLI);
> all product experience, billing, security, and customer data are FluxSwarm's own.

## Files changed (this phase)
- `backend/templates/index.html` — brand strings only (no UI behavior change).

Phase 5 — US/UK positioning & premium brand rework (next).