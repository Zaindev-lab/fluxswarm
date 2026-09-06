# Hermes Skill Discovery — Mechanics (empirical)

Observed while integrating ECC 2.2.0 into a Windows Hermes desktop install.

## Where skills live
- `HERMES_HOME/skills` is the scan root. `HERMES_HOME` defaults to the OS
  app-data dir; on Windows: `C:/Users/<user>/AppData/Local/hermes`. Override
  with the `HERMES_HOME` env var (the loader reads `hermes_constants.get_hermes_home()`).
- Any directory under `skills/` that contains a `SKILL.md` is a discoverable
  skill. Nested `SKILL.md` files inside a skill's `references/`, `templates/`,
  `scripts/` are treated as data, not as separate skills.

## How it scans
- `agent/skill_utils.py` -> `iter_skill_index_files()` walks the tree with
  `os.walk(..., followlinks=True)` and yields every `SKILL.md`. There is NO
  manifest that must be edited; the catalog is rebuilt from disk. (`.bundled_manifest`
  and `.skills_prompt_snapshot.json` exist but are not the scan input.)
- Discovery does not require a restart if the loader re-runs per session; new
  `SKILL.md` files appear on the next catalog build.

## Why `~/.hermes` is the wrong target
- External harness repos (ECC, etc.) ship adapters that install to `~/.hermes`
  (e.g. `scripts/lib/install-targets/hermes-home.js` uses `rootSegments: ['.hermes']`).
  This desktop app does NOT read `~/.hermes`; its home is the app-data `hermes`
  dir. Always copy into `HERMES_HOME/skills/<namespace>/`.

## Verification shortcut
- Count discovered skills before and after with the loader itself:
  `len(list(su.iter_skill_index_files(HERMES_HOME/"skills", "SKILL.md")))`.
  A correct integration raises the total; a wrong path leaves it unchanged.

## Frontmatter fields Hermes reads
- `name` (required, must be unique across all discovered skills).
- `description` (used for routing/trigger matching).
- `metadata.hermes.tags`, `metadata.hermes.related_skills` (optional, nice to have).
- `platforms` / `environment` filters exist in the loader; ECC skills carried
  none, so all 286 imported cleanly. If a third-party skill sets these, confirm
  it won't be filtered out in your environment.
