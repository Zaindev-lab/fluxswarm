# Multi-bot squad: Hermes kanban `swarm` (working recipe)

Once you have N bot profiles (see SKILL.md Workflow), you can make them work
**together on one project** as a parallel swarm: workers run concurrently ->
a verifier checks outputs -> a synthesizer assembles the final result.

## Why kanban (not delegate_task)
`delegate_task` spawns isolated subagents that cannot share a board or a
project workspace. The Hermes **kanban swarm** is the multi-profile team
mechanism: each card is claimed atomically by a *named profile* and executed
in an isolated workspace, so the bots' own skills/SOUL apply.

## Working command sequence (verified Windows/git-bash)
```bash
HERMES_HOME="$LOCALAPPDATA/hermes"

# 1) scope the work onto its own board (swarm lands on 'default' otherwise)
./bin/hermes.exe kanban boards create ecc-project \
  --description "ECC multi-bot build squad"

# 2) launch the swarm — NOTE every --worker/--verifier/--synthesizer value
#    MUST be ONE fully-quoted string "profile:Title:skill1,skill2"
./bin/hermes.exe kanban swarm "Build a small FastAPI service with CI/CD and tests" \
  --worker "ecc-planner:Plan the feature breakdown:planning" \
  --worker "ecc-architect:Design service architecture:api-design" \
  --worker "ecc-devops:Set up CI/CD and Docker:docker-patterns,deployment-patterns" \
  --worker "ecc-tdd:Write the test suite:tdd-guide" \
  --verifier "ecc-reviewer:Review code quality:code-review" \
  --synthesizer "ecc-build-fixer:Assemble and make it green:orch-build-mvp" \
  --created-by default --json

# 3) validate wiring WITHOUT spawning real workers
./bin/hermes.exe kanban dispatch --dry-run --json

# 4) actually run it (against a real project dir set via board workdir)
./bin/hermes.exe kanban dispatch

# 5) pause cleanly (reclaims running workers back to 'ready', keeps cards)
./bin/hermes.exe kanban dispatch --max 0
```

## Pitfalls hit and fixed this session
- **Argparse breaks on spaces.** Bare `--worker ecc-planner:Plan the feature
  breakdown:planning` throws `hermes: error: unrecognized arguments: the
  feature breakdown:planning`. Always wrap the whole value in quotes.
- **Swarm cards land on `default` board.** Create/switch a board first if you
  want the squad scoped to a project; otherwise they mix into `default`.
- **`python3` is NOT on this host** — the interpreter is `python` (venv). Run
  `scripts/verify_skills.py` with `python`, not `python3`.
- **verify probe "no (check external_dirs)" is a red herring.** The probe only
  checks the profile's *local* `skills/` dir. Trust the
  `ECC/3rd-party skills via external_dirs: N` line (N=286 for ECC) as the real
  signal that the pack is wired.
- **`hermes config set description ...` warns** "'description' is not a recognized
  config key — it was saved anyway". The reliable path is to pass
  `--description` at `profile create` time; the key still bridges to the env.
- **`kanban dispatch --max 0` does NOT delete cards** — it reclaims running
  workers to `ready`, so you can resume later with a plain `dispatch`.
