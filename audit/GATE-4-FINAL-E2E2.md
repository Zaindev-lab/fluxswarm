# GATE-4 — E2E #2 FAILURE REPORT (HERMES RUNTIME FAILURE)

**Result: `GATE-4 E2E #2 = FAIL`** — per STEP-6 STOP/REPORT rule, no retry, no #3.

**Board:** `u3-1788210774-f8a29392`
**E2E user:** `gate4e2e1788210774@test.local` (id=3, plan=demo, credits=200→199)
**Environment:** isolated DB (`...\gate4-e2e2\data\users.db`), isolated 8792 server (venv python 22520 → hermes-runtime 11096). Host verified QUIESCENT (all foreign flux-demo swarms killed, STEP 1/2).
**Goal:** create valid python 3.11 iterative Fibonacci function written to `fibonacci.py`.

---

## Classification: **HERMES RUNTIME FAILURE**

Root cause: the `ecc-reviewer` worker's agent session **requested an approval (`approval.pending`), but the gateway rejected it because the runtime/session was no longer in memory ("detached/reaped runtime")**. There is no interactive approval path available to dispatcher-owned workers, so the approval could not be granted; the worker then exited silently → zero heartbeats → dispatcher "pid not alive" → crash → retry → crash → give_up.

- **NOT** a FluxSwarm bug: dispatch flow correctly detected the crash, retried, and gave_up at the configured `consecutive_failures=2` limit; credits were deducted correctly and never erroneously refunded.
- **NOT** a provider failure: 5 other profiles (fluxswarm-root / ecc-planner / ecc-architect / ecc-devops / ecc-tdd) all ran fine on the same host simultaneously, produced real artifacts, and heartbeated normally.
- **NOT** host/environment contention: host was quiescent for #2 (unlike #1); only the reviewer failed, with an identical signature to E2E #1.

This is a **reviewer-profile/component-specific** runtime issue: `approval.pending` RPC from session `63e0005e` cannot be satisfied in headless dispatcher context.

---

## Failure evidence (reviewer `t_555c4895`)

| Run | Start | End | Spawned PID | Error |
|-----|-------|-----|-------------|-------|
| 6   | 22:31:01 | 22:31:37 | 24472 | `pid 24472 not alive` |
| 7   | 22:31:37 | 22:31:49 | 23340 | `pid 23340 not alive` |

- **ZERO heartbeats** in both runs.
- task_state terminal `blocked`, `consecutive_failures=2`, `last_failure_error='pid 23340 not alive'`.
- Dispatcher `gave_up`.
- Build-fixer `t_5a148372` stayed `todo` (correctly gated on failed reviewer — not a bug).
- Gateway logs (`agent.log`/`errors.log`) around 22:29–22:34 show a sustained flood:
  ```
  WARNING tui_gateway.server: session-scoped RPC rejected: method=approval.pending
  session_id='63e0005e' not in memory (detached/reaped runtime; client should
  resume the stored session), rid=13780+...13933
  ```
  -> the reviewer session's approval request was rejected; no runtime to hold it.

## Contrasting successful workers (real artifacts, no failures)

| Task | Profile | Artifact(s) |
|------|---------|-------------|
| `t_f0999e14` | planner | `fibonacci_plan.md` |
| `t_b47155a2` | architect + devops | `fibonacci.py` (iterative, n∈[0,1000]) |
| `t_053d1208` | devops | `Dockerfile`, `docker-compose.yml`, `requirements.txt` |

All 5 done, fail=0. Pinning confirmed: all tasks `model_override=nemotron-3-ultra-free`, `provider_override=opencode-free`; no paid fallback.

---

## Status / next steps

- **STOPPED** per STEP-6. No retry, no #2/#3, no source modifications.
- No reviewer respawn loop active: the two `ecc-reviewer` serve processes (17628/18632) are the standing profile server (child of Hermes app PID 3184), NOT new E2E workers.
- Isolated 8792 server + driver remain running (can be torn down on request); no production changes.
