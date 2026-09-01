# FluxSwarm — Production Operations Runbook

Baseline: commit `5ee4ff6` · branch `master` · GitHub CI run #33566523397 (SUCCESS).
Runtime: FluxSwarm -> Hermes -> ECC profiles/skills -> AI provider.
Hermes production candidate: **v0.20.6 @ 00bbfc690** (pinned).

> This runbook is operator documentation only. It does not deploy, and it never
> prints or commits secrets. All secret values must be injected by the operator
> into the environment; they must NOT be placed in this repository.

---

## Dispatch window (IMPORTANT)

**Use `900` seconds** (`FLUXSWARM_DISPATCH_TIMEOUT_S=900`) as the NORMAL dispatch
window for a swarm launch. The Gate-4/Gate-5 probe used **300s** strictly as a
bounded *test* window — it is NOT a production value.

- Free-tier convergence is **multi-pass** (workers -> verifier/reviewer ->
  synthesizer) and can require several dispatching passes; a single 300s window
  often returns before the synthesizer finishes.
- Allowing the driver to stop mid-flight can leave a `running` claim behind.
  This is expected and safe: the **next dispatch pass reclaims** it and continues.

---

## 1. Installation

- Python 3.11 (host), Hermes agent venv at `hermes-prod/hermes-agent/venv`.
- Install backend deps:
  `pip install -r backend/requirements.txt`
- Pin Hermes to the verified commit (no upstream modification):
  `git -C hermes-prod/hermes-agent checkout 00bbfc690060d1323ddb2f065297c7425cb71c26`
- Editable install into its own venv (matches the clean staging env convention):
  `uv pip install -e hermes-prod/hermes-agent`

## 2. Hermes setup

- Single pinned version. A fresh `HERMES_HOME` does NOT self-create ECC profiles
  or the bundled skills on `kanban init` — the ECC provider below must seed them.
- `HERMES_HOME` is deterministic; set it explicitly (see Environment variables).

## 3. ECC provisioning

FluxSwarm-owned scaffolding (not Hermes/ECC upstream):

- Create the 9 profiles under `$HERMES_HOME/profiles/`
  (`ecc-planner`, `ecc-architect`, `ecc-devops`, `ecc-tdd`, `ecc-reviewer`,
  `ecc-build-fixer`, `ecc-catalog`, `ecc-security`, `ecc-test`).
- Each profile `config.yaml` must set `skills.external_dirs` to
  `<HERMES_HOME>/skills/ecc/skills`.
- Seed the 10 squad skills under `$HERMES_HOME/skills/ecc/skills/`:
  `plan-orchestrate`, `api-design`, `fastapi-patterns`, `docker-patterns`,
  `deployment-patterns`, `tdd-workflow`, `agent-self-evaluation`,
  `verification-loop`, `orch-build-mvp`, `requesting-code-review`.
- Seed the bundled collection under `$HERMES_HOME/skills/software-development/`
  (source of the real `requesting-code-review`).
- `requesting-code-review` is **provisioned idempotently** at launch by
  `hermes_client._ensure_verifier_skill()` (byte-for-byte from the bundled
  collection; never overwrites an existing `SKILL.md`). Running provisioning
  twice does NOT corrupt valid assets.
- Verify with FluxSwarm preflight (`hc.preflight()` == no problems).

## 4. Environment variables

Required names (values are operator-supplied and must never be committed):

- `FLUXSWARM_FERNET_KEY`
- `FLUXSWARM_JWT_SECRET`
- `FLUXSWARM_DEFAULT_PROVIDER`
- `FLUXSWARM_MODEL_<PROVIDER>` (or `FLUXSWARM_DEFAULT_MODEL`)
- `FLUXSWARM_HERMES_BIN`
- `HERMES_HOME`
- Paddle: `PADDLE_API_BASE`, `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`,
  `PADDLE_PRICE_STARTER/PRO/SCALE`, `PADDLE_CLIENT_TOKEN`
- Database/storage: `FLUXSWARM_DB` (SQLite path)
- Rate limiting OPTIONAL: `FLUXSWARM_REDIS_URL`
- Ops: `FLUXSWARM_DISPATCH_TIMEOUT_S` (default `900`)

For development/demo only (NEVER set in production): `FLUXSWARM_DEMO_MODE=1`.

## 5. Secret injection

- Generate `FLUXSWARM_FERNET_KEY` (Fernet 32-byte base64) and
  `FLUXSWARM_JWT_SECRET` securely and inject them into the environment via the
  host secret manager / secure env (NOT in files, NOT in the repo).
