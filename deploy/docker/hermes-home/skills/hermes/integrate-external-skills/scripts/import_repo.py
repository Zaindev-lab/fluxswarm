#!/usr/bin/env python3
"""Import a third-party skills/agents/rules/hooks repo into Hermes, namespaced.

Handles the trap that two skills may share a `name:` with existing Hermes
skills: the conflicting folder is renamed to `<namespace>-<name>` and its
frontmatter `name:` is rewritten so Hermes sees a unique skill.

Usage:
    python import_repo.py --src <extracted-repo> --ns <namespace> \
        --home "C:/Users/DELL/AppData/Local/hermes" [--no-agents] [--no-rules]

Default src points at the ECC 2.2.0 extracted tree used in the reference run.
"""
import argparse
import re
import shutil
from pathlib import Path

import agent.skill_utils as su

AGENT_HOME = "C:/Users/DELL/AppData/Local/hermes/hermes-agent"
sys = __import__("sys")
sys.path.insert(0, AGENT_HOME)


def existing_names(home_skills: Path) -> set:
    names = set()
    for p in su.iter_skill_index_files(home_skills, "SKILL.md"):
        try:
            fm, _ = su.parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
            if fm.get("name"):
                names.add(fm["name"])
        except Exception:
            pass
    return names


def copy_tree(src: Path, dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def import_skills(src_skills: Path, dst_skills: Path, ns: str, existing: set):
    dst_skills.mkdir(parents=True, exist_ok=True)
    collided = []
    for d in sorted(src_skills.iterdir()):
        if not d.is_dir():
            continue
        # detect this skill's frontmatter name
        name = d.name
        smd = d / "SKILL.md"
        if smd.exists():
            try:
                fm, _ = su.parse_frontmatter(smd.read_text(encoding="utf-8", errors="ignore"))
                name = fm.get("name", d.name)
            except Exception:
                pass
        newname = f"{ns}-{name}" if name in existing else d.name
        tgt = dst_skills / newname
        copy_tree(d, tgt)
        if name in existing:
            existing.add(newname)
            collided.append((name, newname))
            smd2 = tgt / "SKILL.md"
            if smd2.exists():
                txt = smd2.read_text(encoding="utf-8", errors="ignore")
                txt = re.sub(r"(?m)^name:\s*.*$", f"name: {newname}", txt, count=1)
                smd2.write_text(txt, encoding="utf-8")
    return collided


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="C:/Users/DELL/Downloads/ECC-main/ECC-main")
    ap.add_argument("--ns", default="ecc")
    ap.add_argument("--home", default="C:/Users/DELL/AppData/Local/hermes")
    ap.add_argument("--no-agents", action="store_true")
    ap.add_argument("--no-rules", action="store_true")
    ap.add_argument("--no-hooks", action="store_true")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.home) / "skills" / args.ns
    home_skills = Path(args.home) / "skills"

    existing = existing_names(home_skills)
    before = len(list(su.iter_skill_index_files(home_skills, "SKILL.md")))

    collided = import_skills(src / "skills", dst / "skills", args.ns, existing)
    if not args.no_agents:
        copy_tree(src / "agents", dst / "agents")
    if not args.no_rules:
        copy_tree(src / "rules", dst / "rules")
    if not args.no_hooks:
        copy_tree(src / "hooks", dst / "hooks")

    after = len(list(su.iter_skill_index_files(home_skills, "SKILL.md")))
    print(f"skills copied: {len(list((dst/'skills').iterdir()))}")
    print(f"name collisions renamed: {len(collided)}")
    for old, new in collided:
        print(f"  {old} -> {new}")
    print(f"discovered total: {before} -> {after} (delta {after-before})")


if __name__ == "__main__":
    main()
