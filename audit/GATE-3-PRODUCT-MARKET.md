# GATE 3 — PRODUCT + UX + US/UK + LEGAL + PRICING

- **Protocol:** MASTER AUDIT (5-Gate). Gate 1 = PASS (architecture), Gate 2 = PASS.
  This is **Gate 3**.
- **Date:** 2026-08-30
- **Scope:** full customer journey on the running app; UX fixes (P0→P1, safe P2);
  US-market readiness (replace Arabic-first, argon default); UK readiness
  (PECR/VAT/consumer rights); privacy/terms/refund/cookies; ECC/Hermes attribution;
  pricing & credits & referrals accuracy; mobile + accessibility; marketing-claims
  scan; regression to Gate-2 totals.
- **Evidence rules:** every claim carries EVIDENCE (code file:line — on-disk — live
  runtime — executed tests). Nothing asserted from old reports alone.
- **Run env:** `C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`
  from `C:\Users\DELL\fluxswarm\backend`; live probes against `http://127.0.0.1:8787`.

---

## 0. VERDICT

> **GATE 3 = PASS** (within-repo scope).
>
> - **All P1 product/UX/market defects found this gate are FIXED and regression-locked**
>   by a new 14-test evidence suite; full suite = **140 passed** (126 prior + 14 Gate-3).
> - The landing SPA is **English-first** (`<html lang="en" dir="ltr">`), the pricing,
>   how-it-works, FAQ, terms and checkout pages are rewritten to be **accurate to the
>   real product model** (verified against `db.PLANS`, `hc.SQUAD/ENV_MAP/FREE_PROVIDER`,
>   and the credit/referral/template economics), every **marketing claim the product
>   could not honour was removed or corrected**, and the previously-false claims
>   "browsable in the UI" and "export/delete from the account page" are now **true**
>   (new workspace endpoint + new account&data section).
> - **GATE 3 = PASS is conditional on the operator/external follow-ups in §25–26**
>   (Paddle credential ownership, Hermes/ECC licence review, jurisdiction review,
>   LIVE domain+TLS+mailer deploy, Redis for multi-worker). Nothing below claims
>   those are done in-repo.

| # | Section | Result |
|---|---------|--------|
| 1 | Executive summary | **FIXED + VERIFIED** |
| 2 | UX audit | **FIXED** (English-first SPA) |
| 3 | Customer journey | **VERIFIED** (HTTP end-to-end test) |
| 4 | How FluxSwarm works | **VERIFIED** (6-agent chain, honest scope) |
| 5 | FAQ | **REWRITTEN + VERIFIED** |
| 6 | Messaging | **CORRECTED** (1 credit/public) |
| 7 | Pricing audit | **FIXED** (packs, not /month) |
| 8 | Credit model | **VERIFIED against db.PLANS** |
| 9 | Referrals | **VERIFIED** (25 credits, clawback clause) |
| 10 | US readiness | **FIXED + VERIFIED** |
| 11 | UK readiness | **VERIFIED** (GBP/VAT, consumer rights kept) |
| 12 | Privacy | **VERIFIED** (re-scoped, accurate) |
| 13 | US privacy (CCPA) | **VERIFIED** (live export/erase) |
| 14 | UK/EU GDPR addendum | **VERIFIED** (lawful basis, ICO) |
| 15 | Terms | **REWRITTEN** (no US-law claim; neutral) |
| 16 | Refund & credit policy | **VERIFIED** (no-refund absolutes gone) |
| 17 | Cookies & tracking | **VERIFIED** (PECR-consistent) |
| 18 | ECC / Hermes attribution | **FIXED** (transparency added) |
| 19 | Accessibility | **PASS (source-level; no browser automation)** |
| 20 | Mobile | **PASS (source-level media query)** |
| 21 | Marketing claims scan | **CLEAN** (banned set absent) |
| 22 | Fixes applied (P0→P4) | **APPLIED, P0=none, P1 closed** |
| 23 | Tests & evidence | **140 passed; runtime probes PASS** |
| 24 | Remaining risks | **external/infra, no in-repo blocker** |
| 25 | Legal review required | **OWNERSHIP / LICENCES / JURISDICTION** |
| 26 | Findings register | **P0–P4 + owner follow-ups** |

**STOP RULE — honored at the end of this file (§28).**

---

## 1. Executive summary

