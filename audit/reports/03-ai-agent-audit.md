# PHASE 3 — AI-AGENT & PROMPT-LAYER AUDIT

Consolidates: `04-ai-agent-skills.md`, `05-ecc-license-positioning.md`,
`LICENSE-AUDIT.md`; execution layer fact-checks in `hermes_client.py`.

## Agent topology (FACT)
- User goal → Hermes CLI subprocess (BYOK keys injected as env, never on disk).
- Supervisor assigns tasks across roles: Planner → Architect → DevOps → TDD →
  Reviewer (VERIFIER) → Builder (SYNTHESIZER, merges to workspace).
- Display names are product-owned (Planner, Architect, …) with `ecc-` prefixes
  stripped; open-source layer = Hermes skills `/skills/ecc/skills`
  (planner/architect/devops/tdd/reviewer/build-fixer profiles).
- Parallelism: 1/2/4/6 by plan (concurrency plumbing in worker loop).

## Prompt engineering / safety (FACT, repo boundary)
- Template agent allowlist (no arbitrary roles, RT-P2 blocks unknowns).
- Goal length cap 4000 (pydantic) + project name ≤120; no shell=True; args passed
  as argv list → injection-hardened at the shell boundary.
- No system-prompt markdown from users is rendered as HTML server-side
  (esc() everywhere) → prompt-content cannot script the UI.
- In-app/direct prompt injection into the agent is accepted product behavior for
  a coding agent; repo surfaces: provider keys isolated to the subprocess env
  (`cleanup_profile_keys` removes leftovers after run).

## Model/provider policy (FACT)
- Provider = user's choice (env-gated allowlist); Anthropic/Gemini/OpenAI +
  others per `PROVIDER_*` config; model pinned per provider.
- Provider cost = $0 to platform (BYOK) — checked in 09b.

## ECC identity & provenance (VERIFIED + FLAG)
- ECC Java data is an external, open-source (Hermes skill/agent) layer; no license
  file ships in this repo and the dependency resolves outside our tree.
- **LEGAL REVIEW REQUIRED** before commercial launch: if ECC/Hermes is
  GPL/AGPL/other copyleft, invoking it as a subprocess union/servant could trigger
  interop obligations; if permissive (MIT/Apache), embed the notice in
  `NOTICES` at packaging. Ownership of Hermes: out of our control → pin the
  resolved version + its license at packaging time.

## Failure & quality behavior (FACT, tested)
- Agent crash → task marked blocked, launch refund path intact
  (FAILURE-MATRIX rows 1–6), workspace read robust to partial writes (read
  flattening tested RT-W6).
- Deterministic outputs: no offline privacy leak; E2E launch streamed real tasks
  in the recorded harness (phase14 log) — agent actually built in the live test.

## Residual AI risks
| ID | Severity | Item |
|----|----------|------|
| LIC-ECC | HIGH(open) | ECC/Hermes license & upstream provenance → REQUIRED review |
| AI-2 | MED | provider-supply (user key) has no usage budget on our side beyond plan cap — cap minutes via ECON-2 |
| AI-3 | LOW | BYOK key secrets in user env on shared node → per-deploy isolation check (single-tenant VPS recommended, DEPLOY.md) |

## Verdict
Agent layer is a defensible value prop (6-skill swarm, product-named, tested
live). The single make-or-break is **ECC/Hermes licensing** — resolve before
public billing.