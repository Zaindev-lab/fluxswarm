#!/usr/bin/env python3
"""Verify Hermes discovers skills for a given profile (set HERMES_HOME first).
Usage: HERMES_HOME=/path/to/profiles/<bot> python verify_skills.py
"""
import os
import sys
import traceback
from pathlib import Path

# Point at the agent source so we can reuse Hermes's own loader.
AGENT = Path(os.environ.get("HERMES_AGENT_SRC",
                            "C:/Users/DELL/AppData/Local/hermes/hermes-agent"))
HERMES_HOME = os.environ.get("HERMES_HOME")
if not HERMES_HOME:
    print("ERROR: set HERMES_HOME to the profile dir first", file=sys.stderr)
    sys.exit(2)
sys.path.insert(0, str(AGENT))
import agent.skill_utils as su

home = Path(HERMES_HOME)
skills_dir = home / "skills"
found = list(su.iter_skill_index_files(skills_dir, "SKILL.md"))
ext = su.get_external_skills_dirs()
ext_count = sum(len(list(su.iter_skill_index_files(d, "SKILL.md"))) for d in ext)
names = set()
for p in found:
    try:
        fm, _ = su.parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        names.add(fm.get("name"))
    except Exception:
        pass
print(f"profile skills dir: {skills_dir} (exists={skills_dir.exists()})")
print(f"local SKILL.md found: {len(found)}")
print(f"external_dirs resolved: {ext}")
print(f"ECC/3rd-party skills via external_dirs: {ext_count}")
probes = sys.argv[1:] or ["ecc-catalog", "security-review", "tdd-guide"]
for pr in probes:
    print(f"  probe {pr!r}: {'YES' if pr in names else 'no (check external_dirs)'}")
