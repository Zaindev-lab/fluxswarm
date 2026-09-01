# GATE-4 — Reviewer Approval Root-Cause Investigation (SOURCE-TRACE ONLY)

Status: DRAFT — TASK 1 & TASK 2 complete; TASK 3 (fix design) & TASK 4 (tests) pending.
Scope: read-only source trace + read-only introspection. No patches, no E2E #3, no approval-policy change.

## TL;DR

The E2E #2 reviewer failure is **NOT an approval problem**. The ecc-reviewer worker dies at
**startup, before any model call**, because the Kanban Swarm verifier task is created with a
hard-coded skill `requesting-code-review`, and that skill is **not resolvable under the
`ecc-reviewer` profile** (it only lives in the bundled `software-development` skill collection,
which the profile's `skills.external_dirs` scan does not mount). The `approval.pending` /
"detached (reaped) runtime" RPC rejections in agent/errors.log were a **downstream artifact**:
the worker process had already exited, so every gateway approval poll for that session hit a
reaped (gone) runtime, and the dispatcher's `pid not alive` crash-detector retried and then
`gave_up`.

## Evidence chain (all verified on host today)

1. Board task `t_555c4895` (ecc-reviewer) has `skills: ["requesting-code-review"]` in the kanban DB.
2. Konban worker spawn in `hermes_cli/kanban_db.py:_default_spawn` builds:
   `hermes -p ecc-reviewer --cli --accept-hooks --skills requesting-code-review chat -q "work kanban task t_555c4895"`
   (`_default_spawn` at kanban_db.py:10852-10890; per-task skills → `--skills` pairs at 10867-10870).
3. The worker log `<board>/logs/t_555c4895.log` shows (twice, run 6 + run 7):
   ```
   Query: work kanban task t_555c4895
   Initializing agent...
   Error: Unknown skill(s): requesting-code-review
   ```
4. `requesting-code-review` is a **bundled** skill: exists at
   `C:\Users\DELL\AppData\Local\hermes\skills\software-development\requesting-code-review\SKILL.md`,
   and is listed in `skills/.bundled_manifest:64` as `requesting-code-review:f7e90257…`.
5. It is NOT under `skills/ecc/skills/`. Read-only `hermes skills list` under the **default** profile
   shows `requesting-code-review │ software-development │ builtin │ enabled`; under `-p ecc-reviewer`
   it shows ONLY `agent-self-evaluation` and `verification-loop` (both `ecc/skills`), **no**
   `requesting-code-review`.
6. The `ecc-reviewer` profile config (`profiles/ecc-reviewer/config.yaml`) sets
   `skills.external_dirs: C:/Users/DELL/AppData/Local/hermes/skills/ecc/skills` — so its skill scan
   = profile-local skills + `ecc/skills`, NOT the bundled `software-development` collection.
   The worker additionally switches `HERMES_HOME` to profile scope (kanban_db.py:10766
   `resolve_profile_env`), so the bundled default-home realm is not seen.
7. Skill preload in the CLI/chat path calls `build_preloaded_skills_prompt`
   (`agent/skill_commands.py:839`); missing identifiers are returned and, when a requested skill
   cannot be found, the CLI raises `ValueError(f"Unknown skill(s): ...")` — the same contract as
   `hermes_cli/oneshot.py:74-80`.
8. Therefore the worker exits rc!=0 immediately with **zero heartbeats**, dispatcher observes
   `pid <n> not alive`, retries, same result → `consecutive_failures=2`, `gave_up`. Meanwhile the
   gateway's approval client polls `approval.pending` on the dead session and gets the logged
   "session-scoped RPC rejected: method=approval.pending session_id='63e0005e' not in memory
   (detached/reaped runtime…)" — dead-session noise, not the cause.

## Why it was reviewer-specific (matches E2E #2 observation)

- Only the swarm **verifier** task hard-codes `skills=["requesting-code-review"]`
  (`hermes_cli/kanban_swarm.py:304`, with `create_swarm` at :293-305). Every other worker's task
  skills come from the swarm `--worker prof:title:skills` spec or FluxSwarm's
  `hermes_client.py` SQUAD/VERIFIER/AGENT_REGISTRY, all of which use real `ecc/skills` skills
  (`plan-orchestrate`, `api-design`, `fastapi-patterns`, `docker-patterns`,
  `deployment-patterns`, `tdd-workflow`, `hum;anizer`).
- The planner/architect/devops/tdd workers succeeded in E2E #2 because their task skills resolve
  under their profiles. The build-fixer task stayed `todo` per orchestration gating (expected).
- The verifier task is created via `hermes kanban swarm --verifier <profile>`; the CLI accepts the
  verifier **profile** but has no way to override the verifier task's skills
  (`hermes_cli/kanban.py:417`, `--verifier` only), so kanban_swarm.py hard-codes the name.
- Hence: any `-p` profile served as verifier that does NOT mount the bundled `software-development`
  collection hits this. On this host, all ECC profiles mount only `skills/ecc/skills`.

## Answers to TASK 1 Q1-Q7

- Q1 (who emits `approval.pending`): the gateway-side approval **client** (gateway/TUI session) polls
  `approval.pending` RPC (`tui_gateway/methods_prompt.py:1588`); it polls the worker's session for
  pending approval state.
- Q2 (who receives/rejects): `tui_gateway/server.py` `_sess_nowait` (rejection log at ~:3380) rejects
  because the session `63e0005e` is **not in the in-memory `_sessions` dict** — the owning process
  is gone (worker already exited).
- Q3 (why detached/reaped): the worker process that owned session `63e0005e` already **terminated at
  startup** (skill preload failure). The gateway only keeps live sessions; when the process dies the
  session is "detached" (stored to disk) and then "reaped" from memory.
- Q4 (where the stored session exists): Hermes state store (`hermes_state.SessionDB`, state.db),
  tagged `source=kanban` by `_retag_legacy_worker_sessions` / `retag_kanban_worker_sessions`
  (kanban_db.py:10700-10717, 10832).
- Q5 (why it cannot resume): a live gateway has no in-memory session to resume from; the worker's own
  process died, so `_sess_nowait` finds nothing. Nothing would be gained by resuming anyway — the
  worker would just die again on `--skills requesting-code-review`.
- Q6 (specific to ecc-reviewer config/skills/runtime?): **Yes — skills.** Not approval config.
  All ECC profile config.yaml files are effectively identical (only `skills.external_dirs` differs in
  value plus description). The difference is the **task's hard-coded skill** vs the skill namespace a
  profile mounts. This is one canonical bad skill name; it is not a runtime/pinning/provider bug.
- Q7 (is the approval necessary for reviewer operation?): **No.** The reviewer never reached any
  approval gate; it died pre-model. The `approval.pending` polls are a side effect of the dead
  session. No approval policy or YOLO setting drove the failure.

## TASK 2 — profile comparison results

| Profile | config.yaml approvals | skills.external_dirs | task skills used in E2E | resolve under profile? |
|---|---|---|---|---|
| ecc-planner | none | ecc/skills | `plan-orchestrate` | YES (worker OK) |
| ecc-architect | none | ecc/skills | `api-design,fastapi-patterns` | YES (worker OK) |
| ecc-devops | none | ecc/skills | `docker-patterns,deployment-patterns` | YES (worker OK) |
| ecc-tdd | none | ecc/skills | `tdd-workflow` | YES (worker OK) |
| ecc-reviewer | none | ecc/skills | **`requesting-code-review` (hard-coded by kanban_swarm)** | **NO → crash** |
| ecc-build-fixer | none | ecc/skills | `humanizer` | YES (never ran; gated) |

No approval-requirement/interactive-tool/toolsets/permissions/session-config/env differences exist
between reviewer and the working profiles. The only effective difference is the verifier task's
skill name.

## TASK 3 — minimal fix candidates (DESIGN ONLY, not implemented)

Constraint: no Hermes source change, no global security relaxation, no provider/opencode-free changes,
no production-data mutation, no watchdog changes, no unbounded approvals bypass.

Candidate A (preferred, minimal, Hermes API-native):
Make FluxSwarm create the swarm verifier with ECC-legible skills instead of relying on the
`hermes kanban swarm` CLI's hard-coded verifier skill. Concretely: after `create_swarm` returns, or
when constructing the swarm command, set the verifier task's `skills` to the reviewer profile's real
skills (`agent-self-evaluation,verification-loop` per `hermes_client.py:50`) using the kanban DB
update path (`kb.update_task_skills` / equivalent), scoped ONLY to the verifier task.

Candidate B (FluxSwarm-side, no task-DB surgery):
Route FluxSwarm's swarm creation through its own registry (AGENT_REGISTRY["Reviewer"][1]) and, when
the swarm CLI cannot express a verifier skill override, update the created verifier task's skills row
to `["agent-self-evaluation","verification-loop"]` directly via the kanban API on the FluxSwarm side.

Candidate C (skills-packaging, no code change):
Add an ECC-owned `requesting-code-review` skill under `skills/ecc/skills/` so the name resolves under
the reviewer profile. This is purely additive to the ECC skill dir (data, not Hermes source), but
adds a duplicate skill whose content must be maintained; prefers A/B which use real ECC skills.

Recommendation: Candidate A, with B as its concrete implementation in `hermes_client.py`
(FluxSwarm-owned code). It is reviewer-scoped (verifier task only), non-interactive, no global
approval change, and it does not touch Hermes source or the approval gate.

## TASK 4 — regression tests (planned; run before any isolated validation)

- A. Verifier task with `agent-self-evaluation,verification-loop` spawns a worker whose process stays
  alive (no `Unknown skill(s)` startup exit; no approval.pending on a dead session).
- B. A genuinely-required approval still blocks correctly (send a task/command that hits
  `single_query_mode: deny` and assert it is denied/asked, not auto-passed).
- C. Reviewer lifecycle `ready → running → heartbeat/activity → completed` with kanban_complete.
- D. Pinning stays `nemotron-3-ultra-free` / provider `opencode-free` on the reviewed run.
- E. No fallback to openrouter/z-ai/paid providers in provider chains under test.
- F. Existing watchdog tests remain green.

Gate: if any of A-F fails → STOP; do not run the isolated dispatcher validation. The isolated
validation is TASK 5 (real dispatcher-owned reviewer run), which itself is NOT E2E #3 and requires
separate user authorization before E2E #3.

## Files / artifacts referenced

- `hermes_cli/kanban_swarm.py:304` — hard-coded `skills=["requesting-code-review"]` (verifier).
- `hermes_cli/kanban.py:407-417,1624,1636` — `kanban swarm` CLI, `--verifier` profile only.
- `hermes_cli/kanban_db.py:10720-10932` — `_default_spawn` (worker spawn, `--skills`, HERMES_HOME pin).
- `agent/skill_commands.py:232-269` (`_load_skill_payload`), `:839-904` (`build_preloaded_skills_prompt`).
- `hermes_cli/oneshot.py:59-82` — raise on all-missing skills (same contract as chat preload).
- `agent/skill_utils.py:533-623` — external_dirs resolution; local dir always first.
- `tui_gateway/methods_prompt.py:1588` — `approval.pending` RPC emitter.
- `tui_gateway/server.py:3380` — `_sess_nowait` detachment rejection.
- `C:\Users\DELL\AppData\Local\hermes\profiles\ecc-reviewer\config.yaml` — external_dirs only.
- `C:\Users\DELL\AppData\Local\hermes\skills\software-development\requesting-code-review\SKILL.md` — bundled.
- `C:\Users\DELL\fluxswarm\backend\hermes_client.py:44-62` — SQUAD/VERIFIER/SYNTHESIZER/AGENT_REGISTRY.
- Board log `…/kanban/boards/u3-1788210774-f8a29392/logs/t_555c4895.log` — the `Error: Unknown
  skill(s): requesting-code-review` evidence.