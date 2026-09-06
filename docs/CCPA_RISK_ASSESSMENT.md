# CCPA/CPRA Automated Decision-Making Technology (ADMT) — Risk Assessment

FluxSwarm uses AI agents to generate code from a user's goal description. This
document is the operator's ADMT risk assessment required by the CCPA/CPRA
regulations (California Code of Regulations Title 11 § 999.403-999.407).
Applicable to FluxSwarm US/UK SaaS, effective 2026-09-04.

---

## 1. ADMT Inventory

FluxSwarm dispatches a fixed squad of six agents per launch. Their decision
scope is bounded and every output is a draft requiring human review.

| Agent (profile)         | Decision scope                                                                                     | ADMT risk |
|-------------------------|----------------------------------------------------------------------------------------------------|-----------|
| `ecc-planner`           | Decomposes the user's goal into dependency-ordered milestones and chooses methodology (monolith vs microservices) | Low — reversible, code only |
| `ecc-architect`         | Selects the software stack, module boundaries and data layout from goal signals                     | Low — code only |
| `ecc-devops`            | Designs deployment/infrastructure layout and execution isolation (sandboxed, network-isolated containers) | Low — code only |
| `ecc-tdd`               | Writes test cases first, then implementation code to satisfy them                                   | Low — code only |
| `ecc-reviewer`          | Self-evaluates generated code against acceptance criteria and flags defects                         | Low — code only |
| `ecc-build-fixer`       | Synthesizes the final buildable result from reviewed work                                           | Low — code only |

**Decision type:** exclusively content generation (recommendation/action on
code artifacts). No decisions are made about a consumer's eligibility,
pricing, or access — all substantive business decisions remain human-mediated.

### 1a. Processing providers and DPAs

The goal description (and the generated code/error logs the agents evaluate) is
transmitted to the AI provider selected for that launch — user-chosen (BYOK) or,
for the public demo surface, the first healthy entry of the demo provider pool.
Processing contracts:

| Provider  | Purpose                                                    | Agreement / DPA |
|-----------|------------------------------------------------------------|-----------------|
| Google    | Model inference for a user's own launch                    | [Google DPA](https://cloud.google.com/terms/data-processing-addendum) |
| Anthropic | Model inference for a user's own launch                    | [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum) |
| OpenAI    | Model inference for a user's own launch                    | [OpenAI DPA](https://openai.com/policies/data-processing-addendum/) |

No provider receives data for training, profiling, or advertising. Data
transfers to providers operate under the provider's own terms (any such transfer
is limited to the minimum input/output needed to render the requested output).
The demo surface abides by the same policies as paid launches; the demo
provider pool only ever receives an operator-approved, free-tier model's worth
of the same goal text. Production deployments refuse to start without a paid
provider key or BYOK (see `backend/envguard.py`).

## 2. Data Minimization

| Data element   | Why necessary                                                                                      | Retention                                        |
|----------------|----------------------------------------------------------------------------------------------------|--------------------------------------------------|
| Email address  | Account identity, authentication, and legal/rights correspondence (ADMT opt-out, review outcomes)  | 90 days after account deletion (audit trail)     |
| Display name   | Account personalization                                              | Same as above                                    |
| Project goal   | The user-provided input passed to the AI squad (the deliverable is derived from it)                | Same as above                                    |
| Launch/decision records | Required for the ADMT "logic disclosure" right (see `GET /api/projects/{id}/admt-logic`)    | Same as above                                    |

No special-category data is intentionally collected, inferred, or sold. Goals
are used solely to produce the requested output and are namespace-isolated per
account.

## 3. Security Measures

- **Encryption at rest:** provider keys sealed with AES-256-GCM envelope
  encryption via a KMS backend (AWS KMS / Azure Key Vault / HashiCorp Vault in
  production; dev-only file backend). Passwords stored as Argon2id hashes.
  JWT secrets and Fernet KEKs generated and held in env/secret store.
- **Encryption in transit:** all traffic over TLS (Caddy/nginx terminator);
  Paddle webhooks verified via raw-body HMAC-SHA256 signature (±300s window).
- **Access controls:** per-account namespace isolation (slug ownership guard,
  403 on cross-user access), admin surface behind a shared-secret bearer token
  (`FLUXSWARM_ADMIN_TOKEN`, deny-by-default), CORS strict allow-list, IP-based
  rate limiting and login failure lockout.
- **Audit logs:** append-only JSONL (`audit.jsonl`) records auth, launch,
  rectification, opt-out/opt-in, ADMT notice acknowledgments, review requests
  and review decisions. Audit records are excluded from deletion requests.

## 4. Human Review Workflow

- **Trigger:** a consumer requests review of a launch via
  `POST /api/projects/{id}/request-human-review`, or the operator opens a
  review after a reported concern. Requests land in the admin queue
  (`GET /api/admin/human-review-queue`).
- **Routing:** queue is visible only to authenticated admins; each item carries
  the user, project, goal and request time.
- **SLA:** 48 hours from `requested_at` to a decision
  (`approved` / `rejected` / `needs_changes`).
- **Outcome delivery:** the requesting user is notified by email (audit-recorded;
  a mailer hook consumes the same message) or Telegram when the account is
  linked and a bot token is configured.
- **Escalation:** items unresolved within 72 hours are escalated to the
  privacy/security lead; consumers can always contact
  [privacy@fluxswarm.ai](mailto:privacy@fluxswarm.ai).

## 5. Annual Review Schedule

This risk assessment is reviewed annually and upon any material change to the
squad, the data model, or governing regulation.

- Next scheduled review: **2027-01-01**
- Owner: FluxSwarm privacy/security lead (contact below).

## 6. Contact

All ADMT inquiries, right-to-disclosure requests, and review escalations:

- **Email:** [privacy@fluxswarm.ai](mailto:privacy@fluxswarm.ai)
- **In-app:** account export (`GET /api/account/export`), ADMT notice
  (`GET /api/account/admt-notice`), and per-project logic disclosure
  (`GET /api/projects/{id}/admt-logic`).