Gate 3 audited the product exactly as a **US/UK first-time customer** would see it,
and found the app was **Arabic-first by default** (`<html lang="ar" dir="rtl">`,
Arabic meta/title, Arabic-only marketing pages), marketed **prepriced as a monthly
subscription** ("/month", meta "Hourly flat fee"), claimed **"unlimited projects"**
(project creation actually debits 1 credit), told customers they **"need a key"**
(the default runtime is the free hosted model), claimed the swarm output was
**"browsable in the UI"** and **"export/delete from the account page"** (neither
UI existed before this gate), and attributed the model stack incorrectly
("OpenRouter-native"). Several marketing absolutes were also unenforceable
("infallible", "guaranteed", "instant", "100% accurate", "fully compliant").

All of the above were **fixed in-source** (see §22 for the P0→P2 mapping), locked by a
new 14-test suite, and re-verified on the **restarted live server**. The terms of
service no longer claim to be "governed by the laws of the United States" for an
Algerian operator; consumer-rights and governing-law clauses now match the actual
operator (§15, §25). Remaining items are external (ownership/licences/deploy) and
are tracked, not silently resolved.

---

## 2. UX audit

**Evidence of defect (pre-fix):**
- `templates/index.html` defaulted `<html lang="ar" dir="rtl">` with Arabic
  meta/title; the `/` page served Arabic to every visitor regardless of browser
  language. For a US/UK market this is a first-impression blocker.

**Fixes (this gate):**
- **Default English-first:** `<html lang="en" dir="ltr">`, EN meta description,
  EN OG/Twitter tags, EN title. Browser-language Arabic still auto-switches to AR
  (`navigator.language` in the SPA) with the manual toggle intact.
- Header navigation: **How it works**, **FAQ**, **Support** (mailto), plus nav for
  **History & results** and **Account & data** — the two previously-missing product
  surfaces are now reachable in the UI.
- New **"What is FluxSwarm?"** intro card on the landing so a cold visitor learns
  what the product does without scrolling into screenshots.
- **History & results** card (calls `GET /api/projects`) and a **Generated workspace
  viewer** card (`loadWorkspace(api/projects/{slug}/workspace)`) — makes the
  "browsable in the UI" claim true.
- **Account & data** card: change password, export (JSON), and delete account
  (with typed confirmation) — makes the "export/delete from the account page"
  claim true.
- Error responses mapped to the UI language via an `errt()` **AR→EN map**; toast
  uses `aria-live`; modal uses `role="dialog"` and closes on `Esc`; buttons get
  disabled states; form fields have `for`/`id` labels (see §19).
- Mobile media query wraps the header and hides the tagline (§20).

**Evidence:** `templates/index.html` (rewritten, 627 lines); `test_gate3_ux.py::test_landing_english_default`
(green); live probe of `/` (EN default, no `dir="rtl"` markup, telegram wiring intact).

---

## 3. Customer journey

The full sequence a customer performs was exercised **over HTTP** in-process
(`test_full_journey_register_to_delete`) against the real ASGI app (Hermes CLI
stubbed):