- **Do not print or echo their values.**

## 6. Provider setup

- Choose one of the supported paid providers: `anthropic`, `openai`, `gemini`,
  `kimi`.
- Set `FLUXSWARM_DEFAULT_PROVIDER` and the corresponding `FLUXSWARM_MODEL_*`.
- If no real credentials are present, do NOT set a placeholder and do NOT fall
  back to `opencode-free` — mark **OPERATOR ACTION REQUIRED** until a paid
  provider + key is configured.
- `opencode-free` / `nemotron-3-ultra-free` is **demo-only** and runs only via
  `FLUXSWARM_DEMO_MODE=1` or an explicit user BYOK opt-in.

## 7. Startup

- `backend/run.sh` (Linux/Docker) or `backend/run.ps1` (Windows) supervises
  uvicorn on `127.0.0.1:8787` and restarts it if `/health` fails. The app locks
  against duplicate instances (serverlock).
- Production startup refuses to run if the required secrets are absent
  (`envguard.assert_production_secrets()` runs before other imports).

## 8. Health check

`GET /health` -> 200 when ready.

## 9. Logs

- Backend: `fluxswarm.log`, `fluxswarm_err.log` (supervisor redirect).
- App security events: append-only audit (`audit.jsonl`); sensitive fields
  (`token/key/secret/password/pw_hash/cipher/raw`) are dropped before write.
- Hermes/kanban logs under `$HERMES_HOME/logs` and per-board `kanban.db`.

## 10. Backup

Backend SQLite + audit + key vault backup (no secrets persisted in the repo):
- `backup.py do_backup` -> binary snapshot + SHA-256 manifest of
  `users.db`, `audit.jsonl`, `byok.json`, `.jwt_secret`.
- Run `do_verify` on the archive before/after restore.

## 11. Restore

- Restore ONLY into an isolated target; never in place over a live DB.
- Use `do_restore(archive, target=isolated_path, verify=True)`.

## 12. Restart

- Stop the supervisor, `run.sh`/`run.ps1` restarts uvicorn on failure. Restart
  is otherwise a clean stop/start of the supervisor process.

## 13. Worker failure recovery

- A failed worker becomes `blocked`/`stuck` after bounded retries; the driver
  returns `outcome=stuck`/`timed_out` within the dispatch window, never hanging
  forever and never reporting fake completion.
- Re-run dispatch to re-dispatch queued/blocked tasks after the provider recovers.

## 14. Provider failure recovery

- On provider failure, workers land `blocked` (bounded), not endless-`running`.
- Resolve the provider, then re-dispatch the board. Credits are refunded only
  when no real work was produced (idempotently; never on converged work).

## 15. Stale task recovery

- If a `running` task has no live worker process and its heartbeat goes stale,
  the next dispatch pass reclaims/queries and either completes it or reports it
  truthfully. Do not manually force-complete tasks.

## 16. Account deletion

- `DELETE /api/account` (authenticated) removes owned app data and owned Hermes
  boards/workspaces (path-containment validated). It does NOT delete another
  user's data and does NOT remove the append-only security audit log.

## 17. Paddle webhook troubleshooting

- Endpoint `POST /api/payments/webhook` is signature-verified (V1 and V2);
  unauthenticated by design but rejects bad signatures and replies 503 when the
  gateway is not configured.
- Events are idempotent (no double credit on replay) and refunds are single-apply.
- Keep Paddle in SANDBOX; the LIVE guard forbids mock while live credentials are
  present. Promote to LIVE only under a controlled change after sign-off.

## 18. Emergency shutdown

- Stop the supervisor (kill `run.sh`/`run.ps1` and the uvicorn process). No user
  task state is destroyed; boards persist in `HERMES_HOME`/SQLite.

## 19. Rollback

- Return to the previous verified commit (default: `5ee4ff6`), restore the last
  verified backup into an isolated location, verify, then restart the supervisor.
- Do not roll back Hermes/ECC upstream; keep the pinned `00bbfc690` unless proven.

---

## Operator backup procedure (concise)

1. Stop accepting new dispatches (maintenance flag or pause).
2. `do_backup(default_data, out_dir)` -> write archive `+ SHA-256 manifest`.
3. `do_verify(archive)` -> integrity OK.
4. Copy archive off-host (encrypted). 
5. Store the manifest separately (it lists checksums, not secret values).
6. Restore only into a fresh isolated target when needed; verify before use.