# GATE-4 — PROVIDER ENDPOINT VERIFICATION

Status: **READ-ONLY VERIFICATION COMPLETE**
Date: 2026-08-31 (UTC epoch ~17881807xx)
Scope: Confirm the EXACT configured inference endpoint for our Hermes/OpenCode `opencode-free` / Nemotron integration, and whether the inference service is actually available.

> Authorization boundary: NO production code, NO Hermes, NO ECC, NO billing, NO resilience logic changed. This is a read-only verification. Gate 4 remains OPEN/PENDING pending the 3 fresh success E2Es.

---

## 1. Exact base URL (from installed config/source)

`hermes_cli/auth.py` `PROVIDER_REGISTRY["opencode-free"]` (L487-496):

```python
"opencode-free": ProviderConfig(
    id="opencode-free",
    name="OpenCode Free",
    auth_type="api_key",
    inference_base_url="https://opencode.ai/zen/v1",
    api_key_env_vars=(),          # keyless; no secret to configure
)
```

- Base URL: **`https://opencode.ai/zen/v1`**
- `hermes_cli/models.py` L5442 `_OPENCODE_ZEN_FREE_BASE_URL = "https://opencode.ai/zen/v1"` confirms.

---

## 2. Exact inference path

For `chat_completions` API mode, the OpenAI SDK appends `/chat/completions` to the base URL.

- `hermes_cli/models.py` `opencode_model_api_mode()` (L5524-5588): `nemotron-3-ultra-free` is not claude/gpt-/grok/muse-spark/qwen → returns **`chat_completions`** (L5586).
- `hermes_cli/models.py` `normalize_opencode_base_url()` (L5591-5633): URL already ends in `/v1` → unchanged for `chat_completions`.
- `hermes_cli/auth.py` L822 confirms the OpenAI-compatible convention `POST {base_url}/chat/completions`.

**Actual inference endpoint: `https://opencode.ai/zen/v1/chat/completions`**

---

## 3. HTTP method

**POST** (OpenAI Chat Completions). Confirmed by `auth.py` L821 `httpx.post(...)` and by live probe below.

---

## 4. Required headers (keyless)

`hermes_cli/models.py` `opencode_zen_free_headers()` (L5464-5481):

```python
{
    "Authorization": "",                                  # overrides SDK Bearer; anonymous
    "HTTP-Referer": "https://hermes-agent.nousresearch.com",
    "X-Title": "Hermes Agent",
    "User-Agent": "HermesAgent/0.20.6",
    # plus Content-Type: application/json added by the client
}
```

---

## 5. Authentication method

**KEYLESS / anonymous.** `api_key_env_vars=()` (auth.py L495) — the free tier is served anonymously; any unknown bearer is rejected (401). `models.py` L5467-5468 documents that `Authorization: ""` prevents the placeholder key from ever reaching the wire, and the relay 401s any unknown bearer. **No API key is required or configured.**

---

## 6. Model ID

**`nemotron-3-ultra-free`** — `FREE_MODEL` in FluxSwarm backend; listed in Hermes catalog `_PROVIDER_MODELS["opencode-free"]` (models.py L578) and `_PROVIDER_MODELS["opencode-zen"]` (L563). `FREE_PROVIDER = "opencode-free"`.

---

## 7. Is the current configuration still valid?

**YES — CONFIGURATION IS CURRENT, NOT OUTDATED.**

Hash-compared against the live official OpenCode Zen documentation (`https://opencode.ai/docs/zen/`, last updated 2026-08-30):

| Doc entry | Doc value (2026-08-30) | Our config | Match |
|---|---|---|---|
| Nemotron 3 Ultra Free endpoint | `https://opencode.ai/zen/v1/chat/completions` | `https://opencode.ai/zen/v1` + SDK `/chat/completions` | **MATCH** |
| Nemotron 3 Ultra Free price | Free (keyless free tier) | keyless, no key configured | **MATCH** |
| Model ID | `nemotron-3-ultra-free` | `nemotron-3-ultra-free` | **MATCH** |
| API surface | `@ai-sdk/openai-compatible` (chat/completions) | `chat_completions` api_mode | **MATCH** |

The Doc-tabulated endpoint for Nemotron is **`/zen/v1/chat/completions`**, NOT the bare `/zen/v1` (which is only a marketing-site base route and 404s by design). Our earlier monitor probed the bare `/zen/v1` and misinterpreted that 404 as an inference outage — **that probe was invalid.**

---

## 8. Endpoint status code classification

Live probes against `https://opencode.ai/zen/v1/chat/completions` (POST, exact keyless headers, model `nemotron-3-ultra-free`, 2026-08-31):

Probe | HTTP status | Body
|---|---|---|
| 0 | **200** | `{"error":{"type":"server_error","message":"Error from provider (Console): Upstream request failed: [502] Upstream error from Nvidia: Service temporarily overloaded"}}` — a *transient overload* from the Nvidia upstream, returned as an HTTP-200 JSON error |
| 1 | **200** | Real completion `gen-1788180702-IEars9sGGBfhzAuWSsXa` (content "p") |
| 2 | **200** | Real completion `gen-1788180715-ZcPTxr960Mp30uH4oHwI` (content: "The user wants me to reply...") |
| 3 | **200** | Real completion `gen-1788180729-dgGPd6EMjHw3oW9sYrwq` (content: "The user is asking...") |

- The endpoint itself returns **HTTP 200** (reachable; the API route works). No 401 (keyless auth valid), no 404 (path correct), no 429 from the relay for our honest attribution.
- One probe hit a **transient Nvidia 502 overload** (wrapped in HTTP 200 JSON) — this is the SAME failure class as the original Run-C root cause (`Upstream error from Nvidia: Service temporarily overloaded`). It is intermittent, not a hard outage.

---

## 9. Does a minimal inference request succeed?

**YES.** Probes 1-3 returned genuine `chat.completion` responses from `nemotron-3-ultra-free` with a completion ID, model, assistant content, and `finish_reason`. The service is functional.

---

## Conclusion

- **CONFIGURATION OUTDATED: NO.** The `opencode-free` / `nemotron-3-ultra-free` integration uses `https://opencode.ai/zen/v1/chat/completions`, matching the current official OpenCode Zen documentation exactly. Keyless (no API key) remains valid.
- **Provider availability:** the inference endpoint is UP and serving; occasional transient Nvidia 502 overloads occur (matching the original root cause class). The earlier "provider down" conclusion was an artifact of probing the bare `/zen/v1` base route.
- **Blocked-by-provider status: RESOLVED.** The 3 fresh success E2Es are no longer blocked on an outage — they are blocked only on the driver, which was poll-scripted against the wrong `/zen/v1` path and therefore will not fire.

Per explicit instruction, the 3 fresh E2Es were **NOT run** in this step. Awaiting go-ahead to run them against the corrected endpoint.

No production code, Hermes, ECC, billing, or resilience logic was modified. Gate 4 remains **OPEN/PENDING**.
