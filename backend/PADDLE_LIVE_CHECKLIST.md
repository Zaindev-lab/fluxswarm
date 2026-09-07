# FluxSwarm — Paddle LIVE Go-Live Checklist

> Purpose: the exact steps to move `backend/payments.py` from the **Paddle
> sandbox** (already verified end-to-end) to the **live account**, including the
> merchant-verification documents Paddle will ask for.
>
> Lead time: Paddle recommends starting live verification **before** finishing
> integration. Manual review ≈ 2–4 business days (business) + 1–3 business days
> (identity). Start early, keep testing on sandbox meanwhile.

## 0. Product prerequisites (do these first)

- [ ] Permanent domain + TLS in production (Caddy auto-TLS). `localhost` and the
      ephemeral `*.trycloudflare.com` tunnel are **not** accepted for live.
- [ ] Webhook URL is stable: `https://<your-domain>/api/payments/webhook`.
- [ ] Fill the operator entity in `.env` so the legal pages disclose the seller:
      `FLUXSWARM_LEGAL_ENTITY`, `FLUXSWARM_LEGAL_REGISTRY_NO`,
      `FLUXSWARM_LEGAL_TAX_ID`, `FLUXSWARM_LEGAL_ADDRESS`
      (rendered on `/privacy`, `/terms`, `/privacy-en`, `/terms-en`; restart the
      backend after editing `.env`).
- [ ] Sales-tax posture: none to set up — **Paddle is the Merchant of Record** and
      handles VAT/GST/sales tax collection + remittance in 100+ jurisdictions.

## 1. Sign up for the live account

1. Create the Paddle **live** account (separate from sandbox) and confirm email.
2. Add yourself/business with the legal details — this is the data Paddle verifies:
   - legal business name · commercial registration number · **tax/VAT number**
   - registered address (city, country)
   - ownership breakdown (any owner with **>25%** — names + percentages)

## 2. Domain + business + identity verification

| Phase | What Paddle checks | What you provide |
|---|---|---|
| **Domain review** | your real domain serves check out / is legitimate | the live domain itself |
| **Business verification** | legal name/address/registration/ownership | **government-issued business registration document (PDF)** listing owners with >25%; a **share/ownership breakdown**. **Not accepted:** EIN/tax IDs, utility bills, accounting docs. |
| **Identity verification** | an owner (or you, if sole trader) is real | passport/ID + proof of address via **Sumsub** (possible short liveness check) |

- [x] / [ ] Reply to verification emails promptly; use PDFs; public-registry links are OK if no login required.

## 3. Live business settings (Dashboard)

- [ ] **Currencies > balance currency** — pick the currency of your payout bank account.
- [ ] **Checkout > Sales tax settings** — inclusive vs exclusive prices (typically *exclusive* when selling to businesses / sales-tax regions).
- [ ] **Checkout > Payment methods** — card is always on; enable the others you want.
- [ ] **Checkout > Default payment link** — set it to your Paddle.js checkout page (must be the verified live domain).
- [ ] **Business account > Payouts > Payout settings** — payout via **bank transfer**, **PayPal**, or **Payoneer**; set the minimum threshold (payouts run on the 1st of the month, once the balance clears the threshold).

## 4. Live keys + product catalog

1. Sandbox and live catalogs are **separate** → create **live** price IDs for
   starter/pro/scale and record them.
2. **Developer tools > Authentication**: create live `API key` + `client-side token`.
3. **Developer tools > Webhooks**: register
   `https://<your-domain>/api/payments/webhook` with events
   `transaction.completed` and `adjustment.created` (Paddle v1 refunds arrive as
   `adjustment.created`), **api_version = 1**; grab the `endpoint_secret_key`.
4. Sanity test auth: `GET /event-types` with the live key → 200.

## 5. Swap credentials (this repo)

In the production `.env` (never in the repo — it is gitignored):

```text
FLUXSWARM_PAYMENT_PROVIDER=paddle
PADDLE_API_BASE=https://api.paddle.com           # NOT sandbox-api
PADDLE_API_KEY=<live api key>
PADDLE_CLIENT_TOKEN=<live client-side token>
PADDLE_WEBHOOK_SECRET=<live endpoint_secret_key>
PADDLE_PRICE_STARTER=<live pri_...>
PADDLE_PRICE_PRO=<live pri_...>
PADDLE_PRICE_SCALE=<live pri_...>
PADDLE_PRICE_TOPUP=<live pri_...>   # optional $9/10-credit refill pack
FLUXSWARM_PUBLIC_BASE_URL=https://<your-domain>
FLUXSWARM_PAYMENTS=1                             # opens the billing gate
FLUXSWARM_LEGAL_ENTITY=...                        # see §0
```

- [ ] Remove/unset `FLUXSWARM_PADDLE_MOCK` — the sandbox free-credits endpoints
      refuse to run whenever live credentials are present (guard in
      `payments.py`), but leaving it is sloppy.
- [ ] Re-run `deploy/bootstrap.sh` so the env is re-read; confirm `/health` 200.
- [ ] Verify webhook POST returns `400` on a bad signature (gate operative), not `503`.

## 6. Live smoke test (small, real money)

1. Register a throwaway user (`email+tag@…`), open `/checkout` for Starter.
2. Pay the minimum genuinely (a couple of dollars) — refund test money afterwards.
3. Confirm: `transaction.completed` webhook → plan upgraded, credits granted,
   entry in `backend/data/audit.jsonl` (no secrets — verified by
   `test_audit_trail_strips_sensitive_keys`).
4. Issue a **refund** from the live dashboard → `adjustment.created (type=refund)`
   arrives → plan downgrades to `demo`, credits are **kept** (never clawed back).
5. Replay the same webhook → `dup=true`, no double grant.

## 7. Rollback (if anything misbehaves)

- Revert `.env` to the sandbox block (§5 sandbox values), unset `FLUXSWARM_PAYMENTS`
  (or `=0`), remove `FLUXSWARM_PADDLE_MOCK` stays unset, restart, verify `/health`.
- Sandbox + live API keys are independent — a bad live key never affects sandbox.

## 8. Every launch, forever

- `deploy/backup.sh` nightly (optionally age-encrypted, see script comments).
- `deploy/monitor.sh` in cron (defaults to `127.0.0.1:8787/health`).
- `backend/rotate_secrets.py` before/after any key rotation; restart after.
- All legal pages: `/privacy` `/terms` `/privacy-en` `/terms-en` show the entity.