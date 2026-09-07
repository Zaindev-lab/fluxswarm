# FluxSwarm Pre-Launch Checklist

> Target audience: US/UK developers & small teams.
> Single language: **English only** (all Arabic removed).
> 0$/month free tier stack: Render + Neon + Upstash + Cloudflare.

---

## Phase 0 — CRITICAL (must-fix before ANY deploy)

- [ ] **Pin `cryptography`** in `backend/requirements.txt`
  Current: `cryptography>=42.0.0` (floating, 2+ majors behind).
  Fix: `cryptography==50.0.0` (exact pin, matches upstream hermes-agent).
- [ ] **Verify `.dockerignore`** excludes everything not needed in the image:
  ```
  backup/
  backups/
  .env
  .env.*
  audit/
  REPORT.md
  *.zip
  *.log
  pyerr.txt
  .coverage
  .pytest_cache/
  deploy/docker/hermes-home/.env
  ```
- [ ] **Fix missing `await` bug** — `index.html:482`
  `setUser(r.json().user)` → `setUser((await r.json()).user)`
  Currently receives a Promise, not the user object. Auth flow is broken.
- [ ] **Validate `HERMES_HOME` in entrypoint.sh**
  Add guard: `[ -z "${HERMES_HOME:-}" ] && echo "HERMES_HOME unset" && exit 1`
  Without it, unset var creates dirs at filesystem root.
- [ ] **Stop leaking `str(e)` to clients** — `main.py:1381,1402,1413,1443,2811`
  Replace with generic messages; log the real error server-side.
- [ ] **Remove hardcoded OpenRouter API key** from `deploy/docker/docker-compose.beta.yml:69`
  `sk-or-v1-fa00af...` — move to `.env` (untracked) or remove entirely.

---

## Phase 1 — ENGLISH ONLY (remove all Arabic)

### 1.1 `index.html`

| Line(s) | What to do |
|---------|-----------|
| 666 | Change default: `let LANG = "ar"` → `let LANG = "en"` |
| 121 | Remove `aria-label="العربية"` |
| 331-353 | Delete entire `AR2EN` dict |
| 450 | Remove Arabic conditional: `(LANG==="ar"?"الملفات المولّدة":slug)` → just `slug` |
| 543-603 | Delete entire `I18N.ar` object |
| 699-700 | Remove Arabic footer links (الخصوصية / الشروط) |
| 8 | Update OG description: remove "account demo" reference |

### 1.2 `demo.html`

| Line(s) | What to do |
|---------|-----------|
| 2 | `<html lang="ar" dir="rtl">` → `<html lang="en" dir="ltr">` |
| 6 | Title: `جرب FluxSwarm مجاناً` → `Try FluxSwarm Free` |
| 39 | `⚡ جرب FluxSwarm مجاناً` → `⚡ Try FluxSwarm Free` |
| 41-42 | Remove RTL/LTR toggle button entirely |
| 45 | Arabic paragraph → English: `A swarm of AI agents builds your project in real-time.` |
| 47 | `صف مشروعك` → `Describe your project` |
| 48 | Placeholder → `e.g. Build a FastAPI app with auth and tests` |
| 49 | Button → `Launch Free Trial` |
| 58 | Link → `→ Full account: unlimited launches & paid plans` |
| 82-89 | Delete RTL/LTR toggle logic |
| 94 | `حصتك اليومية المتبقية` → `Remaining daily quota:` |
| 98 | `TOAST_STATUS` values → all English |
| 106 | → `Board not ready yet — try launching again.` |
| 116 | → `Estimated time remaining: ~` + `seconds` |
| 119 | → `✅ Trial session complete. Sign up free to download the code...` |
| 138 | → `Launch failed: ` |
| 142 | → `Preparing swarm...` |
| 145 | → `Connection error — please try again.` |

### 1.3 `admt_notice.html`

- Keep the `{% if lang == 'ar' %}` blocks **or** remove them entirely.
- Since the platform is English-only: remove all AR branches, keep only the EN content.
- Remove `dir="{{ 'rtl' if lang == 'ar' else 'ltr' }}"` → hardcode `dir="ltr"`.
- Remove `lang="{{ lang }}"` → hardcode `lang="en"`.

### 1.4 `main.py`

- All `I18N` / translation dicts that contain Arabic strings → remove.
- All `lang="ar"` defaults → change to `lang="en"`.
- Any `TemplateResponse(..., lang="ar")` → change to `lang="en"`.
- All hardcoded Arabic in legal pages (privacy, terms) → remove or replace with English.
- Remove the `&lang=ar` parameter handling.

---

## Phase 2 — UX/UI FIXES

### 2.1 Critical UX bugs

- [ ] **CSP violation: `.onclick` assignments** (bypasses nonce delegation)
  - `index.html:367` — `navLogin.onclick` → use `data-onclick="navLogin"` + delegate
  - `demo.html:70,83,125` — three `.onclick` assignments → convert to `data-onclick`
