# FIX-PLAN (Phase 15) — ordered P0 → P1 → HIGH → LOW

Rules: minimal, behavior-compatible-when-possible, each fix gets a regression test
and an anchor. External (no-code) blockers are listed but not "fixed".

## P0 — Unbounded cost / abuse (must land before launch, Phase 16)
| ID | Finding | Anchor | Minimal fix | Test |
|----|---------|--------|-------------|------|
| FIX-1 | FLX-DEMO-1/ECON-1: any user dispatches shared `flux-demo-*` boards unlimited, zero-credit | `main.py` dispatch + launch gates (RT-T8/RT-E2) | daily per-user demo-dispatch cap (e.g. 25/day, durable counter) + operator kill-switch env `FLUXSWARM_KILL_SWITCH=1` returning 503; keep demo usable | `test_demo_caps` |
| FIX-2 | FLX-TG-1: anonymous Telegram can spawn up to 8 agents, unmetered | `telegram_bot.py` (code) | per-chat rotating quota + frozen max_spawn under real provider; no bot creds for probe → unit-level | `test_telegram` extension |

## P1 — Security/robustness
| ID | Finding | Anchor | Minimal fix | Test |
|----|---------|--------|-------------|------|
| FIX-3 | FLX-AUTH-1: 64KB password accepted (hash DoS) | auth register/login `main.py:154+` | length cap 4096 → 400; keep Argon2id path | `test_auth_max` |
| FIX-4 | F6: vault decrypt fail-open → silent empty key | `vault.py:85` | on decrypt failure: log + raise typed error → surface readable 500/502, never blank key | `test_byok_bad_key` |
| FIX-5 | DB-1: `foreign_keys=0` each connection | `backend/db.py` connect | `PRAGMA foreign_keys=ON` in `_connect`/init (safe; schema already clean) | existing suite |
| FIX-6 | DB-2: hot tables lack indexes | schema creation | indexes: `template_purchases.event_id`, `boards.content_slug`/owner, `telegram_links` | `test_schema_indexes` |
| FIX-7 | FLX-CI-1: `ci.yml:4` triggers `main` (repo branch = `master`) | `.github/workflows/ci.yml` | `on.pull_request.branch=['master']` | — |

## MEDIUM — launch quality (Phase 16, batch B)
| ID | Finding | Minimal fix |
|----|---------|-------------|
| FIX-8 | a11y (08): no skip/link/landmarks/focus/aria | add skip-link + `role=main` + `aria-*` on nav/menu + `:focus-visible` + `prefers-reduced-motion` in inline CSS |
| FIX-9 | SEO-1/2 (13): head bare | meta description, OG/twitter, canonical from `FLUXSWARM_PUBLIC_BASE_URL`; static `robots.txt`, `sitemap.xml` |
| FIX-10 | LEG-1: UK GDPR | add `/privacy-uk` page (lawful basis, rights, retention, reps) — COPY TASK for counsel; ship EN/AR EU variant |
| FIX-11 | LEG-2: refund/credit-expiry clause | add short refund + credit-no-expiry clause to `/terms-en`+`/terms` |
| FIX-12 | F5/INFRA-3 | document + compose already has Redis; add `UvicornRedisRateLimiter` note; default stays memory (safe fail-open) |
| FIX-13 | F9/F14 ops | `DEPLOY.md`: supervisor pattern for workers + restart-state note; alerting (space/load) |

## Decisions flagged (no code: pricing/policy)
ECON-2 (post-launch metering), ECON-4 (refund credit-only), FLX-DEMO-1 design of
shared demo boards vs per-user, LEG-3 data-flow wording, SEO-3 default-lang
flip-to-EN for US.

## External blockers (cannot be fixed in-repo)
Live Paddle keys + public domain + TLS + money E2E at domain (US/UK launch);
DB backup/restore drill; provider/model license cards; US & UK legal counsel
sign-off. Full checklist: `backend/PADDLE_LIVE_CHECKLIST.md` + DEPLOY.md.