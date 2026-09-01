# PHASE 7 — SECURITY / CYBERSECURITY AUDIT

Consolidates the commercial-readiness security position from: `TEST-LEDGER.md` (red
team 29/29), `05-security-redteam.md`, `CLAIM-VERIFICATION.md`, `FAILURE-MATRIX.md`,
plus post-fix state (Phase 16 → 18). All assertions were runtime-verified.

## Authentication (VERIFIED)
- Argon2id hashing; legacy sha256 auto-upgrades on login (db.py).
- JWT HS256, 7-day; `alg=none` + signature tampering rejected (RT-03/04).
- Login brute-force lockout (5 fails → 429); per-IP 20/min cap; register cap.
- Password lengths 8..4096 enforced (post-FIX-3); short still 400, oversized 422.
- No user enumeration: unknown vs known email → identical 401 body (RT-02).

## Authorization / tenant isolation (VERIFIED)
- Board namespacing `u{id}-*` with server-side prefix guard; cross-user
  read/dispatch/security-scan → 403 (RT-T1..T3); project list isolated (RT-T4).
- Marketplace: templates namespaced (`mine` vs list); purchase atomic single-
  writer; self-buy blocked (RT-T6a); unknown template agent rejected (RT-P2).
- WebSocket requires Bearer token; unauthenticated → explicit error (RT-T9).

## API (VERIFIED)
- Input validation: pydantic lengths (project name ≤120, goal ≤4000, pw ≤4096),
  email format, provider allowlist, template-agent allowlist.
- Rate limiting: IP 20/min, login lock, register cap, demo daily cap 25, operator
  kill-switch (503) — all enforced.
- Error leakage: strong (no stack traces to client); ARCH-1 (Hermes stderr surfaced
  in 500 detail) is a MEDIUM residual — risk accepted for debuggability, to be
  sandboxed pre-launch.
- CORS: allowlist via `FLUXSWARM_CORS_ORIGINS`, no wildcard (tests).
- Security headers: CSP (locked-down incl. Paddle iframe), X-Content-Type-Options,
  X-Frame-Options DENY, Referrer-Policy, Permissions-Policy; `Cache-Control:
  no-store` on /api.
- Path traversal: `../`, `%2f`, `; rm -rf /` slugs → 403/404 (RT-P1).

## Application (VERIFIED)
- XSS: server-side `esc()` on all rendered user strings (RT-X1/X2); templates
  never echo raw HTML from user fields (CODE).
- Injection: SQL parameterized throughout (db.py); shell args via subprocess list
  (no shell=True, hermes_client.py `_run`).
- SSRF/file upload: no upload endpoint; no URL fetch feature → out of scope.
- Unsafe redirects: none.
- Dependencies: pinned, no known-CVE scan executed → `NOT VERIFIED` (pre-launch:
  `pip-audit`/`pip list --outdated`).

## AI / agents security (VERIFIED at repo boundary)
- Execution boundary = Hermes CLI subprocess (in-process keys injected as env vars
  only, never on disk; `cleanup_profile_keys` de-fangs legacy plaintext).
- Prompt-injection / tool-permission surfaces live in the ECC/Hermes layer (out of
  repo) → **BLOCKED here**; repo-side controls: goal length cap, model/provider
  pinned to user's choice, per-plan parallel cap (1/2/4/6).
- Data exfiltration: provider keys never returned by API (`/api/keys` redacts);
  export endpoint is user-authorized.

## Business logic (VERIFIED)
- Credit manipulation: plan upgrades add `max(current, allowance)`; purchases
  atomic; refund downgrade keeps balance (RT-W5); launch-failure auto-refund.
- Webhook: v1+v2 signatures, 300 s replay window, event-id dedup, constant-time
  compare; unsigned/stale/wrong-secret → 400 no grant (RT-W1..W5).
- Free-tier abuse: FLX-DEMO-1 capped + kill-switch (FIX-1); referral once-guard
  race-safe; self-referral impossible (needs distinct account + paid gate).

## Residual risks (open)
| ID | Severity | Item |
|----|----------|------|
| ARCH-1 | MED | Hermes error detail in 500 body (debuggability vs leakage) |
| FLX-TG-1 (deferred) | HIGH→cap | anonymous Telegram unmetered quota → launch POD |
| DEP-1 | MED | no automated dependency CVE scan (pre-launch) |
| INFRA | MED | HTTPS/WAF/CDN live posture unproven (no host yet) |

## Verdict
Security posture is strong for launch (no Critical/High open in-Repo code after
Phase-16 fixes). Pre-launch checklist: dep CVE scan, sandboxed Hermes errors,
Telegram quota, live TLS/WAF validation.