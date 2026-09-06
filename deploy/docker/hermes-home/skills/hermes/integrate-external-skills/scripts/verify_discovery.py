#!/usr/bin/env python3
"""Verify Hermes actually discovers skills under HERMES_HOME/skills.

Run from the hermes-agent dir:
    cd <hermes-agent> && HERMES_HOME=<home> ./venv/Scripts/python.exe <this-file>

Prints the total discovered SKILL.md count and the count under a given
namespace, so you can confirm an integration increased the visible total.
"""
import os
import sys
from pathlib import Path

# Point at the hermes-agent checkout that ships the loader code.
AGENT_HOME = os.environ.get("HERMES_AGENT_HOME", "C:/Users/DELL/AppData/Local/hermes/hermes-agent")
sys.path.insert(0, AGENT_HOME)

import agent.skill_utils as su  # noqa: E402

SKILLS_HOME = Path(os.environ.get("HERMES_HOME", "C:/Users/DELL/AppData/Local/hermes")) / "skills"
NAMESPACE = os.environ.get("ECC_NS", "ecc")  # change per integration


def count(root: Path) -> int:
    return len(list(su.iter_skill_index_files(root, "SKILL.md")))


def main() -> None:
    total = count(SKILLS_HOME)
    ns_dir = SKILLS_HOME / NAMESPACE
    ns = count(ns_dir) if ns_dir.exists() else 0
    print(f"TOTAL discovered skills : {total}")
    print(f"namespace '{NAMESPACE}' : {ns}")
    if ns == 0:
        print("WARNING: namespace not discovered — check the path you copied into.")


if __name__ == "__main__":
    main()
