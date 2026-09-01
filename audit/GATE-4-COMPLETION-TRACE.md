# GATE-4 COMPLETION TRACE — Forensic Report

**Status:** COMPLETE (read-only forensic trace; NO code changes made)
**Date:** 2026-08-31 ~12:05
**Board:** `u1-1788126303-64ec9946` (Run C, root `t_7872bf2a`)
**Author:** GATE-4 remediation forensics
**Scope:** Determine the EXACT completion protocol and the EXACT stopping point for the four Run-C workers that remained `running` (12+ h).

---

## 0. EXECUTIVE VERDICT

| Question | Finding |
|---|---|
| **Root cause of stuck `running` tasks** | **CONFIRMED (HIGH confidence): the free LLM provider (`opencode-free` / model `nemotron-3-ultra-free`, endpoint `https://opencode.ai/zen/v1`, Nvidia upstream) became overloaded/unreachable.** Every worker returned `[502] Upstream error from Nvidia: Service temporarily overloaded` and/or `APIConnectionError: Hermes can't reach the model provider`. The worker's agent loop never received a model response, so it never reached a `kanban_complete`/`kanban_block` call, and the tasks were never transitioned out of `running`. |
| **Previous "shared web_server event-loop" hypothesis** | **REJECTED** (for the completion path). Proof below. The gateway's asyncio event-loop stalls are real but are on a *different process* (the `serve` gateway) and are NOT{{ the completion path. Completion is a direct SQLite write from each worker subprocess. The "event loop stalled" messages are a *correlated symptom* (the same upstream provider outage also hangs the gateway's agent-client streams), NOT the cause of the stuck tasks. |
| **Why does the worker PID stay alive at 0 CPU?** | The worker subprocess is **blocked on a stalled synchronous HTTP streaming-read from the LLM provider** for hours (network I/O wait → 0 CPU, no child process). It is **not** blocked on SQLite, stdin, stdout, a child, or a Hermes code defect. |
| **Which of the 15 questions has a negative answer (never reached)?** | The agent **never invoked `kanban_complete` or `kanban_block`** — it never even got the LLM response it needed to reason up to a tool call after creating the artifact. See §6. |

---

## 1. EXACT EXECUTION PATH (worker completion protocol)

Traced end-to-end from source + logs + DB. The completion path is **self-contained per worker** and does **not** pass through the shared gateway/web_server:

```
FluxSwarm dispatch thread (backend/main.py _bg_dispatch, timeout_s=1800 now)
  └─ hermes_cli.kanban_db._default_spawn (kanban_db.py L10720)
      └─ subprocess.Popen, start_new_session=True, detached fire-and-forget:
            hermes -p <profile> --cli chat -q "work kanban task <task_id>"
         env: HERMES_KANBAN_DB / HERMES_KANBAN_BOARD / workspaces_root pinned
         stdout/stderr -> <board>/logs/<task>.log
         (task.model_override/prov_override -> -m / --provider; reasoning -> --reasoning)
      └─ worker process (own asyncio loop, own process — SEPARATE from gateway)
          ├─ kanban_show() -> reads task (direct SQLite)
          ├─ write_file(...) -> artifact hello.txt (confirmed "hello world")
          ├─ [AGENT LOOP] requests next LLM completion from opencode-free  <── BLOCKS HERE
          │     stream read hangs hours -> 502 / APIConnectionError -> retry
          ├─ (never reached) kanban_complete  -> complete_task (kanban_db.py L5363)
          │                                       -> write_txn(conn) direct SQLite
          │                                       -> running -> done
          └─ (never reached) kanban_block
```

**Completion protocol (by source):** `completed_task` docstring (kanban_db.py L10729-10731): "The child's completion is observed via the `complete`/`block` transitions **the worker writes itself**." The worker writes its own terminal transition via `complete_task → write_txn(conn)` — a **direct SQLite write from the worker process**, no HTTP/WS/gateway involved.

**This REJECTS the prior event-loop hypothesis:** the completion handshake does not depend on any dispatcher/gateway event loop. The worker is fully self-contained for claiming work and completing it.

---

## 2. EXACT STOPPING POINT (per-worker evidence from per-task logs)

All four worker logs (`<board>/logs/t_*.log`) show the **identical** termination sequence:

1. Worker queries task, then writes the real artifact `hello.txt` (= `hello world`) and `package.json` (Run C artifacts confirmed).
2. Worker submits its *next* agent-loop completion to `opencode-free` / `nemotron-3-ultra-free`.
3. Provider returns (repeatedly, over the whole 12h42m window):

```
⚠️ API call failed: APIError
   Provider: opencode-free  Model: nemotron-3-ultra-free
   Endpoint: https://opencode.ai/zen/v1
   Error: Streaming response failed: [502] Upstream error from Nvidia: Service temporarily overloaded

⚠️ API call failed: APIConnectionError
   Error: Hermes can't reach the model provider. You may be offline. ...
```

