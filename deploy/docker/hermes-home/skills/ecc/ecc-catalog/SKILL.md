---
name: ecc-catalog
description: "Browse and use the Everything Claude Code (ECC) engineering system integrated into Hermes — 286 skills, 68 agents, 22 rule packs, hooks, and AgentShield security scanning. Use when the user wants ECC capabilities: planning, review, build repair, security, TDD, architecture, language-specific reviewers, or security scanning of agent config."
metadata:
  hermes:
    tags: [ecc, engineering, agents, skills, security, tdd, review]
    related_skills: [security-scan, security-review]
---

# ECC (Everything Claude Code) — Integrated Catalog

ECC v2.2.0 is installed under `skills/ecc/`. It gives Hermes a coordinated
engineering system: specialized agents, reusable skills, always-follow rules,
hook workflows, and AgentShield security scanning.

## Asking for an ECC capability

Just describe the task in plain language. The skills below are discovered
automatically by Hermes (e.g. "run a security scan", "review this Go code",
"set up TDD", "plan this feature"). Load any skill by name with `skill_view`.

## Layout (on disk)

- `skills/ecc/skills/` — 286 skills (auto-discovered by Hermes)
- `skills/ecc/agents/` — 68 agent definitions (reference; use as prompt templates)
- `skills/ecc/rules/` — 22 rule packs by language/framework (reference)
- `skills/ecc/hooks/` — hook definitions (reference; Claude Code schema)
- `skills/ecc/agentshield/` — AgentShield security-scan references + command

## Highlights

- **Agents** (in `agents/`): planner, architect, tdd-guide, code-reviewer,
  security-reviewer, build-error-resolver, e2e-runner, spec-miner, and
  language reviewers for Go/Python/Java/Kotlin/Rust/C++/TypeScript/Django/…
- **Skill groups**: TDD, research, security, docs, frontend, data, ML,
  operations, language-specific patterns (Rust, Go, Swift, Kotlin, PHP, …).
- **AgentShield**: `security-scan` skill audits a `.claude/` config for
  injection/misconfiguration risks; `security-review` gives security
  checklists for auth, secrets, APIs, payments.

## Name collisions handled

Two ECC skills were renamed to avoid clashing with bundled Hermes skills:
`accessibility → ecc-accessibility`, `manim-video → ecc-manim-video`.
Load them by their renamed id.

## Notes

- Hooks follow the Claude Code JSON schema and are NOT auto-loaded into Hermes
  (Hermes has its own hooks system). They are provided as reference patterns.
- Rules are reference material; copy the relevant pack into a project when you
  want always-follow guidance for a specific language/framework.
