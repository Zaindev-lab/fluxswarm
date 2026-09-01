# PHASE 9–13 — LEGAL & PRIVACY DOCUMENTS (live-verified 2026-08-30)

## Delivered & verified (HTTP 200, content-checked)
| Page | AR | EN | Covers |
|------|----|----|--------|
| Privacy | `/privacy` | `/privacy-en` | data collected, BYOK + Paddle sharing, CCPA/CPRA access/erase, append-only audit exclusion, North America hosting (launch posture), UK/EU addendum (lawful bases, ICO, transfers) |
| Terms | `/terms` | `/terms-en` | as-is, Paddle MoR, credits-only-after-payment, never-expire, auto-refund on failed launch, refund→Demo keeps balance, abuse suspension, US governing law, refund link |
| Refund | `/refund` | `/refund-en` | **no absolute no-refund**; auto credit refunds; 14-day discretionary monetary review net of consumed work; consumer rights (UK/EU) not waived; Paddle process |
| Cookies | `/cookies` | `/cookies-en` | no tracking cookies, JWT in localStorage, flux-lang; no analytics/ads/pixels; PECR no-banner position; Paddle cookies on its domain only |
| Acceptable Use | `/acceptable-use` | `/acceptable-use-en` | unlawful/malicious content, reselling credits, API scraping, provider-terms compliance, suspension + referral to authorities |
- All pages: RTL/EN pairs, entity block from `FLUXSWARM_LEGAL_*` env (empty until deploy), contact email, `آخر تحديث / Last updated: 30 August 2026`.
- Footer (index.html) now links Privacy · Terms · Refund · Cookies · Acceptable-Use (AR) · Privacy/Terms (EN).
- Server restarted with new routes; endpoints returned 200; `py_compile` clean; pytest **85/85** green after edits.

## Accuracy-to-implementation checks
- "credits never expire / not withdrawable" — matches db (`credits` balance, no expiry).
- "auto-refund on failed launch keeps credits" — matches launch path + RT-W5.
- "refund → downgrade to Demo, balance kept" — matches webhook handler (kind refund).
- "delete /api/account erases rows + keys" — matches `api_account_delete` (vault + db).
- "append-only audit excluded" — matches audit.jsonl write-only.
- No cookie claims beyond truth (server sets none); localStorage claims exact.

## Gaps recorded (non-blocking at write-time; feed Phase 29)
- D3: `referrals.referred_email` of a deleted user is not erased (erasure gap).
- D12: Hermes board artifacts not auto-deleted on `/api/account` — disclosed on the
  page ("later releases; email to request") — live parity pending implementation.
- Self-service rectification endpoint not implemented → page directs to support
  (honest, no false claim). Flag P2: add `PATCH /api/account`.
- UK/EU representative decision + consumer-contract law review → **LEGAL REVIEW
  REQUIRED** (env-entity block reserved for deploy).
- "Hosted in North America" is launch posture; current dev host is local (Phase 29).

## Status
P9–P13 COMPLETE and live. No legal page overstates implementation.