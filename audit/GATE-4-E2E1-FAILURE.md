# Gate-4 — E2E #1 RESULT: NO-GO (convergence timeout) — Candidate C fix PROVISIONING/PINNING PASS

Status: **FAILURE — STOPPED per critical stop rule** (no auto-patch, no E2E #2/#3).
Date: 2026-09-01 (UTC ms 1788265798 → 1788267473 window).
Scope: isolated temp HERMES_HOME `C:\Users\DELL\AppData\Local\Temp\opencode\gate4_home`.

## 1. Verdict
- Candidate C fix (verifier-skill provisioning) itself: **PASS** on provision + resolve + no-Unknown-skill.
- Runtime pinning (G/H): **PASS**.
- No paid-provider fallback: **PASS** (0 hits).
- Swarm convergence: **FAIL** — wall-clock timeout with workers still `running`; reviewer/builder never ran (correctly gated on workers).
- Overall Gate-4 E2E #1: **NO-GO**.

## 2. Board / evidence locations
- Board slug: `u4-gate4-candidatec-e2e1` (temp home). NOTE: hidden double-swarm confound (see §7).
- Board DB: `...\gate4_home\kanban\boards\u4-gate4-candidatec-e2e1\kanban.db`
- Task logs: `...\boards\u4-gate4-candidatec-e2e1\logs\*.log`
- Evidence JSON: `C:\Users\DELL\fluxswarm\audit\gate4_e2e_evidence\e2e1.json` (incl. live WMI spawn-argv capture, 2679 lines deduped below)
- Workspaces: all under TEMP (confirmed; no production write path).

## 3. Pinning result (each task, from tasks table)
All tasks (both roots, all 4 worker profiles, reviewer, builder): `model_override=nemotron-3-ultra-free`, `provider_override=opencode-free`. Reviewer task kept `skills=["requesting-code-review"]`. **PASS.**

## 4. Spawn-command evidence (WMI-captured, real worker argv)
```
hermes.EXE -p ecc-planner  --cli --accept-hooks --skills plan-orchestrate   -m nemotron-3-ultra-free --provider opencode-free ... chat -q "work kanban task t_991a3264"
hermes.EXE -p ecc-architect --cli --accept-hooks --skills api-design --skills fastapi-patterns -m nemotron-3-ultra-free --provider opencode-free ... chat -q ...t_c19ca6be
hermes.EXE -p ecc-devops ... -m nemotron-3-ultra-free --provider opencode-free ... t_26a110ff
hermes.EXE -p ecc-tdd ... -m nemotron-3-ultra-free --provider opencode-free ... t_6a651b87
```
Forbidden-provider substring scan across ALL captured argv (`openrouter`, `z-ai`, `glm-5.2`, `anthropic`, `openai`, `gemini`, `kimi`): **0 hits. PASS.**

## 5. Candidate C proof on the isolated env
- Bundled source present: `...\gate4_home\skills\software-development\requesting-code-review\SKILL.md` → `true`.
- Post-launch prov knowledge: `...\skills\ecc\skills\requesting-code-review` EXISTED and **byte-identical** to bundled source.
- Profile-scoped preload probe (real loader, temp reviewer profile): before launch `MISSING=['requesting-code-review']`; ECC-local skills load; after provisioning (unit test E/F + this env) resolves.
- `Unknown skill(s)` grep across ALL E2E board logs: **NONE**.

## 6. Convergence / failed-task specifics
- dispatch outcome: `stuck`, `timed_out=true`, `stall=null`, `terminal=false` after 1500s.
- At stop: 7 workers `running` with FRESH heartbeats (hb_age 4–52 s) — alive, slow, not dead.
- `task_runs` failures (only crashes):
  - `t_abd57d7a` (ecc-architect, secondary graph): run 8 crash `pid 23884 not alive`; rerun 11 crash `pid 2204 not alive` → task left `blocked`.
  - No other crash; no provider/skill error text in any other run.
- Reviewer tasks stayed `todo` (correct gating — never promoted to ready because workers did not finish); builder stayed `todo`. Therefore **reviewer live-spawn/completion was NOT exercised in E2E #1**.
- Credit: no `done` agent task → `board_has_completed_work=false`; in production this board would trigger a launch-credit refund on a stuck finalize. Not refunded here (no main.py DB wiring in driver).

## 7. Root-cause classification (convergence timeout)
Primary: **upstream provider instability on opencode-free (free tier)** — worker log `t_abd57d7a.log` shows repeated
`EmptyStreamError after 24.9s ... reconnecting` + `Retrying in 2.4s/5.5s (attempt 1/3)` cycles; the crashed architect died there.
Contributing harness confound (my error): the driver was double-launched on the same slug (first run crashed at `db_rows` AFTER a successful `launch_swarm`; rerun launched a second swarm graph), producing **two sibling swarm graphs (14 tasks), 8 concurrent free-tier workers** instead of 4. Provider throttling/stream drops are consistent with 2× concurrency on the throttled free tier.
Secondary security finding (isolation leak, harness): a TDD worker's `pip install -e .` wrote a **`todo_cli` editable package into the SHARED production `hermes-agent\venv\Lib\site-packages`** (files stamped 2026-09-01 13:55:13; `__editable__.todo_cli-0.1.0.pth`, `todo_cli-0.1.0.dist-info/*`) plus a `click` pyc. Worker tool actions inherit the ambient interpreter/PATH → pip targets the global hermes-agent venv, not the TEMP workspace. This MUST be isolated in any rerun (sanitize PATH + pip/uv target a temp venv). Operator action required to decide on restoring the leaked `todo_cli` install (I did NOT modify the venv).

## 8. Cleanup already performed (scratch only)
- Stopped 7 orphaned TEMP-home kanban workers (PIDs 10412, 15480, 19176, 18576, 19916, 5612, 22084) — verified these were `nemotron-3-ultra-free … kanban task` processes.
- No production process was killed; production boards/DB untouched by E2E processes (their `-shm` mtime churn is the pre-existing persistent gateway/cron daemon's normal heartbeat activity).

## 9. What a rerun (only when authorized) must change
1. One launch per slug; verify no residual board before relaunch (implemented — driver now force-clears the scratch board via the `\\?\` namespace and refuses to continue if it cannot).
2. Sanitize worker env so pip/uv cannot write into the shared hermes-agent venv (implemented — PATH-guard `pip.cmd` shims + `PIP_TARGET` + `PYTHONPATH`; verified `shared_venv_writes.non_pyc == []` on the clean rerun).
3. Lower concurrency for free-tier (implemented — `E2E_MAX_SPAWN=4`, `E2E_TIMEOUT_S=3600`).
4. Then validate reviewer live run → completion, builder run after gate, all 7 terminal, no fallback, no refund, convergence `ok`.

---

## UPDATE: E2E #1 clean rerun (2nd attempt) — still NO-GO (convergence timeout)
2026-09-01, isolated temp home, `max_spawn=4`, `timeout_s=3600`. Evidence `audit\gate4_e2e_evidence\e2e1.json` (rewritten by the clean run).

### Result
- Single swarm graph (root `t_da37c834`), 4 workers spawned, reviewer/builder queued. outcome `stuck`, `timed_out=true`, `stall=null`, wall 3633s, EXIT 1.
- **Completed in 1h**: planner `t_16e24f08` done, tdd `t_925e5326` done (real artifacts in workspaces: tdd 24 files, planner 2 files). **Still running at cap**: architect `t_e7eac4cb` (run 6, hb_age 16s, 21 files) and devops `t_da3f729a` (run 4, hb_age 46s, 26 files). Reviewer/builder correctly queued behind them.
- The swarm WAS converging but far too slowly; the free-tier `opencode-free` provider still showed `Retrying in …` stream interruptions (empty-stream pattern seen in E2E #1 logs) — even at 1× concurrency the 4-worker graph did not finish within an hour.

### What PASSED on the clean rerun
- Pinning: all 7 tasks `model_override=nemotron-3-ultra-free`, `provider_override=opencode-free`.
- Spawn argv (WMI): only `dispatch --max 4` + the 4 worker `-m nemotron-3-ultra-free --provider opencode-free` commands; forbidden-provider scan **0 hits**.
- pip isolation: `shared_venv_writes = {non_pyc: [], pyc_count: 3}` — the TDD worker no longer wrote `todo_cli` into the shared hermes-agent venv. Confirmed the E2E1 leak is contained.
- No `Unknown skill(s)` in any E2E #1 board log.

### NEW FINDING — provisioning robustness gap (empty-dir no-op)
- `_ensure_verifier_skill()` no-ops on `target.exists()` without checking `(target/"SKILL.md").exists()`. During the interrupted runs the temp target `skills/ecc/skills/requesting-code-review` was left as an **EMPTY directory** (confirmed: 0 entries; exactly 1 empty dir among all 300+ `ecc/skills` dirs). On the clean rerun launch, provisioning therefore no-op'd and `byte_identical_to_bundled` came back **false** (target empty vs source SKILL.md). → **FIX APPLIED (authorized):** `_ensure_verifier_skill` now re-provisions when `SKILL.md` is absent (rmtree of the empty dir first), refuses to destroy a non-empty foreign dir, and the empty-dir + foreign-refusal cases are covered by new tests `test_ae`/`test_af`. Full suite **171 passed**.

---

## UPDATE 2: E2E #1 — 3rd attempt (fresh isolated home `gate4_home2`, hardé fixed fix, max_spawn=4, timeout_s=10800) — NO-GO (dispatcher stall)
2026-09-01. Evidence `audit\gate4_e2e_evidence_v3\e2e1.json`. Single graph root `t_224bfcf8`; 4 workers spawned.

### Result
- `dispatch` outcome `stuck`, `terminal=false`, `timed_out=true`, **`stall=true`**, wall **100.5s** (the dispatcher's own stall detector fired at ~100s — far short of the 3h cap).
- All 4 workers `running` + reviewer/builder queued at bail. Worker processes stayed alive but **heartbeats froze ~80s after start** (45s stale, not advancing) → that is what tripped the detector. No crash, no error text anywhere.

### What PASSED (3rd attempt)
- Provisioning: `bundled_source=true`, `target_exists_after=true`, **`byte_identical_to_bundled=TRUE`** (fresh home + hardened fix). The empty-dir gap is closed.
- Pinning: all 7 tasks `nemotron-3-ultra-free` / `opencode-free`; WMI spawn argv shows only the 4 free-tier worker commands + `dispatch --max 4`; forbidden-provider hits **0**; `shared_venv_writes.non_pyc=[]` (pip isolation held; pyc_count 0).
- No `Unknown skill(s)` in any log.

### Root-cause classification (3rd attempt)
Workers started real work (planner log shows `kanban_show` + workspace `ls` + mid-reasoning) then the **LLM stream stopped mid-generation with NO error and NO retry line**; the worker went silent (no heartbeat), and the dispatcher's stall detector bailed at ~100s. This is the `opencode-free` free tier failing differently from attempts #1/#2 (silent hang instead of `EmptyStreamError` retry-thrash / slow-but-progressing). The product-level stall detector (~90s no-progress) is too eager for the free tier's long/torn single-turn generations.

### Gate-4 status
- Unit/regression level (Candidate C): **PASS** — 171 tests green, provisioning live-proven byte-identical on a fresh isolated home.
- Runtime E2E level (reviewer spawn → completion, build-fixer after gate, all 7 terminal, convergence `ok`, no refund): **NOT DEMONSTRATED** — three consecutive NO-GO (provider instability on `opencode-free` in every mode: 8-way thrash, 1h slow-converge, 100s silent-hang).
- Per the critical STOP rule, no further E2E runs are auto-attempted. Continuing requires operator decision: tune/disable the stall detector for the free tier (product config — operator-owned), switch provider/model for validation, or accept unit-level Gate-4 and document the live-tool caveat.
- Consequence: if the reviewer had been promoted in that state, preloaded skills would have been `MISSING=['requesting-code-review']` — i.e. the ORIGINAL bug resurfaces silently. The reviewer never ran (gating), so the empty target never caused an in-run crash, but **the live reviewer path is still NOT validated** and the no-op deserves a hardening fix: treat an empty/invalid target (no SKILL.md) as “missing” and (re)provision. Origin of the empty dir: harness/cross-run interference, NOT the code path of this run; provisional fix + fresh isolated home + reviewer-spawn validation is the correct next step once authorized. Unit test A–D should be extended to cover the “target exists but empty” case.