# BYOK, token economics, and ECC AgentShield security

Companion to `hermes-kanban-swarm` SKILL.md. Captured from the FluxSwarm build
(SaaS wrapping the ECC devops squad). These are the parts that are NOT in the
base skill and that determine whether the product is *profitable* and *safe*.

## 1. Token economics — the #1 rule

A kanban swarm runs **6+ agents** per launch (4 workers + verifier + synthesizer).
Each agent consumes real LLM tokens. Two pricing models:

- **Flat-rate with YOUR keys = guaranteed loss.** If you fund the tokens and
  sell "1 credit = 1 launch", a single launch can cost $3–9 while you charged
  $1.16 (Starter: 25 launches for $29). Margin goes negative fast, especially
  with a chatty/looping builder.
- **BYOK (Bring Your Own Key) = safe margin.** The user supplies their provider
  key; *they* pay for tokens. You charge only a "coordination fee" (platform/
  dispatch/storage). Token cost leaves your P&L entirely → loss on tokens is
  impossible. This flipped FluxSwarm's margin from ~negative to ~93–96%.

**Decision rule:** for any SaaS that drives multi-agent swarms, default to
BYOK-first. Keep YOUR keys only for a tightly-capped Demo (e.g. 3 units/user).

## 2. BYOK storage + injection (verified pattern)

`vault.py` — Fernet-encrypted at rest, never return raw tokens:
```python
from cryptography.fernet import Fernet
# key from FLUXSWARM_FERNET_KEY (generate once); fall back to in-memory in dev only
def set_user_key(user_id, provider, token):
    data = _load(); data.setdefault(str(user_id), {})[provider] = _FERNET.encrypt(token.encode()).decode(); _save(data)
def get_user_key(user_id, provider):  # decrypt on use only
    ...
def mask_key(t): return t[:4] + "…" + t[-4:]   # API returns masked only
```
Never log or return the decrypted token to the client. The `/api/keys` endpoint
returns `mask_key(...)` only.

`hermes_client.py` — inject the user's key into the kanban subprocess env so
THEIR agent run bills THEIR account:
```python
ENV_MAP = {"anthropic":"ANTHROPIC_API_KEY","openai":"OPENAI_API_KEY",
           "gemini":"GEMINI_API_KEY","kimi":"KIMI_API_KEY"}
def _run(args, board=None, provider_keys=None):
    env = dict(os.environ); env["HERMES_HOME"] = HERMES_HOME
    for prov, tok in (provider_keys or {}).items():
        ev = ENV_MAP.get(prov)
        if ev and tok: env[ev] = tok
    return subprocess.run([HERMES_BIN, "kanban", *(("--board", board) if board else ()), *args],
                          capture_output=True, text=True, env=env, timeout=120)
```
Demo plan → `provider_keys = {}` (uses your capped key). Paid plan → collect
the user's keys and pass them through `launch_swarm(..., provider_keys=...)` and
`dispatch(..., provider_keys=...)`.

## 3. ECC AgentShield security scan (`security.py`)

Surface a "security score" as a product differentiator (quality guarantee).
Deterministic static scan first (no LLM = no hallucination), optionally enrich
with the real ECC skill via the CLI:

```python
LEAK = {  # regexes for secrets/injection in generated code
  "aws_key": r"AKIA[0-9A-Z]{16}",
  "private_key": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
  "openai_key": r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}",
  "anthropic_key": r"sk-ant-[A-Za-z0-9_-]{20,}",
  "injection": r"(?i)(ignore (previous|above|all) instructions|disregard (your|the) (system|previous) (prompt|instruction))",
}
def scan_project(board, goal, include_llm=False):
    text = goal
    ws = HERMES_HOME/kanban/boards/board/workspaces
    for f in ws.rglob("*.py"): text += "\n"+f.read_text(errors="ignore")[:4000]
    findings = [{"rule":k} for k,p in LEAK.items() if p.search(text)]
    score = max(0, 100 - len(findings)*20)
    if include_llm and HERMES_BIN.exists():
        out = subprocess.run([HERMES_BIN,"skills","run","security-scan","--",goal],
                             capture_output=True, text=True, timeout=60)
        if out.returncode==0 and out.stdout.strip():
            result["llm_notes"] = out.stdout[:800]; result["method"]="static+llm"
    return {"score":score,"findings":findings,"method":"static"}
```

**Innovation tip:** loop the swarm until `score >= 90` (auto-rerun reviewer+
builder) and *sell the guarantee*, not just the code. That is the moat vs
single-agent builders (Bolt/v0/Devin).

## 4. i18n for the web UI (cheap, broadens market)

Add `data-i18n="key"` to static elements, a `<script>` with `I18N={ar:{...},en:{...}}`,
and `setLang(l){ document.documentElement.dir = l==="ar"?"rtl":"ltr";
document.querySelectorAll("[data-i18n]").forEach(...); }`. AR+EN covers the two
biggest SaaS markets in MENA + West.

## 5. Remaining economic pitfalls to fix before launch (from review)
- Referral reward must fire **once** (use a `rewarded` flag; the `referrals`
  table has one but it was unused → infinite re-reward on re-subscribe).
- Plan upgrade must **add** credits, not reset to the tier's full amount
  (else users drain then "upgrade" to refill free).
- Demo launch must be **capped per IP/session** (it cost no credit and no auth
  in the first cut → unlimited free compute).
- Unit of sale should be **agent-units**, not "launches": a swarm = ~6 units.
