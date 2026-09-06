# Verified command transcripts — FluxSwarm session (2026-08-28)

Host: Windows 11, Hermes CLI at `C:/Users/DELL/AppData/Local/hermes/bin/hermes.exe`,
HERMES_HOME = `C:/Users/DELL/AppData/Local/hermes`. Interpreter: `python` (venv), NOT `python3`.

## 1) Create a board, then launch a swarm on it (board must exist first)
```
hermes kanban boards create flx-demo --description "FluxSwarm isolation test"
hermes kanban --board flx-demo swarm "demo goal" \
  --worker "ecc-planner:Demo plan:planning" \
  --verifier "ecc-reviewer:Demo review:code-review" \
  --synthesizer "ecc-build-fixer:Demo build:orch-build-mvp" \
  --created-by flx-swarm --json
```
Output JSON:
```json
{"root_id":"t_6a294d1b","worker_ids":["t_5739cd49"],"verifier_id":"t_a17df154","synthesizer_id":"t_cba8f0bb"}
```

## 2) List the isolated board
```
hermes kanban --board flx-demo list
```
Shows root `done`, worker `ready`, verifier/synthesizer `todo` — isolated from other boards.

## 3) Dispatch (run the workers) and observe live
```
hermes kanban dispatch --board flx-demo --max 8
hermes kanban --board flx-demo list
```
Result: root done, 4 workers running, verifier+synthesizer queued.

## Gotchas confirmed
- `--board X swarm` WITHOUT `boards create X` first → "board 'X' does not exist. Create it with `hermes kanban boards create X`."
- `--worker` args with spaces in the TITLE must be ONE quoted shell arg: `"ecc-planner:Plan the feature breakdown:planning"`. Splitting breaks parser.
- `dispatch --max 0` does NOT spawn; use `--max 8` to actually run.
- `dispatch --dry-run` validates wiring without spawning.
