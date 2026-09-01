# Gate-4 — FINAL REPORT: **NO-GO** (runtime E2E) / fix validated at unit + live-provisioning level

Date: 2026-09-01. Scope: fix authorization, regression suite, three real-worker E2E attempts on isolated temp Hermes homes.

## Verdict
- **Candidate C fix (verifier-skill provisioning): PASS** at unit level (171 tests) and at live-provisioning level (byte-identical copy on a fresh isolated home; no `Unknown skill(s)`; pinning/fallback/venv-isolation all verified).
- **Gate-4 runtime acceptance: NO-GO** — every E2E attempt failed to converge because the `opencode-free` free tier (model `nemotron-3-ultra-free`) is unstable today (retry-thrash, >1h-to-converge, silent mid-generation hangs) and the product stall detector bails in ~100 s.
- Per operator decision, **unit-level evidence is accepted as the Gate-4 evidence**; the live-reviewer-tool criterion is documented as not demonstrated.

## Root cause (recap)
Dispatcher-owned reviewer workers resolve preloaded skills from profile-scoped scope; `requesting-code-review` lives in `skills/software-development/`, so bare `--skills requesting-code-review` did NOT resolve → worker died `Unknown skill`, reviewer blocked, swarm deadlocked (production `u3-*`). Fix = provision `requesting-code-review` into `skills/ecc/skills/` (the reviewer's external dir) before the swarm graph is created.

## Change applied
`backend/hermes_client.py`
- `_ensure_verifier_skill()`: copies the REAL bundled skill byte-for-byte into `skills/ecc/skills/requesting-code-review`; no-op when `SKILL.md` present; re-provisions an empty/stale dir (hardened); refuses to delete a non-empty foreign dir; refuses to fabricate when the bundled source is absent. Wired into `launch_swarm()` and `launch_from_template()`.

`backend/tests/test_verifier_skill.py` — regression A–I plus hardening (`test_ae` empty-reprovision, `test_af` foreign-refusal). **Full suite: 171 passed** (Python 3.11 host; 1 pre-existing starlette warning).

## E2E evidence (all isolated temp homes; production data untouched; SCRATCH-only worker cleanup)
| Attempt | Config | Result | Key evidence |
|---|---|---|---|
| #1 | reused home, 1500 s, max_spawn 8 (double-launch confound, my harness bug) | **NO-GO** — two graphs, 8 workers, `EmptyStreamError` retry-thrash, timed out | `audit\gate4_e2e_evidence\e2e1.json`, board `u4-gate4-candidatec-e2e1` |
| #2 | clean reuse, 3600 s, max_spawn 4 | **NO-GO** — converged partially (planner+TDD done) but architect/devops still running at 1 h; exposed empty-skill-dir no-op gap | `audit\gate4_e2e_evidence\e2e1.json` (v1), board `gate4_home/.../u4-gate4-candidatec-e2e1` |
| #3 | fresh home `gate4_home2`, 10800 s, max_spawn 4 | **NO-GO** — dispatcher `stall=true` fired at 100.5 s: workers silent-hung after LLM stream froze mid-generation; heartbeats stopped ~80 s after start | `audit\gate4_e2e_evidence_v3\e2e1.json`, board `gate4_home2/.../u4-gate4-candidatec-e2e1` |

Across all three attempts the following never failed and are recorded in the evidence JSONs:
- Pinning: every task `model_override=nemotron-3-ultra-free`, `provider_override=opencode-free`.
- Spawn argv (live WMI): `-m nemotron-3-ultra-free --provider opencode-free` on every worker; forbidden providers (`openrouter`, `z-ai`, `glm-5.2`, `anthropic`, `openai`, `gemini`, `kimi`) = 0 hits.
- No `Unknown skill(s)` in any worker log.
- Attempts #2/#3: worker tool path isolation held — `shared_venv_writes.non_pyc = []` (no shared `hermes-agent\venv` pollution on the clean reruns).

## Not demonstrated (acceptance gap)
- Reviewer live spawn → task completion with the provisioned skill; build-fixer/synthesizer execution after the verifier gate; all 7 tasks terminal; `dispatch` outcome `ok`/converged; credit-refund semantics. Cause: workers never finished on the free tier within any attempt.

## Incidents logged
1. **E2E #1 venv leak (attempt #1, harness-caused, CONTAINED)**: a TDD worker's `pip install -e .` wrote an editable `todo_cli` package into the SHARED `hermes-agent\venv\Lib\site-packages` (stamps 2026-09-01 13:55:13) because worker PATH exposed the shared venv's pip. Contained on attempts #2/#3 via PATH-guard `pip.cmd/uv.cmd` shims + `PIP_TARGET`/`PYTHONPATH`. **Operator action left open**: remove/reconcile `todo_cli-0.1.0` dist-info + `__editable__.todo_cli-0.1.0.pth` + `click/__pycache__/testing...pyc` from the shared venv (I did not touch it).
2. **Empty-skill-dir no-op (attempt #2, FIXED)**: `_ensure_verifier_skill()` no-op'd on an empty existing target (interrupted earlier run) which would silently resurrect the MISSING-skill bug. Hardened as described; regression tests added; live-verified byte-identical on attempt #3.

## Recommended path for a future Gate-4 runtime pass (operator-authorized)
1. Validate with a reliable provider/model endpoint (any provider change is operator-owned) OR relax the dispatch stall detector's no-activity window for free-tier runs.
2. Rerun E2E #1 (fresh home pattern: single graph, `max_spawn=4`, pip/path isolation, WMI argv capture) → then #2 and #3 only when #1 is `outcome ok`.
3. Confirm the reviewer live run uses `--skills requesting-code-review` and completes; then build-fixer after the gate; all 7 terminal; checkout credit semantics on a stuck board.

## Artifacts
- `backend/hermes_client.py` (fix), `backend/tests/test_verifier_skill.py` (tests), `audit/gate4_e2e.py` (driver), `audit/GATE-4-REVIEWER-ROOTCAUSE.md` (root cause), `audit/GATE-4-E2E1-FAILURE.md` (attempts #1–#3 detail), evidence JSON dirs `audit/gate4_e2e_evidence*`.