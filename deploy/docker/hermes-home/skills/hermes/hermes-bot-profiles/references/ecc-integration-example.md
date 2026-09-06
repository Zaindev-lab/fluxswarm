# Worked Example: Integrating ECC (Everything Claude Code) into Hermes

Verified during a real session on 2026-08-28 (Windows / Hermes Desktop).

## Source
- GitHub: `github.com/affaan-m/ECC` (attached as `ECC-main.zip`, extracted to `Downloads/ECC-main/ECC-main`).
- Version 2.2.0. Repo confirms: 68 agents, 286 skills, 94 commands, 22 rule packs.

## Step 1 — Install the skill pack into the shared skills dir
Hermes discovers skills by walking `HERMES_HOME/skills` for `SKILL.md`. Copy ECC under a namespace to avoid collisions:
```
C:/Users/DELL/AppData/Local/hermes/skills/ecc/skills/   (286 skills)
C:/Users/DELL/AppData/Local/hermes/skills/ecc/agents/   (68 agents, reference)
C:/Users/DELL/AppData/Local/hermes/skills/ecc/rules/    (22 rule packs, reference)
C:/Users/DELL/AppData/Local/hermes/skills/ecc/hooks/    (reference; not auto-loaded)
C:/Users/DELL/AppData/Local/hermes/skills/ecc/agentshield/  (security-scan refs)
C:/Users/DELL/AppData/Local/hermes/skills/ecc/ecc-catalog/SKILL.md  (index skill)
```
Collisions with bundled Hermes skills (renamed frontmatter `name:`): `accessibility`→`ecc-accessibility`, `manim-video`→`ecc-manim-video`.

Verification (Hermes's own loader):
```
python -c "import sys; sys.path.insert(0, '<hermes-agent>'); import agent.skill_utils as su; \
from pathlib import Path; print(len(list(su.iter_skill_index_files(Path('<skills>'),'SKILL.md'))))"
```
Result: global discovered skills went 83 → 370.

## Step 2 — Create role bots (each is a profile)
```
hermes profile create ecc-planner   --no-skills --no-alias --description "ECC planning bot"
hermes profile create ecc-architect --no-skills --no-alias --description "ECC architecture bot"
hermes profile create ecc-reviewer  --no-skills --no-alias --description "ECC code review bot"
hermes profile create ecc-tdd       --no-skills --no-alias --description "ECC TDD bot"
hermes profile create ecc-build-fixer --no-skills --no-alias --description "ECC build-repair bot"
hermes profile create ecc-security  --no-skills --no-alias --description "ECC security/AgentShield bot"
hermes profile create ecc-catalog   --no-skills --no-alias --description "ECC catalog/routing bot"
```

## Step 3 — Wire shared skills WITHOUT copying (critical)
For each profile dir `<HERMES_HOME>/profiles/<bot>`:
```
HERMES_HOME="<HERMES_HOME>/profiles/<bot>" \
  hermes config set skills.external_dirs "C:/Users/DELL/AppData/Local/hermes/skills/ecc/skills"
```
Then write a role persona into `profiles/<bot>/SOUL.md`.

### Why NOT a junction
A `mklink /J profiles/<bot>/skills <global skills>` looked right but `os.walk(followlinks=True)` returned only 1 SKILL.md for the sub-profile (global returned 371). `skills.external_dirs` is the supported path; `get_external_skills_dirs()` resolved all 286 correctly.

## Step 4 — User-facing examples embedded in each bot SOUL
- "راجع كود Go هذا" → loads `code-reviewer` / `go-reviewer`
- "أضف اختبارات TDD" → `tdd-guide`
- "افحص الأمان" → `security-review` or `security-scan` (AgentShield)
- "خطّط هذه الميزة" → `planner`

## Gotchas
- `hermes profile delete <name>` is interactive (type name to confirm) — not scriptable.
- `hermes config set` writes to whichever profile `HERMES_HOME` points at; always set it explicitly per command.
- `config.yaml` is security-guarded — never edit by hand; always use `hermes config set`.
- The ECC `.hermes/` adapter installs to `~/.hermes`, which is NOT this desktop app's home — use the paths above instead.