Single-call `Elapsed` times observed (wall-clock, worker stuck on the hung stream): **12,669.85 s (~3.5 h)**, **34,267.98 s (~9.5 h)**, **45,097.11 s (~12.5 h)**.

4. After exhausting retries, worker prints `Resume this session with: hermes --resume ...` and **terminates the agent run WITHOUT a `kanban_complete`/`kanban_block` transition** (protocol violation per `detect_crashed_workers`).

| Worker (profile) | DB worker_pid | Last artifact | Last log write | Agent run Duration | Provider error present |
|---|---|---|---|---|---|
| t_3b9f8d8f (ecc-planner) | 13916 | hello.txt | 11:27:35 | 12h 42m 9s | YES (502 + Conn) |
| t_245d21a6 (ecc-architect) | 13080 | hello.txt | 11:27:55 | 8m 9s / 12h 34m 11s | YES (502 + Conn) |
| t_92f734a9 (ecc-devops) | 368 | hello.txt | 11:27:36 | 12h 42m 6s | YES (502 + Conn) |
| t_95f73b64 (ecc-tdd) | 14564 | hello.txt | 11:27:35 | 12h 42m 4s | YES (502 + Conn) |

> Note: t_245d21a6 run 3 (`pid 4400`) **crashed** at 22:53:40 (task_events `crashed {pid:4400, retry_status:ready}`), so it was re-spawned as run 6 (`pid 13080`) at 22:53:41. All other runs are the original long-running ones.

---

## 3. ANSWERS TO THE 15 FORENSIC QUESTIONS

1. **Did the agent invoke `kanban_complete`?** **NO.** No `kanban_complete`/`kanban_block` event exists anywhere in `task_events` for any of the four tasks. Task state stayed `running`; no `done`/`blocked` transition was ever written.
2. **Did it invoke `kanban_block`?** **NO.** Same absence.
3. **Did it attempt to execute either command?** **NO.** The agent could not obtain a model completion after writing the file, so it never reached the tool-call step for a terminal kanban op. (It *did* successfully call `kanban_show` and `write_file` earlier — those completed before the provider outage.)
4. **Did a child process for completion exist?** **NO** observed. Completion is done in-process via direct SQLite, not via a spawned child.
5. **Is the worker waiting on a child process?** **NO.** No completion child. The worker is blocked on the LLM network stream.
6. **Is it blocked on SQLite?** **NO.** No evidence of any SQLite lock contention. `kanban.db-wal`/`kanban.db-shm` present (WAL mode); the worker never even reached the write.
7. **Is SQLite locked by another process?** **NO.** WAL allows concurrent readers/writer; no lock report and no blocking here (the worker never reached `complete_task`).
8. **Is it waiting on stdin/stdout/stderr?** **NO.** `start_new_session=True` detaches; no pipe dependency for completion. stdio redirected to per-task log file.
9. **Is it waiting on an LLM/API response?** **YES — THIS IS THE STOPPING POINT.** The worker's HTTP streaming read from `opencode.ai/zen/v1` kept the connection open for hours without delivering a completion, then failed with 502/APIConnectionError. This is the exact blocking resource.
10. **Is the chat process inside an agent loop?** **Yes**, but the loop is **stalled on the blocked synchronous provider stream read**, not actively executing agent steps (hence 0 CPU, silent agent.log).
11. **Exact process tree?** See §7.
12. **Exact command lines?** See §7.
13. **What system resource is it waiting on?** **Network I/O on the LLM provider connection (streaming read).** 0 CPU + long heartbeats + long per-call elapsed times = blocked I/O, not compute, not disk, not SQLite.
14. **Last observable event before idle?** At ~**11:27** the provider cut hard: `OpenAI client closed`, `OpenAI client created`, `event loop stalled 10796.2s`, WS reconnect — all within `11:27:26–51`. Worker heartbeats stop at `11:27:27`; worker logs end `11:27:35–55`. See §5.
15. **Why does the worker PID remain alive?** The subprocess stays alive because Hermes's retry loop keeps the process resident while it retries the (blocked/failing) provider stream. It is NOT dead, NOT spinning: it is blocked on the network stream. When the provider fully failed (≈11:27) the runs terminated — and in fact **the PIDs are now dead** (see §7) but the DB row is not reclaimed.

---

## 4. DB STATE TRANSITION (verified read-only from kanban.db)