- [ ] **`prompt()` for delete/template goal** — replace with inline modal:
  - `index.html:510` — `deleteAccount()` uses `prompt()` → add confirmation dialog
  - `index.html:533` — `buyTpl()` uses `prompt()` → add inline goal input field

### 2.2 Loading states (add spinners/skeletons)

| Function | Current state | Fix |
|----------|--------------|-----|
| `loadSquad()` :359 | Empty grid → data | Add skeleton cards |
| `loadPlans()` :360 | Empty → data | Add skeleton pricing cards |
| `launchDemo()` :421 | No feedback during fetch | Add spinner on button |
| `loadProjects()` :427 | Shows "empty" immediately | Add skeleton row |
| `loadReferrals()` :371 | Empty → data | Add skeleton |
| `loadByok()` :372 | Empty → data | Add skeleton |
| `loadTemplates()` :529 | Empty → data | Add skeleton |

### 2.3 Error states (add user-facing errors)

| Function | Current state | Fix |
|----------|--------------|-----|
| `loadSquad()` | Silent failure | Show "Could not load agents" inline |
| `loadPlans()` | Silent failure | Show "Could not load plans" inline |
| `connectWS()` | No `onerror` handler | Add error toast |
| `loadTemplates()` | Silent failure | Show "Could not load templates" inline |

### 2.4 Accessibility

- [ ] Add `<nav>` element around navigation bar (`index.html:44`)
- [ ] Add `aria-labelledby` to auth modal (`index.html:284`)
- [ ] Status dots (`s-done`, `s-running`) need text labels for colorblind users
- [ ] Add `aria-live="polite"` to demo progress area (`demo.html`)
- [ ] Footer: use semantic `<footer>` element
- [ ] Touch targets: increase button padding to minimum 44px height (WCAG 2.5.5)
- [ ] Fix `lang` attribute: HTML `lang="en"` must match default JS `LANG="en"`

### 2.5 Color contrast (WCAG AA)

- [ ] `demo.html` light theme `--mut:#5c6880` on `--bg:#f5f7fb` → 3.5:1 (FAILS)
  Fix: darken to `--mut:#4a5568` (~5.5:1)
- [ ] `demo.html` dark theme `--mut:#9aa4c2` on `--bg:#0b1020` → 4.7:1 (borderline)
  Fix: lighten to `--mut:#a8b4d0` (~6:1)
- [ ] Status text and quota text using `--mut` → use higher-contrast color

### 2.6 Mobile responsiveness

- [ ] `index.html`: reduce grid `minmax` from `230px` → `160px`
- [ ] `index.html:132-139`: hero textarea `min-width:300px` → `min-width:0;flex:2`
- [ ] `index.html:100`: tagline hidden on mobile → show as subtitle under title
- [ ] Add tablet breakpoint `@media (max-width:1024px)` for layout adjustments
- [ ] `demo.html`: add `@media` queries (currently has none)
- [ ] `admt_notice.html`: add basic mobile `@media` queries

### 2.7 Footer cleanup

- [ ] Remove hardcoded light-mode colors (`#7a8699`, `#6d7cfa`, `#e0e5ee`)
- [ ] Use CSS variables: `color:var(--mut)`, `border-color:var(--border)`
- [ ] Wrap in semantic `<footer>` element

---

## Phase 3 — TEMPLATE/THEME SELECTION (US/UK developer audience)

### Recommended design direction

**Option A: Vercel/Linear-inspired (Recommended)**
- Clean, minimal, monochrome with accent color
- Dark mode default (developers prefer dark)
- Inter/system font stack
- Lots of whitespace, card-based layout
- Gradient accents (subtle purple/blue)
- This is the dominant aesthetic in the developer tools space (Vercel, Linear, Raycast, Resend)

**Option B: Tailwind UI "Product" template**
- Similar to Option A but with more color
- Pre-built components (hero, pricing, features grid)
- More polished out-of-the-box

**Option C: Stripe Dashboard-inspired**
- More structured, data-dense
- Better for dashboards than landing pages
- Overkill for FluxSwarm's current scope

### Current theme assessment

The existing dark theme (`--bg:#0b1020`, accent `--fg:#6d7cfa`) is already close to Option A. Changes needed:

- [ ] **Font stack**: change to `Inter, system-ui, -apple-system, sans-serif` (add Inter from Google Fonts or self-host)
- [ ] **Border radius**: increase from `8px` → `12px` (modern feel)
- [ ] **Shadows**: add subtle `box-shadow` to cards (currently flat)
- [ ] **Spacing**: increase padding in sections (current `24px` → `40px+`)
- [ ] **Max-width**: increase content area from implicit → `1200px`
- [ ] **Gradient hero**: add subtle gradient background to hero section
- [ ] **Animation**: add `prefers-reduced-motion` respected transitions
- [ ] **Logo**: add a proper SVG logo (currently text-only "FluxSwarm")

### Color palette (Option A — Vercel-inspired)

```
--bg:     #000000  (pure black, like Vercel)
--fg:     #ededed  (light text)
--accent: #0070f3  (Vercel blue) or #6d7cfa (current purple)
--mut:    #888888  (muted text, guaranteed contrast)
--card:   #111111  (card background)
--border: #222222  (subtle borders)
--success:#0cce6b
--error:  #ff0000
--warn:   #f5a623
```

