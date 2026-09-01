# PHASE 27–28 — GITHUB / SOURCE PUBLISHING PREP

## Repository hygiene (VERIFIED)
- `.gitignore` covers secrets: `.env*`, `backend/data/`, `*.db*`, backups,
  `hermes_home/`, `pyerr.txt`, `__pycache__` → nothing sensitive can be committed.
- Secret scan: no `PK-`/live tokens in tree (CLAIM-VERIFICATION + code review);
  vault/keys never persisted to repo.
- `ci.yml` runs unit tests on `master` (FIX-7 fixed branch).
- Docs present: `README.md` (product+run), `DEPLOY.md`, `PADDLE_LIVE_CHECKLIST.md`,
  `GTM_LAUNCH_PLAN.md`.

## Publishing state
- Git repo: no remote configured → **push BLOCKED** (no URL, no auth in scope).
- `audit/reports/` deliberately **untracked** (kept out of the public repo until
  the audit and ECC-license posture stabilize → avoid publishing a half-finished
  commercial audit or license ambiguity).
- Once remote is added: push trusted files only; keep `.env`-dependents excluded;
  add `NOTICES` with the ECC/Hermes attribution (post license review — 03).

## Prep checklist (ready when user says go)
1. Add remote + `gh auth`; push `master`.
2. Create `NOTICES` (ECC/Hermes license per 03 outcome) before first public push.
3. Publish `audit/reports` to a private repo or after FINAL-AUDIT is sealed.
4. Add `pip-audit` step to `ci.yml` (DEPS-1).
5. Tag `v0.9.0-prelaunch` after FINAL-AUDIT + regression (P30).

## Blockers
Remote/auth external; ECC license review external; otherwise ready.