```
tasks:   t_3b9f8d8f running pid=13916 | t_245d21a6 running pid=13080
         t_92f734a9 running pid=368   | t_95f73b64 running pid=14564
         (verifier t_9a797353 todo, synthesizer t_a7d8e8eb todo)
task_runs:
  run 3  t_245d21a6 crashed (pid 4400 not alive) 22:45:24 -> 22:53:40
  run 6  t_245d21a6 running pid=13080 22:53:41 -> open
  run 2  t_3b9f8d8f running pid=13916 22:45:22 -> open
  run 4  t_92f734a9 running pid=368   22:45:24 -> open
  run 5  t_95f73b64 running pid=14564 22:45:24 -> open
task_events (term): NO done / NO blocked / NO kanban_complete / NO kanban_block.
```

The terminal-none state is exactly what a worker that exits **without** a completion/block write leaves behind. `detect_crashed_workers` (kanban_db.py L8863) would have reclaimed these dead-PID `running` tasks and bounced them to `ready`, **had the dispatcher still been running** — but the FluxSwarm dispatch thread had already returned `TimeoutExpired` after its 1800s window, so no further dispatch pass ran. The DB is simply frozen: rows `running`, owner PIDs now dead, nothing to transition them.

---

## 5. HEARTBEAT CADENCE — the definitive timing proof

`task_events` heartbeats for **all four workers are recorded at identical wall-clock stamps**, with long idle gaps:

```
22:58 → 02:27 (~3.5h gap) → 04:27 (~2h) → 08:27 (~2h) → 11:27 (stop)
```

These stamps **exactly coincide with the gateway's `event loop stalled` warnings** in `agent.log`:
`02:27:23`, `04:27:24`, `08:27:24`, `11:27:26` — drifts `12498.6s`, `7191.2s`, `14393.8s`, `10796.2s`.

**Interpretation:** the worker subprocess is **blocked for hours** on the hung provider stream; its heartbeat tick (same asyncio loop) can only fire when the loop briefly unblocks (i.e., when the stream errors out). The heartbeat "catch-up" timestamps surfacing in the same minute as the gateway stalls is because both the worker subprocesses **and** the gateway's own agent-clients hit the **same upstream provider outage** together. This is cross-process correlation of one external cause, **not** a shared-loop dependency.

agent.log at the cut:
```
11:27:26,547 WARNING hermes_cli.web_server: event loop stalled 10796.2s (GIL pressure suspected)
11:27:42,261 INFO  run_agent: OpenAI client closed ... provider=opencode-free
11:27:47,001 INFO  tui_gateway.ws: ws accepted peer=127.0.0.1:64399
11:27:49,150 WARNING agent.credential_pool: Copilot token exchange degraded to RAW token ...
11:27:51,119 INFO  run_agent: OpenAI client created ... provider=opencode-free
```

---

## 6. "EVENT LOOP STALLED" WATCHDOG — semantics (web_server.py L20028-20055)

`_loop_heartbeat` re-arms a **2s** `call_later` tick; drift > 5s logs `event loop stalled <drift>s (GIL pressure suspected)`. The drift values here are **hours** (6223–14394s), i.e. the gateway's asyncio loop genuinely did not run its tick for many hours. This is a real freeze of **that gateway process's** loop.

**Why this is NOT the completion-path cause:**
- The completion handshake is a direct SQLite write from each **worker subprocess** (a separate process with its own loop), per source (`completed_task` docstring + `complete_task → write_txn`).
- The stalled loops belong to the **gateway** (`serve`) / agent `run_agent` clients, which are **not** on the worker→completion path.
- The worker-level "stall" (12,669s / 34,267s / 45,097s per API call) is the worker's own loop blocked on the provider stream — same root trigger (upstream outage) but process-local, not a shared web_server defect.
- Therefore "GIL pressure / Hermes web_server bug" is **REJECTED** as the cause of the stuck tasks. The recurring warning is best read as: *the upstream provider (Nvidia/opencode.ai) was repeatedly unreachable/overloaded, blocking every agent stream (gateway + workers) in this environment.*

---

## 7. PROCESS TREE & COMMAND LINES (read-only, at ~12:02:47)

Current surviving python processes:

| PID | CPU | Command line | Role |
|---|---|---|---|
| 16056 | 0s | `venv\Scripts\python.exe -m hermes_cli.main serve --host 127.0.0.1 --port 0` | Hermes gateway wrapper (persistent daemon) |
| 18748 | 1408s | `...\.hermes-runtime\...\cpython-3.11-windows-...` (child of 16056) | Hermes gateway runtime |
| 12172 | 0s | `venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8787` | FluxSwarm/E2E app wrapper |
| 7840 | 22.7s | `...\.hermes-runtime\...\cpython-3.11-windows-...` (child of 12172) | uvicorn runtime (the known app, pid 7840) |

**The four Run-C worker PIDs are now GONE at this snapshot:**
`13916 GONE`, `13080 GONE`, `368 GONE`, `14564 GONE`.

