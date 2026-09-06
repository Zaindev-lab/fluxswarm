---
name: integrate-external-skills
description: Merge a third-party skills repo into Hermes.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [skills, integration, hermes, third-party, agents, rules]
    related_skills: []
---

# Integrate an External Skills / Agent Repo into Hermes

## When to use
- User points at a repo that contains `skills/`, `agents/`, `rules/`, `hooks/` (e.g. `affaan-m/ECC`) and asks to "use these here", "add these skills", "merge into Hermes", "I want to benefit from these when building projects".
- Any "wire X's capabilities into Hermes" request.
- Cloning/downloading a skills repo and wanting it available as first-class Hermes skills.

## Key facts (verified empirically this session)
- Hermes discovers skills by **walking `HERMES_HOME/skills` for `SKILL.md`** (`os.walk`/`rglob` in `hermes-agent/agent/skill_utils.py`). `HERMES_HOME` defaults to the OS app-data dir (Windows: `C:/Users/<user>/AppData/Local/hermes`; override via `HERMES_HOME` env).
- **The external repo's OWN install target is usually WRONG for this desktop app.** ECC's `.hermes/` adapter and `scripts/lib/install-targets/hermes-home.js` install to `~/.hermes`, which this app does NOT read. Always write into `HERMES_HOME/skills/<namespace>/`, never `~/.hermes`.
- **Hooks use the Claude Code JSON schema** and must NOT be injected into Hermes config — Hermes has a different hooks system. Copy hooks as **reference only**.
- **Agents and rules** are reference material (prompt templates / always-follow guidance), not auto-discovered skills. Copy them under the namespace for browsing, not into the loader path as skills.
- Dropping a valid `SKILL.md` under the skills dir is enough; no manifest edit is required. The app rebuilds its catalog from disk.

## Steps
1. **Get the repo locally**: extract the zip, or `git clone`. Confirm `skills/` exists.
2. **Smoke-test the discovery path** before mass-copy: copy ONE skill into `HERMES_HOME/skills/<namespace>/<skill>/` and run `scripts/verify_discovery.py`. The discovered count must increase by 1. This proves you're writing to the dir Hermes actually scans.
3. **Namespace everything** under `HERMES_HOME/skills/<namespace>/` (e.g. `ecc`) so bundled Hermes skills are never overwritten.
4. **Copy skills** (`skills/` → `<namespace>/skills/`). Prefer `scripts/import_repo.py`, which auto-handles collisions.
5. **Handle name collisions**: parse each `SKILL.md` frontmatter `name:`; if it clashes with an existing discovered skill, rename the folder to `<namespace>-<name>` AND update the frontmatter `name:`. (ECC had 2 collisions: `accessibility` → `ecc-accessibility`, `manim-video` → `ecc-manim-video`.)
6. **Copy agents/rules/hooks** under the namespace as reference. Do NOT auto-load hooks.
7. **Verify discovery** with `scripts/verify_discovery.py` (uses Hermes's own `iter_skill_index_files`). The total must rise by the number of new skills. If it doesn't, you wrote to the wrong path.
8. (Optional) Add an **index skill** (`<namespace>/<namespace>-catalog/SKILL.md`) summarizing highlights so the capabilities are browsable by name.

## Pitfalls
- ❌ Don't write to `~/.hermes` (ECC's default target) — ignored by this app's loader.
- ❌ Don't inject Claude Code `hooks.json` into Hermes — wrong schema, can break the app. Reference only.
- ❌ Don't overwrite bundled skills — always namespace + collision-rename.
- ❌ Don't trust the repo's own `install.sh`/`--target hermes` path blindly; it may target the wrong home.
- ⚠️ If the vision tool fails (e.g. `401 invalid API key`) and you must read a table/screenshot in an image, fall back to **local OCR via `tesseract.js`**: `npm i tesseract.js` in an empty dir, upscale the PNG with `jimp` (×2–3), run with lang `ara+eng` and a digit whitelist to isolate embedded numbers. Imperfect for Arabic layout tables but recovers figures like "68 / 286 / 94".

## Verification
Always re-run discovery counting after the copy. A real integration shows the count go up; a silent failure (wrong path) leaves it unchanged. Use `scripts/verify_discovery.py` so the number matches what the app sees — not a raw `rglob` glob count.

## Support files
- `scripts/import_repo.py` — parameterized copy of `skills/agents/rules/hooks` with automatic name-collision rename.
- `scripts/verify_discovery.py` — counts discovered skills via Hermes's own loader.
- `references/discovery-mechanics.md` — empirical notes on how Hermes resolves `HERMES_HOME` and scans skills.