---

## Phase 4 — PRODUCTION READINESS

### 4.1 Backend hardening

- [ ] Add `--timeout-graceful-shutdown 30` to uvicorn in `entrypoint.sh`
- [ ] Document single-replica constraint (server lock + entrypoint `rm -f`)
- [ ] Add server-side logging (currently only audit/stderr prints)
- [ ] WebSocket `/ws/{slug}` rate/concurrency limit (currently unbounded)
- [ ] Verify `dev-complete` mock endpoint + `mock-secret` are behind `_mock_active()` guard
- [ ] Remove or guard any debug/test endpoints in production

### 4.2 Dependencies

- [ ] Run `pip-audit` against `requirements.txt`
- [ ] Bump `fastapi` to latest (currently 0.133.1, ~5 months old)
- [ ] Bump `uvicorn` to latest (currently 0.41.0)
- [ ] Bump `alembic` to latest (currently 1.16.4, ~2 minors behind)
- [ ] Pin `cryptography==50.0.0` (covered in Phase 0)
- [ ] Consider bumping `redis` from 5.x → 6.x (major, test required)

### 4.3 Testing gaps

- [ ] No frontend/E2E tests for `index.html` language switching
- [ ] No WebSocket rate-limit test
- [ ] No test for demo page EN-only mode
- [ ] Run full suite with PG-backed tests via Docker Postgres (27 skipped)
- [ ] Verify `test_paddle_flow_api.py` still passes with webhook rate-limit changes

### 4.4 Security checklist

- [ ] CSP: verify `script-src` is nonce-only (no `unsafe-inline`)
- [ ] CSP: verify `style-src` includes `unsafe-inline` (needed for inline styles + Paddle)
- [ ] HSTS: verify only sent over HTTPS (Render provides HTTPS)
- [ ] `sanitize_goal()` regex: verify covers all injection patterns
- [ ] Webhook rate limit: 10 req/min/IP enforced
- [ ] Structured 429 responses: `{error, message{en}, retry_after_seconds, upgrade_url}`
- [ ] No secrets in git history (verify with `git log -p | grep -i "sk-\|password\|secret\|key"`)

### 4.5 Content & copy

- [ ] Hero tagline: currently "3 free credits then pay-per-use" — verify accurate
- [ ] Pricing section: verify plans match actual Paddle price IDs
- [ ] Legal pages (privacy, terms): verify contact email is correct (`privacy@fluxswarm.ai`)
- [ ] OG meta tags: remove "demo" references, set proper title/description
- [ ] Footer links: all functional, no 404s
- [ ] `README.md`: update for English-only platform

### 4.6 Performance

- [ ] All CSS/JS is inline in templates — consider extracting to static files for caching
- [ ] No image optimization (hero banner, icons)
- [ ] No lazy loading for below-fold content
- [ ] Add `Cache-Control` headers for static assets
- [ ] Consider adding a proper favicon (currently default)

### 4.7 Render deployment

- [ ] `render.yaml`: verify `rootDir: .` and `dockerfilePath: deploy/docker/Dockerfile` are correct
- [ ] `FLUXSWARM_DATABASE_URL`: Neon connection string with `sslmode=require`
- [ ] `REDIS_URL`: Upstash Redis URL
- [ ] Verify health check passes: `GET /health` returns `{"ok":true}`
- [ ] Verify cold start time < 60 seconds
- [ ] Set up Cloudflare for HTTPS + CDN (optional but recommended)

---

## Phase 5 — POST-LAUNCH (nice-to-have)

- [ ] Extract CSS/JS to static files with cache headers
- [ ] Add proper SVG logo + favicon
- [ ] Add Inter font (self-hosted for privacy)
- [ ] Add `prefers-reduced-motion` respect
- [ ] Add tablet breakpoint (640-1024px)
- [ ] Add loading skeletons everywhere
- [ ] Add proper error boundary in JS
- [ ] Add `aria-live` regions for dynamic content
- [ ] Add proper modal for delete confirmation (replace `prompt()`)
- [ ] Add proper modal for template goal input (replace `prompt()`)
- [ ] Consider adding WebSocket reconnection logic
- [ ] Consider adding offline detection toast
- [ ] Analytics integration (Plausible/Fathom — privacy-friendly)
- [ ] Consider adding a proper changelog/release notes page

---

## Execution order

| Priority | Phase | Estimated effort |
|----------|-------|-----------------|
| P0 | Phase 0 — Critical fixes | 1-2 hours |
| P0 | Phase 1 — English only | 2-3 hours |
| P1 | Phase 2 — UX/UI fixes | 4-6 hours |
| P1 | Phase 3 — Theme polish | 2-3 hours |
| P2 | Phase 4 — Production readiness | 3-4 hours |
| P3 | Phase 5 — Post-launch | Ongoing |

**Total estimated: 12-18 hours of focused work before launch.**
