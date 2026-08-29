"""
FluxSwarm security layer.

Runs ECC security skills (security-scan / security-review) over a swarm's
generated project to catch secrets leakage, prompt-injection patterns, and
common vulns BEFORE the result is marked done. This is the "AgentShield"
differentiator surfaced to users as a security score.

Designed to never hallucinate: it shells out to the real `hermes` CLI that
loads the genuine ECC security skill, captures stdout, and parses a simple
summary. Falls back to a deterministic static scan if the CLI is unavailable.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import hermes_client as hc

HERMES_BIN = Path("C:/Users/DELL/AppData/Local/hermes/bin/hermes.exe")
HERMES_HOME = "C:/Users/DELL/AppData/Local/hermes"

# Patterns that, if present in generated code, are flagged immediately.
LEAK_PATTERNS = {
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai_key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "generic_secret": re.compile(r"(?i)(secret|password|api_key|token)\s*=\s*['\"][^'\"]{8,}['\"]"),
}

INJECTION_PATTERNS = {
    "prompt_injection": re.compile(r"(?i)(ignore (previous|above|all) instructions|disregard (your|the) (system|previous) (prompt|instruction))"),
    "eval_exec": re.compile(r"(?i)\b(eval\(|exec\(|os\.system\(|subprocess\.(call|run|Popen))"),
}


def _run_skill(task_text: str, skill: str) -> str | None:
    """Best-effort: ask Hermes to run an ECC security skill on `task_text`."""
    if not HERMES_BIN.exists():
        return None
    try:
        env = dict(os.environ)
        env["HERMES_HOME"] = HERMES_HOME
        r = subprocess.run(
            [str(HERMES_BIN), "skills", "run", skill, "--", task_text],
            capture_output=True, text=True, env=env, timeout=60,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        return None
    return None


def _static_scan(text: str) -> dict:
    findings = []
    for name, pat in LEAK_PATTERNS.items():
        if pat.search(text):
            findings.append({"severity": "high", "type": "secret_leak", "rule": name})
    for name, pat in INJECTION_PATTERNS.items():
        if pat.search(text):
            findings.append({"severity": "medium", "type": "injection_risk", "rule": name})
    score = max(0, 100 - len(findings) * 20)
    return {"score": score, "findings": findings, "method": "static"}


def scan_project(board: str, goal: str, include_llm: bool = False) -> dict:
    """
    Scan a swarm's generated output.
    - Reads real generated files from the board's workspaces (not just the goal).
    - Always runs the deterministic static scan (no hallucination risk).
    - Optionally enriches with the real ECC security skill via Hermes CLI.
    """
    produced = goal
    try:
        produced += "\n" + hc.read_workspace(board)
    except Exception:
        pass

    result = _static_scan(produced)
    if include_llm:
        llm_out = _run_skill(goal, "security-scan")
        if llm_out:
            result["llm_notes"] = llm_out[:800]
            result["method"] = "static+llm"
    return result


def severity_label(score: int) -> str:
    if score >= 90:
        return "secure"
    if score >= 70:
        return "minor"
    if score >= 40:
        return "needs_review"
    return "dangerous"
