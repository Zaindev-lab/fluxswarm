# 10 — Privacy / Legal / License Audit

Phase: 10 / 19 — Legal pages verified LIVE (HTTP 200): `/privacy`, `/privacy-en`,
`/terms`, `/terms-en` all render, include the Operating-entity block
(`FLUXSWARM_LEGAL_*` env: entity name, registry no, tax id, registered office,
phone) and the current contact email. `backend/tests/test_legal.py` exists and
passes in the 75-test baseline.

## Privacy mechanics that match the policy (RUNTIME-VERIFIED)
1. Data collected is scoped to what the policy states: email, name, Argon2id
   password hash, BYOK provider keys (Fernet-encrypted), audit/usage records.
   Policy wording matches code APIs (`/api/account/export`, `/api/account`).
2. CCPA/CPRA rights implemented and exercised: export (`GET /api/account/export`)
   and erasure (`DELETE /api/account`) — RT-E3 verifies owned-row removal.
3. Audit log disclosed as append-only/excluded from erasure (matches audit.py).
4. Paddle disclosed as merchant of record / data processor (policy + terms).
5. No cookies set by the backend (tokens ride in localStorage + Authorization
   header) → no cookie-consent banner required under PECR/UK GDPR for cookies (a
   documented localStorage approach). 
6. Data residency claim "stored in North America" matches the single-node / USA
   host assumption in DEPLOY.md (today: this machine).

## Findings (verified)

### LEG-1 — GDPR / UK GDPR addendum missing (MEDIUM, UK path)
The policy describes CCPA/CPRA only. UK launch needs UK GDPR-aligned disclosures:
lawful basis, rights (rectification/portability/objection), retention periods,
subprocessors list, and a UK-representative/DPO channel. Code supports access+erase
(native) and rectification (partial: email/name editable via API? — not exposed).
Phase 12: add `/privacy-uk` or extend `/privacy-en` with a UK GDPR contract
addendum. Also `DELETE /api/account` erases the SQLite rows but NOT on-disk Hermes
artifacts (DB-3) — the policy must either scope this or the deletion must be
completed; currently a gap between promise and mechanism.

### LEG-2 — Terms lack jurisdiction-neutral basics for US launch (MEDIUM)
`/terms-en` covers "as is", Paddle MoR, abuse policy, US-governed law — but no
explicit **refund policy**, **dispute resolution / arbitration**, **acceptable-use
detail**, **availability SLA**, or **auto-renewal/cancellation** description.
Paddle-supplied checkout covers billing T&Cs, yet the app's own terms should state
credit expiration policy (credits currently never expire) and suspension grounds.
Phase 15: mini-AU/refund/credit-expiry clause (low risk, high trust).

### LEG-3 — Third-party data flow disclosure is thin (LOW)
Privacy says "do not share except Paddle", but the core feature forwards the
user's goal to **whatever AI provider the user configured** (BYOK) and to Hermes.
The data leaves the app to those providers by design. Disclosure should name that
(and the model providers + license holders). Phase 15 wording; also ties into
LICENSE-AUDIT provider cards.

### LEG-4 — No export of board/runtime artifacts in erasure export (LOW)
`/api/account/export` covers the SQLite rows; the user's Hermes boards/artifacts
(their actual work) are not part of the export payload. For "right of access"
completeness, a JSON summary of boards/tasks should be included — or documented as
excluded with rationale (runtime state on provider systems). Phase 15 decision.

### LEG-5 — Cookie/localStorage privacy notice absent (LOW)
Legal pages say nothing about the `flux-lang`/JWT in localStorage. Minor, but a
privacy page should mention local storage + session persistence for transparency.

## License (see LICENSE-AUDIT.md)
All app deps are MIT/BSD/Apache except **python-telegram-bot 22.8 = GPL-3.0**.
Hosted SaaS use is fine (no distribution); redistribution of images/source would
trigger GPL. Repo has no LICENSE file (default all-rights-reserved). First-party
code is clean; no upstream modifications.

## Verdict
Privacy/policy scaffolding is real and code-backed (strong for a seed-stage SaaS).
Before UK launch: add UK GDPR addendum + close the erasure-gap on Hermes artifacts
or re-scope the promise (DB-3). Terms need a refund/credit clause. Legal counsel
final sign-off still required for both markets (Phase 11/12).

Phase 11 — US Launch Readiness.