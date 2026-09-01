# GATE-4 PROVIDER CONFIRMATION

**Status:** COMPLETE (read-only experiments; NO code changes)
**Date:** 2026-08-31 ~12:30
**Authorization:** Read-only provider probe + single minimal worker confirmation experiment ONLY.

---

## ROOT CAUSE STATUS: **CONFIRMED**
The failed completion path was caused by the **free LLM provider being transiently overloaded/unreachable** during the Run-C window (Aug 30 22:44 → Aug 31 ~11:27). With the same provider now responsive, **a single Hermes worker completes the full lifecycle in 130s** — proving the code path (spawn → agent → kanban_complete → SQLite → exit) is healthy. There is **no Hermes/ECC/dispatcher defect** behind the Run-C stall.

---

## EXPERIMENT A — DIRECT PROVIDER PROBE

**Target:** provider `opencode-free` / model `nemotron-3-ultra-free` / endpoint `https://opencode.ai/zen/v1`

### Part A1 — Raw `urllib` probe (rejected; NOT representative)
| Check | Result |
|---|---|
| DNS | OK — `opencode.ai` → 172.65.90.20-23 (0.021s) |
| TLS | OK — TLS1.3 AES256-GCM (0.537s) |
| HTTP (3 requests) | **403 Forbidden `error code: 1010`** |
| Streaming | **403** |
| Latency | 0.8s |

**Interpretation:** 403 error 1010 is a **Cloudflare edge block** triggered by the raw `urllib` client's TLS/browser signature. **Not representative** of what Hermes experiences.

### Part A2 — OpenAI-SDK with arbitrary fake key (rejected; NOT representative)
| Check | Result |
|---|---|
| HTTP (3 req) | **401** `AuthError: "Invalid API key"` |
| Streaming | **401** |
| Latency | 0.3-2.0s |

**Interpretation:** The endpoint is reachable but **401s any unknown bearer**. Hermes's keyless path avoids this by sending an **empty `Authorization` header** (see Part A3).

### Part A3 — Faithful Hermes client replication (REPRESENTATIVE) — **PASS**
Replicated Hermes's exact keyless client per source (`hermes_cli/models.py:5441-5481`, `agent/agent_init.py:1313-1325`):
- `api_key = "opencode-zen-free-keyless"` (placeholder)
- `default_headers = {"Authorization": "", "HTTP-Referer": "https://hermes-agent.nousresearch.com", "X-Title": "Hermes Agent", "User-Agent": "HermesAgent/0.20.6"}`

| Check | Result |
|---|---|
| HTTP req1 (stream=False) | **200 OK**, content `'OK'`, latency **14.88s** |
| HTTP req2 (stream=False) | **200 OK**, latency **13.93s** |
| HTTP req3 (stream=False) | **200 OK**, latency **12.51s** |
| Streaming (stream=True) | **200 OK**, first chunk **14.34s**, done 15.08s, 7 chunks |
| Repeat minimal requests | **4/4 success** |

**Provider response class: OK_200.** Provider is **currently reachable and functioning**, though time-to-first-token is slow (~13-15s).

### Experiment A verdict: **PASS**
- Ruled out DNS/TLS (fine), auth (Hermes bypasses 401 via empty Authorization), connectivity (reachable).
- Confirmed the exact method difference: raw urllib → Cloudflare 403; fake key → 401; **Hermes's keyless headers → 200**.
- Did **not** declare health from a single call — 3 non-stream + 1 stream, all 200.

---

## EXPERIMENT B — SINGLE MINIMAL HERMES WORKER — **PASS**

**Method (faithful to production spawn path):**
- Isolated board: `gate4-probe-1788175283` (NOT the production Run-C board `u1-1788126303-64ec9946`).
- One task: `t_e31b2f2a`, assignee `ecc-planner`, pinned **same provider/model as Run-C** (`nemotron-3-ultra-free` / `opencode-free`), `--max-runtime 900` (Experiment C guard — no indefinite wait).
- Trivial deterministic task: "Create probe.txt containing exactly `PROBE_OK`, then complete."
- Spawned via Hermes's **production `_default_spawn`** (kanban_db.py L10720), same Hermes binary, same `HERMES_HOME`, same ECC profile.

**Observed lifecycle (verified covers THE WHOLE path):**
| Stage | Evidence |
|---|---|
| spawn | `spawned worker pid = 15892`; task `ready→running`; run 1 created |
| model response | worker log: successive tool turns; provider responded (200) |
| agent action | `write_file` created `probe.txt` = `PROBE_OK` (diff `+PROBE_OK`) |
| verify | `read_file` re-read probe.txt |
| decision | log: "Now I can complete the task" |
| **kanban_complete** | log: "preparing kanban_complete" |
| **SQLite transition** | `task_events` → **`completed`** at 12:23:36; status `running→done` |
| worker exit | process tree 15892→8024→2076 **clean exit** (verified gone) |

**Elapsed time: 130 seconds.**

Final state: task `t_e31b2f2a` = **`done`**; `task_events` contains terminal `completed` transition; `task_runs` run 1 = `done` with `started_at`/`ended_at`.

### Experiment B verdict: **PASS**
A single Hermes worker using the exact Run-C provider/model/etc. configuration **complete the full lifecycle** and reaches SQLite `done`.

---

## EXPERIMENT C — FAILURE HANDLING OBSERVATION — **N/A (not triggered)**
The provider stayed up throughout both experiments; no forced-outage failure path was exercised. The `--max-runtime 900` guard was the only defensive measure and was not reached (worker finished in 130s). No indefinite wait occurred.

---

## ANSWERS TO THE AUTHORIZATION QUESTIONS (anti-hallucination)
| Question | Answer |
|---|---|
| A. Provider currently works? | **YES** — 4/4 minimal requests returned 200 via Hermes's keyless client (Part A3). |
| B. Can a single Hermes worker complete? | **YES** — Experiment B completed in 130s and wrote the terminal transition. |
| C. Does completion reach SQLite? | **YES** — `task_events` `completed`, task status `done`, run record `done`. |

**Not claimed:** provider "healthy" beyond this window; Hermes globally healthy; FluxSwarm production-ready; P0 fixed. This experiment only proves (A)/(B)/(C) hold **right now** with the provider up.

---

## REJECTED / REFINED HYPOTHESES AFTER THIS EXPERIMENT
| Hypothesis | Verdict |
|---|---|
| Hermes web_server event-loop stall blocks completion | **REJECTED** (unchanged) — worker completion is a direct per-process SQLite write; Exp B completed via a separate worker process independent of the gateway loop. |
| Dispatcher / timeout / ECC bug froze workers | **REJECTED** — Exp B used the same spawn path and completed cleanly. |
| Provider permanently down | **REJECTED** — provider responded 200 in Exp A3 and Exp B. |
| **Root cause** = transient provider outage during Run-C | **CONFIRMED** — all four Run-C worker logs show the same 502/APIConnectionError during that window; same config completes now. |

---

## RESIDUAL NOTES / NEXT STEPS (not authorized — for later)
- Run-C board `u1-1788126303-64ec9946` still shows `running=4` — a **reclaim** of those dead-PID `running` rows (via `detect_crashed_workers` / a fresh dispatch pass, or explicit completion) is a remediation action that requires **separate authorization**.
- The provider's slow time-to-first-token (~13-15s) and its volatility (502 overload windows) are external; resilience (fallback providers) was explicitly deferred ("Do NOT add fallback logic yet").
- No production code, Hermes, ECC, dispatcher, timeout, prompt, pricing, or billing was changed.

---

## STOP
Experiments complete. **No code was changed.** Awaiting authorization before any remediation or further action.