register → login → `GET /api/me` → **create project (1 credit, swarm launched)` →
`GET /api/projects` history shows it → `GET /api/projects/{slug}/tasks` →
`GET /api/projects/{slug}/workspace` returns the generated files →
change password (old session dies, 401; re-login works) → CCPA export (JSON) →
account delete (rows erased, token dead) .

**Assertions that pass:** fresh account credit = `PLANS["demo"]["credits"]` (3);
creation leaves 2; history lists the new slug; tasks enumerate; workspace content
is returned; a `RuntimeError` during `launch_swarm` → HTTP 500 **and the credit is
refunded** (no pocketed fee for a failed launch). Unauth workspace → 401;
cross-tenant workspace read → 403; guessed foreign board → 403 (no 404 data leak);
demo boards stay public showcase (200 for any authenticated user).

---

## 4. How FluxSwarm works

`/how-it-works` rewritten (EN). Verified to match the real system:
- **What/Who** — hosted AI development squad for solo devs/startups/small teams.
- **6-agent team** — Planner → Architect → DevOps → TDD → Reviewer → Builder — which
  is the actual `hc.SQUAD` (4 workers: `ecc-planner`, `ecc-architect`, `ecc-devops`,
  `ecc-tdd`) + VERIFIER (`ecc-reviewer`) + SYNTHESIZER (`ecc-build-fixer`).
  **Evidence:** `hermes_client.py:43-50` and `api/squad` (test `test_squad_api_shape`).
- **Model choice** — "bring a key (Claude/OpenAI/Gemini/Kimi) or use the free one":
  matches `ENV_MAP` + `FREE_PROVIDER="opencode-free"` default (no key needed).
- **Workspace** — "browsable in the UI": now true via the new workspace endpoint
  (§3) + viewer (§2).
- **Billing** — 1 credit/launch, pre-paid packs, never expire, auto-refund on failed
  launch, no monthly fee.
- **Transparency** — Hermes = execution runtime, ECC = open-source skill profiles;
  "FluxSwarm is not Hermes and does not own ECC" (see §18).

---

## 5. FAQ

`/faq` rewritten with 20 EN answers, every one checked for accuracy:

| Question | Claim | Evidence |
|----------|-------|----------|
| Does the squad need my AI key? | **No.** Free hosted model default | `hc.FREE_PROVIDER="opencode-free"` |
| What is Hermes? ECC? | runtime + OSS skill profiles; not owned by FluxSwarm | `hermes_client.py` + §18 |
| Costs without a key | 1 credit/launch, demo 3 free | `db.PLANS` |
| Plan prices | packs **not subscriptions**; $29/25, $99/120, $299/500 | `db.PLANS` |
| Speed | board streams live; no "instant" promise | honest phrasing |
| Cancel | no subscription to cancel; packs | `db.PLANS` model |
| Failed launch | credit refunded automatically | `main.py` create-project catch path + test |
| Credits run out | boards/data kept; only new launches gated | `db.deduct_credit` behaviour |
| Sell output / templates | you own output; **50% author share** paid in credits | `db.buy_template` |
| Referrals | **25 credits**, once per email; **self-referral/abuse prohibited, rewards may be clawed back** | `db.REFERRAL_REWARD_CREDITS` + new clause |
| Key storage | Fernet at rest, injected at launch, never returned | `vault.py` + `hermes_client` |
| Privacy/retention/support | namespace isolation, export/erase, audit log excluded from deletion, mailto support | code + policies |

Stale, false answers removed: "BYOK-first / you need a key", "OpenRouter-native",
"instant". Test: `test_faq_believes_product_claims`.

---

## 6. Messaging

Hero/meta copy corrected to **verifiable** statements:
- New meta: "1 credit per launch. BYOK optional." and OG: "…or use the free hosted
  model." — both true (§4, §8).
- Removed "unlimited projects" (spend is 1 credit/launch) and "Sign in for free …
  unlimited" framing.
- Landing hero states **1 credit per lane** and the free-model default honestly.
- Support contact surfaced in header/footer (mailto).

---

## 7. Pricing audit

`/pricing` rewritten (EN, `_MARKET_BASE`, `lang="en"`). Corrections:
- **"Hourly flat fee" meta → "1 credit per launch, BYOK AI builders".**
- **"/month" → "one-time credit pack"** on every plan: the product has **no
  recurring subscription**; `upgrade_plan` sets credits = `max(current, plan)`.
- "Simple pricing, no token meters", "1 credit = 1 swarm launch", "credits prepaid
  and never expire", "failed launch refunded automatically", "Paddle merchant of
  record (sales tax/VAT remittance)", "merchant refund → Demo + balance kept",
  prices in USD with **GBP applied by Paddle at UK checkout** — all accurate.
- Renders live plan data (`db.PLANS`) so it can't drift. Test + live probe verify
  each plan name/price/credits plus "credit pack", "USD", "GBP", and absence of
  "/month".

---

## 8. Credit model

Verified against code, not copy:

| Fact | Value | Evidence |
|------|-------|----------|
| Plans | demo $0/3/par1 · starter $29/25/2 · pro $99/120/4 · scale $299/500/6 | `db.PLANS` |
| Launch cost | exactly 1 credit, all plans | `db.deduct_credit` in create-project |
| Failed launch | credit refunded | create-project catch path + test |
| Expiry | none | `db.py` (no expiry path) |
| Upgrade | credits = `max(current, plan.credits)` | `db.upgrade_plan` |
| No subscription | Paddle subscription object used as *prepaid class*; no recurring billing | pricing/FAQ copy + `db.PLANS` |
| Scaffold | `PLANS[plan]` lookups stable after refund | `db` plan handling |

Test: `test_pricing_page_accurate_to_plans` + `test_api_plans_match_db` iterate the
real plan table.

---

## 9. Referrals

- Reward = **25 credits** once per referred email on subscribed plan upgrade
  (`db.REFERRAL_REWARD_CREDITS=25`, verified).
- **New anti-abuse clause** added to FAQ and Referral terms: self-referral / fake
  referrals prohibited; **rewards may be clawed back** (documents the unenforced
  enforcement gap instead of claiming an impossible "reference check").
- Referral UI unchanged; ref_code per user (`db.create_user`).

---

## 10. US readiness

- **Language:** English-first landing (§2) — resolved the biggest blocker.
- **Pricing:** USD default; Paddle (merchant-of-record) collects/remits applicable
  US sales tax incl. marketplace-facilitator scenarios.
- **Privacy/CCPA:** live `GET /api/account/export` + `DELETE /api/account`
  (incl. deletion of keys/boards), stated in `/privacy-en` — exercised §3.
- **Legal:** contact email present; entity disclosure present in live deploy
  (env-driven); **no claim of "US-governed" terms**; terms are neutral/applicable
  law (§15).
- **Hosting:** privacy policy states launch-time hosting in North America.
- **Remaining (external, §26):** US-specific legal review of the jurisdiction
  clause; TLS/domain deploy before public launch.

---

## 11. UK readiness

- **Pricing:** GBP applied by Paddle at checkout for UK customers; USD list prices
  shown.
- **VAT:** Paddle as merchant of record handles digital-services VAT remittance
  (stated on /pricing, terms).
- **Consumer rights:** explicitly **not waived** in Terms and Refund policy
  ("statutory consumer rights including UK and EU"); no automatic full-refund
  promise that would contradict UK/EU rules — refund page reviews case-by-case and
  keeps the 14-day digital-content nuance.
- **Cookies (PECR):** `/cookies-en` states no tracking cookies, no analytics/ads/
  pixels → no consent banner required; Paddle sets cookies only on its own domain.
- **Regulator:** UK/EU addendum points to the ICO as the supervisory authority.
- **Remaining (external):** UK/EU-specific legal confirmation of the neutral
  jurisdiction clause before launch.

---

## 12. Privacy

Data collected = email, name (Argon2id), BYOK keys (Fernet), goals + generated
outputs, usage/audit, minimal payment metadata. **No sale, no ads, no training.**
Sharing: only Paddle (MoR) + the user's chosen AI provider (BYOK). Export + full
erasure live; **security audit log is append-only and excluded from erasure**
(stated in policy). Evidence: `/privacy`, `/privacy-en`, `vault.py`, `audit.py`,
§3 journey, `test_legal_*`.

---

## 13. US privacy (CCPA/CPRA)

Right to access (`GET /api/account/export` returns user/projects/keys-absent JSON),
right to correction/deletion (`DELETE /api/account`), immediate key deletion on
delete. Exercised over HTTP in the journey test and in Gate-2 paddle-suite
(`test_account_export_delete_via_api`). No "verifiable request" friction beyond
the authenticated session; documented enforcement path vs the account page.

---

## 14. UK/EU GDPR addendum

`/privacy-en` and `/privacy` carry a UK/EU addendum: lawful bases (contract
performance, legitimate interests — security/anti-fraud, legal obligation —
billing), the user's access/rectification/erasure/portability/objection rights,
complaint route (UK: **ICO**), and transfer-to-AI-provider disclosure. Respects
the "no transfer for marketing" boundary. Verified by test + runtime probe.

---

## 15. Terms

`/terms` (ar) and `/terms-en` rewritten:
- **Removed the false/conflicting "governed by the laws of the United States"
  clause** — the operator is an Algerian single-member LLC; the provision was
  legally meaningless and could confuse consumers. Now: "governed by applicable
  law … mandatory consumer protections in your country unaffected; jurisdiction
  specifics kept under legal review."
- New clauses: **Credits** (prepaid, 1 credit/launch, no expiry, auto-refund,
  merchant refund → Demo + keep balance), **Your data and your keys**, **Output**
  (you own it, subject to provider/third-party licences), **Acceptable use**,
  **Availability** (no uninterrupted-availability promise), **Termination**
  (self-delete any time; suspension policy), **Limitation of liability**
  (as-is, to the extent permitted; consumer rights not waived), **Governing law
  and jurisdiction** (neutral).
- Entity disclosure block is env-driven and rendered on both variants.

Evidence: `test_legal_pages_cover_consumer_rights_and_entity`,
`test_legal_pages_stable_translation_pairs`, live probe (EN terms: consumer rights
present, "laws of the United States" absent).

---

## 16. Refund & credit policy

`/refund` + `/refund-en` verified:
- Credits are service credits: never expire, not withdrawable.
- **Automatic refunds** only where true: failed launch before any work → credit
  back; Paddle monetary refund → plan downgraded to Demo, balance kept.
- **Monetary refunds reviewed case-by-case** (14-day window net of consumed work);
  no "full automatic refund" absolute; unconsumed balance refundable subject to
  Paddle process. UK/EU consumer rights not waived.
- This closes the Gate-2-note "no-refund absolutes" concern.

---

## 17. Cookies & tracking

`/cookies` + `/cookies-en` (verified):
- No server-side tracking cookies; JWT in `localStorage` (7-day expiry / logout),
  plus `flux-lang` preference.
- No analytics, ads, pixels, third-party trackers. **No PECR consent banner needed**
  by our own site; Paddle sets cookies only on its own checkout domain. Links to
  refund/privacy. Live probe: both variants 200.

---

## 18. ECC / Hermes attribution

Transparency added everywhere the product mentions them (how-it-works, FAQ,
terms/output clauses, landing card):
- Hermes = execution runtime; ECC = underlying open-source agent skill profiles.
- **FluxSwarm is not Hermes and does not own ECC**; their licences belong to their
  respective authors; availability of both is required to run a launch.
- No invented license claims; see §25 (external licence review stays open).

---

## 19. Accessibility

Source-level review of the rewritten SPA (honest scope — **no browser automation
available**, so these are markup/behavior verifications, stated as such):

| Check | Status |
|-------|--------|
| Modal opened with `role="dialog"`, `aria-modal`, closes on `Esc` + overlay click | present |
| Toast announcements use `aria-live="polite"` | present |
| All inputs have `for`/`id` label associations | present |
| Form submit buttons disabled during in-flight requests | present |
| Interactive header items are real `<button>/<a>` | present |
| Contrast/variants use the dark-on-light tokens (source review only) | not machine-measured |
| Full keyboard + screen-reader pass | **requires browser automation — flagged** |

---

## 20. Mobile

Media query added to the SPA: header collapses/wraps, tagline hidden on narrow
viewport; grid layouts are `auto-fit/minmax` so cards stack; inputs/buttons sized
for ~375px touch. Server marketing pages use fluid grids. Honest limitation:
**visual verification on real devices not executed here** (no device farm); layout
rules verified by CSS review.

---

## 21. Marketing claims scan

The rendered **EN** landing, pricing, how-it-works, and FAQ were scanned
(case-insensitive) for claims the product cannot honour:

`"unlimited" · "/month" · "hourly" · "openrouter" · "infallible" · "guaranteed" ·
"enterprise-grade / enterprise grade" · "100% accurate" · "zero data retention" ·
"fully compliant" · "best-in-class"`

- **Result: clean** on the four pages (test `test_claims_scan_no_hype` + runtime
  probe of `/`, `/pricing`, `/faq`).
- Pre-existing stale phrases that would have tripped it (e.g., "/month", "Hourly",
  "OpenRouter") were removed as part of the rewrites; the scan now guards them.
- Honest caveats kept where legality varies per buyer (±18/§15/§16 wording).

---

## 22. Fixes applied (P0→P4 classification)

| Class | Finding | Disposition |
|-------|---------|-------------|
| P0 | (none existed — no data loss or payment impact) | — |
| P1 | AR-first default landing blocks US/UK audience | **FIXED** (EN-first + ar override) |
| P1 | Pricing marked "/month" + meta "Hourly flat fee" (false: packs) | **FIXED** (packs + USD/GBP) |
| P1 | FAQ/landing claimed keys required & "OpenRouter-native" (false: free model default) | **FIXED** |
| P1 | "Unlimited projects" (false: 1 credit/launch) | **FIXED** |
| P1 | "Workspace browsable in the UI" and "export/delete from account page" were false | **FIXED** (endpoint + Settings section) |
| P1 | ToS "governed by the laws of the United States" for an Algerian LLC | **FIXED** (neutral + consumer rights) |
| P2 | Unenforceable marketing absolutes (guaranteed/infallible/instant/…) | **FIXED** (removed; scan added) |
| P2 | No referral-abuse policy while crediting 25/referral | **FIXED** (abuse + clawback clause) |
| P2 | Refund page could read as absolute no/yes refunds | **FIXED** (precise auto-refund vs case-by-case) |
| P3 | API error `.detail` strings remain Arabic (EN UI maps them via `errt()`; raw API consumers see AR) | **DEFERRED** (see §24) |
| P4 | — | none this gate |

---

## 23. Tests & evidence

| Run | Command | Result |
|-----|---------|--------|
| Gate-3 new suite | `pytest tests/test_gate3_ux.py -q` | **14 passed in 2.42s** |
| Full regression | `pytest -q` | **140 passed in 16.54s** |
| Stale-assertion fix | `tests/test_paddle_flow_api.py` (Arabic checkout assertion → EN) | 10 passed (cascade resolved) |
| Syntax gate | `py_compile main.py db.py hermes_client.py backup.py` | OK |
| Server restart | `restart_gate3.ps1` (same env-loading as `run.ps1`) | new uvicorn pair on :8787 |
| Live health | `GET /health` | 200, `{"limiter_backend":"memory"}` |
| Live landing | `GET /` | EN default, no RTL attr, no "unlimited", tg wiring present |
| Live pricing/faq | `GET /pricing` `/faq` | 200, "credit pack", free-model, no OpenRouter |
| Live legal | 11 pages | all 200 |
| Live workspace | login demo → `GET /api/projects/flux-demo-1/workspace` | 200 `{"slug":"flux-demo-1","content":""}`; unauth → 401 |

**Regression discipline artifacts (this gate):**
1. `/mock-checkout` was converted to English per the US-market mandate; the old
   test asserted Arabic `"دفعة تجريبية"` → aborted before the dev-complete grant,
   cascading into the two downstream "user 1 == pro" failures. Fixed the stale
   assertion to the English page marker; **10/10 paddle-flow tests pass again**.
2. The API detail strings for 401/403/400 remain Arabic throughout; the EN SPA
   maps them (errt()); the open issue is only for non-SPA API consumers → P3.

---

## 24. Remaining risks

- **No browser automation** in this environment: visual rendering, true keyboard/
  screen-reader pass, and touch-device layout are verified by source review +
  HTTP-level tests only (§19–20).
- **Running server**: restarted to Gate-3 code this gate (replaces the note in
  Gate-2 §4 that :8787 ran pre-fix code). One fresh post-restart **live launch
  soak** is still the recommended operator gate.
- **External, unchanged:** Paddle credential ownership unprovable in-repo;
  Hermes/ECC licence review outstanding; jurisdiction/cross-border deploy review;
  production SMTP mailer for password reset; Redis for multi-worker; LIVE domain +
  TLS.
- **P3:** raw-API consumers receive Arabic `.detail` error bodies (UI unaffected).

---

## 25. Legal review required (external)

| Item | Status |
|------|--------|
| Paddle sandbox/price-ID ownership | **NOT VERIFIED in-repo** (operational verification) |
| Hermes & ECC licences (commercial usage) | **REVIEW REQUIRED** (kept unclaimed; attribution accurate) |
| Jurisdiction / applicable-law clause | **UNDER REVIEW** (neutral wording shipped; verify with counsel before EU/UK launch) |
| Consumer-rights / refund wording (UK/EU) | **REVIEW RECOMMENDED** (policy is deliberately careful, not legal advice) |
| Cookie consent per-market (PECR/GDPR/CCPA interplay at scale) | **REVIEW when ads/analytics are ever added** |

---

## 26. Findings register (owner follow-ups)

| # | Item | Disposition | Owner follow-up |
|---|------|-------------|-----------------|
| UX-1 | English-first landing + accurate marketing | FIXED + test-locked | re-verify visually in a real browser |
| UX-2 | Workspace endpoint + viewer | ADDED + test-locked | live launch soak on restarted server |
| UX-3 | Account & data section (password/export/delete) | ADDED + test-locked | — |
| PR-1 | Pricing packs / USD+GBP / Paddle MoR note | FIXED + test-locked | confirm Paddle tax handling config |
| PR-2 | Referral abuse + clawback clause | FIXED (documented) | monitor-led enforcement when any scale |
| LG-1 | ToS US-law clause removed; neutral governing law | FIXED | counsel review before launch |
| LG-2 | Hermes/ECC attribution + licence disclaimer | FIXED | obtain licence sign-off |
| LG-3 | Refund/cookies/consumer wording | FIXED + test-locked | counsel review (UK/EU) |
| TEST | 140 passed incl. 14 Gate-3 | DONE | — |
| LIVE | :8787 on Gate-3 code | DONE | run 1 real launch; then Gate 4 |

---

## 27. (reserved)

---

## 28. STOP RULE

> **GATE 3 report delivered — PASS. Execution halts here.**
> Gate 4 (the protocol's P2 inner-loop: `AgentRuntime`/`HermesAdapter` abstraction)
> begins only on explicit authorization from the audit owner. Nothing in this
> session modified live data or launched a real paid transaction; the running
> server now serves the Gate-3 code.