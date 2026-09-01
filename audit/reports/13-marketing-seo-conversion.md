# 13 — Marketing / SEO / Conversion Audit

Phase: 13 / 19 — Evidence: `PRODUCT-FAQ.md`, `GTM_LAUNCH_PLAN.md`, template scans.

## Positioning (from GTM_LAUNCH_PLAN.md — present, EN/AR)
- One-liner (EN): "Type the goal — a full 6-agent squad builds it live. BYOK means
  you pay your tokens, FluxSwarm only takes a coordination fee."
- ICP: startup/indie teams (1–10 people), EU–MENA; pain = prototype→MVP velocity.
- Existing: legal pages, pricing phrases, Telegram on-ramp, marketplace.

## SEO — CODE-VERIFIED gaps (index.html)
| Element | Status |
|---------|--------|
| `<title>` | present (AR default) |
| `<meta name="description">` | **missing** |
| OpenGraph / Twitter cards | **missing** |
| `<link rel="canonical">` | **missing** |
| JSON-LD structured data | **missing** |
| `robots.txt` / `sitemap.xml` | **missing** (static dir absent) |
| Analytics (gtag/GTM) | not present (by design; add post-launch) |
| Paddle checkouts | served from `/checkout` page (not in index) — fine |

## Conversion surfaces
- Landing = the app itself (registration is the CTA). No marketing landing page;
  GTM plan may warrant one. Registration is friction-light (email+name+password).
- i18n EN/AR persists; default AR — for a US launch the default should flip to EN
  based on `Accept-Language`/geo (Phase 15, LOW): reduces bounce for US visitors.
- Pricing table present (`/api/plans` + page) with 4 plans; no annual/one-click demo
  CTA beyond "Demo مجانية".

## Findings
- SEO-1 (LOW): head meta/OG/canonical/structured-data missing — cheap to add, blocks
  social-sharing previews and SERP snippets.
- SEO-2 (LOW): no robots.txt/sitemap.xml — crawler-crawlable dynamic routes are
  fine, but the legal pages/plans should be indexable; add static files.
- SEO-3 (LOW): default UI language is Arabic → US visitors may bounce unless the
  default detection flips to EN; keep AR for MENA positioning.

## Verdict
Product story and plan exist and are coherent with the code; technical SEO and
conversion hygiene are thin but LOW-cost to fix in Phase 15. Analytics should wait
for a real domain (no wasted tags pre-TLS).

Phase 14 — Full E2E & Failure Testing.