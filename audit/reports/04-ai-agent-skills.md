# 04 — AI / Agent / Skills Audit

Phase: 4 · Evidence: CODE (hermes_client.py), Hermes install inventory (profiles/skills dirs), runtime /health · Date: 2026-08-30

## What the platform runs (squad = 6 agents + optional per-template)

Inventory from `hermes_client.py:36-54` + the real profiles present under `C:/Users/DELL/AppData/Local/hermes/profiles/`:

| Agent | Display | Role | Skills (referenced) | Cost surface |
|-------|---------|------|---------------------|--------------|
| `ecc-planner` | Planner | plan feature breakdown | `plan-orchestrate` | model tokens + steps |
| `ecc-architect` | Architect | system architecture | `api-design, fastapi-patterns` | model tokens |
| `ecc-devops` | DevOps | CI/CD, containers | `docker-patterns, deployment-patterns` | model tokens |
| `ecc-tdd` | TDD | test suite | `tdd-workflow` | model tokens |
| `ecc-reviewer` | Reviewer | verify quality | `agent-self-evaluation, verification-loop` | model tokens |
| `ecc-build-fixer` | Builder | assemble + green build | `orch-build-mvp` | model tokens |
| (`ecc-security`) | — (skill role) | AgentShield scan | `security-scan, security-review` | optional LLM scan |

All 7 profiles exist on disk (verified via directory listing). Marketplace templates are subsets of this registry (`AGENT_REGISTRY`, hermes_client.py:47-54).

Skills referenced by the squad are present in `skills/ecc/skills/` (spot-verified: `plan-orchestrate`, `api-design`, `fastapi-patterns`, `docker-patterns`, `deployment-patterns`, `tdd-workflow`, `agent-self-evaluation`, `verification-loop`, `orch-build-mvp`, `security-scan`, `security-review` all listed).

## Agent permission matrix (platform boundary)

The OS-level tool powers (bash/web/file edit) are NOT granted by FluxSwarm code — they are inherited from each Hermes `profile.yaml`/`config.yaml` and Hermes' own permission engine, which lives **outside this repo** (external system). At the *platform* boundary:

| Surface | Grant | Notes |
|---------|-------|-------|
| read/edit files | — | Agents operate inside Hermes sandboxes/workspaces under Hermes rules; not auditable from this repo (BLOCKED) |
| bash | — | same (Hermes-controlled) |
| network | provider API outbound | `hermes_client` injects provider keys as subprocess env only (comp: `PROFILES` env, `_run` hermes_client.py:147-158) |
| user secrets reach agents | limited | BYOK keys injected per-launch into subprocess env; **never written to disk** (cleanup_profile_keys removes legacy plaintext, hermes_client.py:108-137) |
| DB/other users | DENY | API ownership guards + per-user boards |
| demo boards | PARTIAL | any logged-in user can read/dispatch `flux-demo-*` (demo only) — PHASE 5 proof |
| Telegram | PARTIAL | anonymous mode = anyone can launch (no account/credit/limit) — PHASE 5 proof |

## Model routing & cost control

- `_resolve_runtime` (hermes_client.py:74-89): user BYOK provider wins (Anthropic/OpenAI/Gemini/Kimi), else `hy3-free` (opencode-free) default. **Platform's own token spend** occurs only on free-hosted runs.
- Cost ceilings per launch: `max_spawn` = plan parallel (demo 1 … scale 6) at `main.py:215,475,1211`; Telegram caps at min(parallel,8) for linked users.
- **No ceilings that the audit requires** (per protocol §11):
  - No per-user daily/monthly launch quota (only credits; a Scale user can burn 500 credits' worth).
  - No per-launch token/cost budget (`--max` only caps concurrency, not total cost/time).
  - Anonymous Telegram path: no quota at all (FLX-TG-1).
  - `/api/projects/{slug}/dispatch` on shared `flux-demo-*` boards is unpriced and unrate-limited (FLX-DEMO-1).
- Subprocess timeout 300 s per `_run` (hermes_client.py:158) bounds single calls; dispatcher loop bounded 600 s (hermes_client.py:279-289).

### Cost-abuse verdict path (prototcol §11 critical test)
> "Can a user pay $X and cause the platform to spend $10X-$100X?"
- Web: credits gate launches (1/launch) and purchases (5/hour) — bounded.
- **Free demo board dispatch: NO.** A logged-in user can call `/api/projects/flux-demo-<ts>/dispatch` repeatedly with no credit, no rate limit, no daily cap → unbounded concurrent swarm launches at platform cost. → NO-GO-tier if left (P0/P1). PROOF in Phase 5.
- **Telegram anonymous: NO.** Any Telegram chat can launch swarms (`max_spawn=8`) indefinitely. → P1. PROOF in Phase 5.

## Prompt injection

- Untrusted inputs that reach an LLM in this product: the user **goal** (which the squad is instructed to execute), template descriptions/names, and generated-repo contents at scan time.
- The goal IS the product's input by design — no system prompt in this repo to inject into (prompt content lives inside Hermes). The `security-scan` skill reads user goal + generated workspace (`security.py:70-89`) — this is a *detector*, not a tool executor.
- Webhook/payment surfaces never feed attacker text into an LLM.
- Verdict: prompt-injection at the *platform* API layer is structurally absent; the user-facing skills are the outer Hermes system (out-of-scope repo audit; BLOCKED for live probe since it would cost tokens).

## Findings

- FLX-AI-1 (P1): No per-launch cost/session budget; only parallel caps + credits. Scale user with 500 credits = up to 500 concurrent-ish agent runs. Reachable.
- FLX-AI-2 (P1): Free (non-BYOK) model runs spend platform tokens; a burst of anonymous Telegram or shared-demo dispatches bills FluxSwarm, not users.
- The static fallback `security.scan_project` (security.py:58-67) is deterministic and catches the critical classes (AKIA, PEM keys, sk-*). `include_llm` enrichment shells out `hermes skills run security-scan -- goal` (security.py:47-49), argv-only (no shell), 60 s timeout — command-injection-safe (list args), but goal up to 4000 chars enters argv (Windows ~32K limit — safe).