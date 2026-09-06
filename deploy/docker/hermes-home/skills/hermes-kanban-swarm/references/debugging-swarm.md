# Debugging Hermes Kanban Swarms (verified this session)

When a swarm "does nothing" or a worker is stuck `blocked`/`running` with no output,
work through this in order. Every step below was reproduced and confirmed.

## 1. Confirm the worker actually spawned
`list` and `show --json` lie — the `status` field is often `None` even while the
worker is alive. The truth is in the **task log**:

```
HERMES_BIN=HERMES_HOME/bin/hermes.exe
BD=<board-slug>
hermes kanban --board $BD show <task_id> --json | grep -E "model_override|provider_override|spawned|claimed"
hermes kanban --board $BD show <task_id>        # Events / Runs section
tail -f HERMES_HOME/kanban/boards/$BD/logs/<task_id>.log
```

A healthy spawn prints the pinned provider/model (current deployment:
`'provider':'openrouter','model':'z-ai/glm-5.2:free'`), then `[run N] spawned {'pid': ...}`.

## 2. The three real failure modes (with fixes)
| Symptom in log | Root cause | Fix |
|---|---|---|
| `Error: Unknown skill(s): planning` | `--worker` skill name doesn't exist under `skills/ecc/skills` | Use a verified name (below); verify with `test -d HERMES_HOME/skills/ecc/skills/<name>` |
| worker `running` forever, empty `workspaces/` | profile default model is a keyed model with no key → dispatch fails, card `blocked`/hung | Pin a runnable model per task (step 3) |
| `Crashed: N ... Auto-blocked: N` | same as above | same fix |

## 3. Pin a model on every task (the definitive fix for blocked workers)
```bash
# After `swarm` returns, loop over all task ids:
for T in $(hermes kanban --board $BD list --json | python -c "import sys,json;[print(t['id']) for t in json.load(sys.stdin)]"); do
  hermes kanban --board $BD set-model $T z-ai/glm-5.2:free --provider openrouter
done
# THEN dispatch
hermes kanban --board $BD dispatch --max 8
```
`set-model` help: `hermes kanban set-model <task_id> [model] --provider <provider>`.
The override "applies on the next dispatch" — so set-model must precede dispatch.

Do NOT write provider/model pins into profile `.env` files. The `opencode-free`/`hy3-free`
defaults were retired; the operator runtime env (openrouter / z-ai/glm-5.2:free) is the
single source of truth, and `set-model` per task is the only override that works.

## 4. Free provider (what the no-key demo uses)
The demo runs on the OpenRouter free tier: provider `openrouter`, model `z-ai/glm-5.2:free`,
authenticated by the operator's `OPENROUTER_API_KEY` (injected as env for every profile).
A direct profile run proves the path works:
```bash
hermes --profile ecc-planner -m z-ai/glm-5.2:free -z "Say exactly: PLANNER_OK" --provider openrouter
# -> prints PLANNER_OK
```
Note: the global default model does NOT propagate to spawned worker profiles —
you MUST pin per-task (step 3). Free endpoints rate-limit aggressively (429): a
burst of parallel workers can exhaust the per-minute quota; retries resume after
the window clears.

## 5. Verified-good ECC skill names (use these in --worker)
Present under `HERMES_HOME/skills/ecc/skills/`:
`plan-orchestrate`, `api-design`, `fastapi-patterns`, `docker-patterns`,
`deployment-patterns`, `tdd-workflow`, `agent-self-evaluation`,
`verification-loop`, `orch-build-mvp`, `orch-add-feature`, `orch-fix-defect`,
`orch-build-mvp`, `repo-scan`, `security-scan`, `security-review`.

NEVER use (do not exist): `planning`, `tdd-guide`, `code-review`.

## 6. Subprocess bridge cmd ordering (Windows/Python)
Build every command as:
```python
cmd = [HERMES_BIN, "kanban", "--board", slug, *args]   # --board BEFORE subcommand
```
`hermes kanban list --board X` (after) → `unrecognized arguments: --board X`.

## 7. Where output lands
- Planner: `workspaces/<task_id>/BREAKDOWN.md` + `swarm_deliverables/PLAN.md`
- Shared deliverables dir: `HERMES_HOME/kanban/boards/<slug>/swarm_deliverables/`
- Per-worker scratch: `workspaces/<task_id>/`
