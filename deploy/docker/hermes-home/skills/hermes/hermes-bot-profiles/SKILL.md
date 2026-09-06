---
name: hermes-bot-profiles
description: "Create Hermes bot profiles and wire external skill packs."
metadata:
  hermes:
    tags: [hermes, bots, profiles, skills, integration, ecc]
---

# Hermes Bots are Profiles

## When to use
- The user asks to "add bots" to the Hermes Desktop **Bots** pane, or to create project/role-specific helper agents.
- You are integrating a third-party skill system (e.g. ECC / Everything Claude Code) so its skills, agents, and rules become usable inside Hermes.
- You need to expose a specific skill directory to one profile without duplicating 286 folders × N bots.
- You must wire `skills.external_dirs` into any Hermes profile.

## Key fact (verified this session)
In Hermes Desktop, the left **Bots** pane lists **one row per Hermes profile**. "New Bot" literally runs `hermes profile create`. A bot's identity = its profile directory: its own `SOUL.md` (persona), `config.yaml`, `skills/`, `memories/`, `sessions/`. So "adding a bot" == creating a profile and pointing it at the right skills.

## Workflow
1. **Create the bot profile** (official CLI):
   ```
   hermes profile create <name> --no-skills --no-alias --description "<role summary>"
   ```
   - Omit `--no-skills` only if the bot should inherit the active profile's bundled skills. For an external-only skill pack, keep `--no-skills`.
   - Each profile lands at `HERMES_HOME/profiles/<name>/`.
2. **Expose the skill pack with zero duplication** via the supported key `skills.external_dirs`, set through the CLI (the config file is security-guarded — never edit `config.yaml` by hand):
   ```
   HERMES_HOME="<profile dir>" hermes config set skills.external_dirs "C:/abs/path/to/skills"
   ```
   - `skills.external_dirs` accepts a string or list; paths are expanded (`~`, `${VAR}`) and resolved relative to `HERMES_HOME`. Only existing dirs are used.
   - This is the verified path: `agent.skill_utils.get_external_skills_dirs()` resolves all skills under that dir during every scan.
3. **Write a tailored persona** into `<profile>/SOUL.md` (and a good `--description`) so the bot shows a useful role in the pane.
4. **Verify discovery** with the probe in `scripts/verify_skills.py` (set `HERMES_HOME` to the profile dir).

## Multi-bot squad (kanban `swarm`)
After creating several bot profiles, make them build **one project together**
as a parallel swarm: workers run concurrently -> a verifier checks outputs ->
a synthesizer assembles the final result. Use Hermes **kanban**, NOT
`delegate_task` (subagents can't share a board/project workspace). Full
verified command sequence and pitfalls in `references/kanban-swarm-orchestration.md`.
Key gotchas: wrap each `--worker "profile:Title:skill1,skill2"` in **one quoted
string** (spaces break argparse); create/switch a board first or cards land on
`default`; `python3` is absent on this host — use `python` for the verify script;
verify the pack via the `ECC/3rd-party skills via external_dirs:` line, not the
local probe. Pause a live swarm with `hermes kanban dispatch --max 0` (reclaims
to `ready`, keeps cards).

## Pitfalls (learned the hard way)
- **Do NOT junction/symlink a profile's `skills/` dir** to the global skills dir. `os.walk(followlinks=True)` only traversed **1** entry in this environment — the junction broke skill discovery for the sub-profile even though the global walk found 371. Use `skills.external_dirs` instead.
- **`hermes profile delete <name>` is interactive** (it prints a warning and asks you to type the name to confirm). It cannot be run headless/non-interactively — delete via the Desktop UI or accept the prompt; do not script it.
- **Name collisions:** when merging a third-party pack, check each skill's `name:` frontmatter against existing Hermes skills. Collisions found with ECC: `accessibility`, `manim-video`. Fix by namespacing the folder (e.g. `skills/ecc/`) **and** editing the colliding skill's frontmatter `name:` (e.g. `ecc-accessibility`).
- **`hermes profile show` skill count can lag** the real walk (cached `.bundled_manifest`). Trust `iter_skill_index_files()` over the banner number.
- **HERMES_HOME leakage:** if you set `HERMES_HOME` in one shell call, later calls in the same shell inherit it. Always set `HERMES_HOME` explicitly per command when targeting a specific profile, or `hermes config set` will write to the wrong profile's config.

## Verify
Run `scripts/verify_skills.py` with `HERMES_HOME` pointed at the profile dir; it prints the discovered `SKILL.md` count, probes for named skills, and reports `external_dirs` resolution.

## Related
- `hermes-agent` skill — **bundled/protected**; core agent internals. Do not patch.
- Desktop plugin `hermes-bots` (`apps/desktop/src/plugins/hermes-bots/plugin.js`) — the UI that renders the Bots pane.
- Concrete worked example (ECC integration, counts, commands): `references/ecc-integration-example.md`.
