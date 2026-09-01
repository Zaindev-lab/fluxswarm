# LICENSE-AUDIT — FluxSwarm

Dependency licenses for the pinned `backend/requirements.txt` (as of the pinned
versions). Commercial SaaS distribution model: **server-hosted, not distributed** —
GPL/LGPL copyleft obligations (source offering) only trigger on *distribution* of
the covered work; hosting-as-a-service is not distribution. Verify legal advice
for your specific contracts; this is engineering fact, not counsel.

| Dependency | Pinned | License | Notes |
|------------|--------|---------|-------|
| fastapi | 0.133.1 | MIT | safe |
| uvicorn[standard] | 0.41.0 | BSD-3-Clause | safe |
| websockets | 15.0.1 | BSD-3-Clause | safe |
| pydantic | 2.13.4 | MIT | safe |
| Jinja2 | 3.1.6 | BSD-3-Clause | safe |
| httpx | 0.28.1 | BSD-3-Clause | safe |
| PyJWT | 2.13.0 | MIT | safe |
| argon2-cffi | 25.1.0 | MIT | safe |
| redis | 5.2.1 | MIT | safe |
| python-telegram-bot | 22.8 | **GPL-3.0-only** | ⚠️ aggregator-process use; SaaS hosting ≠ distribution, but any redistribution of the image/source or copyleft-combined modifications triggers strong obligations. Isolate & document. |
| aiohttp | 3.14.3 | Apache-2.0 | safe |
| cryptography | >=42.0.0 | Apache-2.0 OR BSD-3-Clause | safe |

## AI-provider / model licenses (OUT of repo)
Hermes agent profiles and any model weights/platform are external (Hermes install
at `C:/Users/DELL/AppData/Local/hermes`, provider cards). Their licenses are the
providers': not auditable from this repo — record each provider card in Phase 13
(provider = license holder; user BYOK places terms on the user).

## First-party license
- No `LICENSE`/`COPYING` file in the repo. For a private SaaS codebase this
  defaults to all-rights-reserved — acceptable if intentional; if any part is
  meant to be open-sourced, add an explicit license (else contributors hold rights).

## Compliance posture
- No copyleft contamination in the app source today (all app code is first-party).
- Container image redistribution (public registry / CI artifacts) is where
  GPL-3.0 (python-telegram-bot) becomes relevant. Keep the telebot service image
  private, or vendor GPL distribution obligations. **MEDIUM flag** for Phase 12/15.
- `cryptography>=42` loose pin is dual-licensed; harmless for the product.

## Verdict
Engineering-compliant for a hosted SaaS if images stay private. Action items:
1) add first-party LICENSE or explicit NOT-LICENSED notice; 2) isolate the
telegram-bot GPL dependency (container + doc); 3) record provider/model licenses
at launch (Phase 11/13).