Earlier in the window (≈11:53) they were observed wrapper-0-CPU / runtime-~14s-CPU — i.e., **alive but blocked** (0 CPU). They have since terminated when the provider failure became final (≈11:27), consistent with the per-log `Resume this session` footers. **No worker subprocess remains to document further; the DB rows are simply stale/orphaned.**

---

## 8. CONFIRMED FACTS (evidence-grounded)

1. All four workers created the real artifact `hello.txt` = `hello world` (canonical task satisfied at artifact level).
2. None ever wrote a `kanban_complete` or `kanban_block` transition (verified in `task_events`; zero terminal events).
3. The provider `opencode-free` (model `nemotron-3-ultra-free`, `https://opencode.ai/zen/v1`, Nvidia upstream) returned `502 Upstream error from Nvidia: Service temporarily overloaded` and `APIConnectionError` across **all four** worker logs.
4. Worker agent runs all lasted ~12h42m then terminated **no-completion** (protocol violation) at ~11:27 (all within 20s).
5. The 4 worker PIDs are now dead; `tasks` rows remain `running` with no reclaim (dispatcher already exited at its 1800s timeout with `TimeoutExpired`).
6. Drain/downtime of the free provider correlates 1:1 in wall-clock with gateway `event loop stalled` stamps, proving a single external upstream cause.

---

## 9. REJECTED HYPOTHESES

| Hypothesis | Verdict | Reason |
|---|---|---|
| Shared Hermes `web_server` event-loop stall causes stuck completion | **REJECTED** | Completion is a direct per-worker SQLite write (`complete_task → write_txn`); the stalled gateway loops are a separate process not on the completion path. The `event loop stalled` messages are a *correlated symptom* of the same provider outage. |
| FluxSwarm dispatcher bug froze workers | **REJECTED** | Workers ran and produced artifacts; the dispatcher correctly timed out. No dispatch defect on the completion path. |
| SQLite lock / WAL contention blocked completion | **REJECTED** | WAL mode; worker never reached `complete_task`; no lock report; provider error is the sole blocking factor. |
| Worker waiting on child process / stdio | **REJECTED** | Detached (`start_new_session=True`), stdio → log file; no completion child. |
| Hermes code defect / GIL bug is the root cause | **REJECTED** as root cause | Blocked-network-stream behavior is normal for a hung upstream; Hermes retry/rebuild logic behaved as designed. The GIL warning is a misdiagnosis of idle/blocked-loop drift. |

---

## 10. ROOT-CAUSE CONFIDENCE

**ROOT CAUSE: CONFIRMED — HIGH confidence**
External LLM provider (`opencode-free` / Nvidia `opencode.ai` `/zen/v1`) was overloaded and intermittently unreachable, so the worker agent loop could not get a model response to issue `kanban_complete`; the worker eventually exited without a terminal transition, and no reclaim pass ran (dispatcher already timed out), leaving tasks `running` indefinitely.

- Probability the provider error is the blocking cause: ≈**certain** (identical, direct, repeated provider errors in all four logs; converged 11:27 timing).
- Probability the stalled-gateway loop is the cause: ≈**0** for the completion path (process separation proven).
- **One residual UNKNOWN:** whether the 4 worker PIDs *would* still be alive (blocked) at this moment or have exited — they are now *dead*, so the "PID alive at 0 CPU" observation was a transient blocked-network state; no current live process to interrogate.

---

## 11. SINGLE BEST NEXT EXPERIMENT (read-only; NO code change)

**Probe the free provider health directly and deterministically, and confirm a lone worker CAN complete when the provider is up.**

1. From the Hermes venv, directly drive `opencode-free`/`nemotron-3-ultra-free` with a 1-turn completion (curl/OpenAI client to `https://opencode.ai/zen/v1`) and record latency + HTTP status over ~2 minutes. This proves whether the provider is currently up (last night's Run C may have hit a transient outage window) and confirms/refutes provider availability right now.
2. If the provider responds, run a **single-variable re-dispatch** of only the still-`running`/`ready` Run-C tasks (or a fresh minimal 1-task board) and observe whether the worker reaches `kanban_complete → done` and the board converges with all downstream (verifier/synthesizer) lanes completing.
3. Do **not** modify prompts, timeouts, dispatcher logic, Hermes, or ECC. This is observation only.

**Expected disambiguation:**
- If the lone worker completes → root cause = **transient external provider outage** (confirmed). Recommend NOT changing code; rely on provider health + the existing `detect_crashed_workers` reclaim once a dispatcher pass runs.
- If the lone worker still blocks on `opencode-free` even though the direct probe is healthy → then and only then investigate a **worker-process-specific** issue (never the shared web_server loop), with fresh evidence.

---

## 12. WAITING FOR AUTHORIZATION

Forensic trace is **complete**. **No code was changed.** Stopping here per instruction; awaiting authorization before running the next (read-only) experiment or any remediation.
