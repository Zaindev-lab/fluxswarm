# FluxSwarm — المعمارية الكاملة (Architecture)

> **STALE — superseded by `ARCHITECTURE_v2.md`** (PostgreSQL/Alembic, KMS
> envelope BYOK, no free tier, Docker dispatch, CCPA/CPRA ADMT, dev-container
> parity). Kept for history.
>

> وثيقة مراجعة شاملة للمشروع، تُراجع في Kimi. تغطي: المنتج، التقنيات، الوحدات، مخطط البيانات، الأمان، المحرك (Agent Runtime)، الفوترة (Paddle)، النشر، والأسئلة المفتوحة.
> الحالة: **Sandbox Paddle E2E مكتملة وموثقة** (دفعة حقيقية + توقيع + منح ترقية). الخطوة التالية: الترويج لمفاتيح Live ثم النشر على VPS.

---

## 1. نظرة عامة (Product Overview)

FluxSwarm هي منصة SaaS تتيح للمستخدم وصف **مشروع/هدف** بلغة طبيعية (عربي/إنجليزي)، فتُطلق **«سرباً» (Swarm) من وكلاء ذكاء اصطناعي** (Planner → Architect → DevOps → TDD → Reviewer → Builder) عبر أداة **Hermes CLI** لبناء التطبيق فعلياً وإخراج الأكواد في مساحات عمل قابلة للتصفح.

- **النموذج التجاري:** Freemium — خطة Demo مجانية (3 اعتمادات)، وخطط Starter/Pro/Scale مدفوعة عبر **Paddle Merchant-of-Record** (يدير الضرائب/الامتثال).
- **نموذج الاستهلاك:** «اعتماد» (Credit) واحد لكل إطلاق (Launch). لا يمكن للمستخدم تفعيل أكثر من موازٍ وفق خطته (Demo=1، Starter=2، Pro=4، Scale=6).
- **BYOK (Bring Your Own Key):** المستخدم يستطيع إدخال مفتاح مزوّد (Anthropic/OpenAI/Gemini/Kimi) يُشغَّل السرب عليه (المستخدم يدفع للمزوّد مباشرة)، مخزن مشفَّراً بـ Fernet.
- **الخلفية التشغيلية للمُشغِّل:** `FLUXSWARM_DEMO_MODE=1` فقط يسمح بـ opencode-free كرنّتايم افتراضي؛ الإنتاج فاشل سريعاً (fail-fast) إن لم يُضبط مزوّد/نموذج.

---

## 2. حزمة التقنيات (Tech Stack)

| الطبقة | التقنية |
|---|---|
| اللغة | Python 3.11 (arpn venv في `C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv`) |
| الويب | FastAPI + Uvicorn (عملية واحدة على Windows، نمط `run.ps1`) |
| الواجهات | Jinja2 + HTML المضمّن في `main.py` (صفحات قانونية + `/checkout`) + `static/` |
| قاعدة البيانات | SQLite (`backend/data/users.db`) — عمليات ذرّية عبر `BEGIN IMMEDIATE` |
| التخزين الإضافي | `data/audit.jsonl` (سجل أحداث)، `data/byok.json` (مفاتيح مشفرة)، `data/.jwt_secret` |
| الأمان | Argon2id (كلمات المرور)، JWT (HS256)، Fernet (تشفير BYOK)، PyJWT، `cryptography` |
| ML/Agents | Hermes CLI (`hermes.exe`) كعملية فرعية؛ السرب يركض على opencode (نموذج `big-pickle` في demo، أو `hy3-free`/`opencode-free` للترقية المجانية) |
| العميل | httpx (provider health)، subprocess (هرمس) |
| DEV/CI | pytest، `py_compile`، GitHub Actions (`.github/workflows/ci.yml`) |
| النشر | `deploy/bootstrap.sh` (Ubuntu VM)، Caddy/nginx، Dockerfile + docker-compose.beta |

**المضيف التطويري الحالي:** Windows 11، النفق `trycloudflare` → `127.0.0.1:8787`.

---

## 3. مخطط عام (High-Level Flow)

```
المستخدم (Browser)
   │  https://<domain>  (Caddy/nginx على VM، أو نفق trycloudflare في التطوير)
   ▼
FastAPI backend (uvicorn, port 8787)
   ├─ auth.py      توقيع/فحص JWT  (HS256, secret حي إلزامي في الإنتاج)
   ├─ ratelimit    حد كلمات/FER + حد IP + حد تسجيل + حد شراء (Redis أو memory)
   ├─ db.py        SQLite: users/projects/referrals/payment_events/...
   ├─ vault.py     تشفير مفاتيح BYOK بـ Fernet (مفتاح خارج الريبو في الإنتاج)
   ├─ payments.py  PaddleGateway: create_checkout + verify_paddle_signature + webhook
   ├─ hermes_client.py  جسر إلى Hermes: boards + dispatch + kill trees + outcomes
   ├─ provider.py  فحص صحة المزوّد (لا يستهلك اعتمادات)
   └─ telegram_bot.py  ربط Telegram بحسابات المستخدمين (عملية مستقلة)

Paddle (MoR)
   ├─ /api/subscribe/{plan}  ←  إنشاء Checkout الحالي وإرجاع checkout_url
   └─ https://<domain>/api/payments/webhook  ←  transaction.completed / refunded
        للتحقق من التوقيع (ts=;h1= HMAC-SHA256) ثم منح/إلغاء
```

---

## 4. شجرة المشروع (Repository Layout)

```
fluxswarm/
├── README.md, GTM_LAUNCH_PLAN.md           # طلقة الإطلاق وخطة GTM
├── ARCHITECTURE.md                         # (هذه الوثيقة)
├── Dockerfile, docker-compose.yml, .env.example
├── Caddyfile, nginx.conf                   # قوالب عكس-بروكسي
├── deploy/
│   ├── bootstrap.sh                        # تثبيت كامل لـ Ubuntu VM + systemd
│   ├── Caddyfile.us, monitor.sh, backup.sh, restore.sh
│   └── docker/{Dockerfile, entrypoint.sh, docker-compose.beta.yml}
├── audit/                                  # بوابات المراجعة GATE-1..GATE-4 (وثائق)
├── backups/                                # أرشيفات النسخ الاحتياطي ZIP
└── backend/
    ├── main.py          (≈1900 سطر) كل المسارات + middleware + صفحات
    ├── auth.py          JWT + فحص الإخراج الجماعي (logged_out_at)
    ├── db.py            (941 سطر) طبقة SQLite الوحيدة المعتمدة
    ├── hermes_client.py (1272 سطر) جسرHermes + dispatch + reconciliation
    ├── payments.py      PaddleGateway + التحقق من التوقيع + السمات الاحتياطية
    ├── provider.py      فحص صحة المزوّد (enum وضع)
    ├── vault.py         Fernet BYOK ISO
    ├── billing.py       محوّل بوابة الفوترة (paddle)
    ├── ratelimit.py     Redis|memory خلفيتان متماثلتان
    ├── audit.py         سجل append-only JSONL، تنقية/قص تلقائي
    ├── security.py      كاشف ثوابت أسرار في الكود (static)
    ├── envguard.py      رفض الإقلاع ناقص الأسرار في الإنتاج
    ├── serverlock.py    قفل عملية واحدة
    ├── rotate_secrets.py, backup.py, telegram_bot.py, validate_paddle.py
    ├── data/            users.db, audit.jsonl, byok.json, .jwt_secret
    ├── templates/       index.html (+ قوالب الصفحات)
    ├── static/          robots.txt, sitemap.xml
    └── tests/           (75 اختباراً ناجحاً حالياً)
```

---

## 5. طبقة البيانات (Data Layer — `db.py`)

SQLite مُفعَّل `PRAGMA foreign_keys=ON`. كل عمليات تغيير الأرصدة داخل `BEGIN IMMEDIATE` (بدون سباق).

| الجدول | أهم الأعمدة | الغرض |
|---|---|---|
| `users` | email UNIQUE, pw_hash (Argon2id), plan, credits, ref_code, referred_by, logged_out_at | الحسابات والخطط والأرصدة |
| `projects` | board_slug (عزل حسب user), goal, launch_status/outcome/reason/refunded | مشاريع تنسّق إلى Hermes boards |
| `referrals` | referrer_code, referred_email, rewarded | مكافأة مرة واحدة لكل إحالة |
| `squad_templates` + `template_purchases` | agents JSON, price_credits | سوق القوالب + الشراء (50% للمؤلف) |
| `payment_events` | event_id UNIQUE (idempotency)، gateway, kind, user_id, detail(JSON, فيه txn) | سجل المدفوعات/استرجاع idempotent |
| `telegram_links` + `telegram_codes` | chat_id ↔ user_id | ربط Telegram |
| `password_resets` | token_hash (SHA-256 فقط، قابل للاستخدام مرة واحدة، انتهاء 900s) | استعادة كلمة المرور |
| `demo_usage` | PK (who, day) | سقف يومي لإساءة استخدام Demo |

نقاط عشوائية ضد السباق موثّقة في الكود: `deduct_credit` (سحب اعتماد واحد)، `refund_launch_credit`، `reward_referrer_once`، `buy_template/refund_template_purchase`.

كلمات المرور: **Argon2id**؛ الهاشات القديمة `sha256(salt+pw)` ما زالت متحققة وتُرقّى في نفس الجلسة (silent upgrade في `authenticate`). الرموز المسترجعة لا تُفكَّك أبداً (SHA-256 at rest).

---

## 6. المصادقة والجلسات (`auth.py`)

- **JWT HS256**، مدة 7 أيام، يوقّع بالسر من `FLUXSWARM_JWT_SECRET` (إلزامي في الإنتاج؛ `envguard.assert_production_secrets()` يرفض الإقلاع ناقصه).
- **إبطال جماعي:** `logged_out_at` (عائم، ليس عدداً صحيحاً) — أي توكن `iat` أسبق من قيمة الجدول يُرفض (logout / تغيير كلمة مرور / reset تقتل كل الجلسات).
- Reset token: عشوائي 32 بايت، يُخزَّن SHA-256 فقط، `RESET_TTL=900s`، single-use، تنظيف في كل إصدار.
- Rate limits على المسارات الحساسة: 5 فشل تسجيل دخول/900s لكل ip+email؛ 20 طلب تسجيل/IP/60s؛ 10 تسجيلات/IP/ساعة؛ 5 شراء قوالب/مستخدم/ساعة.

---

## 7. الأمان (Security Posture)

1. **envguard (رفض فوري في الإنتاج):** بدون `FLUXSWARM_FERNET_KEY` و`FLUXSWARM_JWT_SECRET` (إذا `FLUXSWARM_DEMO_MODE != 1`) يرفض التطبيق الإقلاع — لا أسرار مؤقتة في الإنتاج.
2. **CSP إلزامي لكل الاستجابات** (`main.py:196`): `script-src 'self' 'unsafe-inline' https://cdn.paddle.com`، `frame-src` يشمل `buy/sandbox-buy/checkout/sandbox-checkout.paddle.com` (مطلوب لـ Paddle Overlay)، `X-Frame-Options: DENY`، `frame-ancestors 'none'`، `Cache-Control: no-store` لكل `/api`.
3. **CORS:** قائمة صريحة فقط من `FLUXSWARM_CORS_ORIGINS`؛ `*` يعتبر خطأ إقلاع (RuntimeError). بدون قيمة = same-origin فقط.
4. **X-Forwarded-For موثوق:** `FLUXSWARM_TRUSTED_PROXIES` (IP/CIDR) فقط يُقبل منه XFF لاشتقاق IP العميل — يمنع تهريب معدل الـ limiter.
5. **rate limiting** بـ Redis (متعدد العمال) أو memory متماثل.
6. **vault:** مفاتيح BYOK مشفرة بـ Fernet؛ المفتاح في `~/.fluxswarm/fernet.key` (خارج الريبو) أو env؛ ترحيل لمرة واحدة من `data/.fernet_key` القديم ثم حذفه.
7. **serverlock:** عملية واحدة عبر قفل — يمنع ازدواج uvicorn (مصدر أوبئة «المشرف القديم»).
8. **audit JSONL:** يسجّل أحداثاً حساسة؛ ينقي القيم الحساسة (tokens/keys/secrets) ويقص الطويلة (**<=4000 بايت/سجل**).
9. **chatgpt إبراز: webhook Paddle لا يثق إلا بالتوقيع** — إعادة توجيه العميل لا تثبت دفعاً أبداً (المبدأ مركزي في `handle_webhook`).

---

## 8. المحرك — جسر Hermes (`hermes_client.py`)

- **الخرائط:** مشروع = لوحة Hermes كانتسل منفصلة عزلت سربها (`--board <slug>`، slug آمن `[A-Za-z0-9_-]{1,120}`).
- **السرب:** 4 عمال `ecc-planner/ecc-architect/ecc-devops/ecc-tdd` + مُراجع `ecc-reviewer` + مُجمِّع `ecc-build-fixer`. `AGENT_REGISTRY` يربط أسماء العرض في السوق بالأسماء الداخلية + المهارات.
- **حل الرنّا تايم (`_resolve_runtime`):** (1) `opencode-free` المختار صراحةً يغلب أي BYOK؛ (2) BYOK → نموذج المزوّد عبر `FLUXSWARM_MODEL_<PROVIDER>` أو الافتراضي؛ (3) بلا شيء → `FLUXSWARM_DEFAULT_PROVIDER/MODEL`؛ وفي الإنتاج بلا إعداد ⇒ `ProviderConfigError` (لا صمت، لا free افتراضياً).
- `FLUXSWARM_DEMO_MODE=1` فقط يجعل free الافتراضي (نموذج `big-pickle` — هذه الجلسة تعمل عليه).
- **dispatch loop:** حلقة متواصلة حتى حالة نهائية؛ `DISPATCH_TIMEOUT_S=900` سقف صلب + كاشف التعطّل (no-progress) يتوقف قبل ذلك؛ يقتل شجرة العمليات عبر `kill_process_tree` (Win32 عبر `_get_child_pids_win32`).
- **نتائج الإطلاق:** `board_has_completed_work` + `launch_status/outcome/reason/refunded` في المشروع: يستحق استرداد اعتماد إن لم يكتمل عمل حقيقي أبداً، ولا يُستردّ مرتين.
- **قراءة المخرجات:** `show_task`, `read_workspace`, `read_worker_pids` لعرض المنتجات في الـ UI.
- **سقف Demo:** `bump_demo_usage("anon"/"u{id}", day)` بـ `FLUXSWARM_DEMO_DAILY_CAP=25` إفتراضي — مساحات العرض تكلّف، فلا يمكن أن تكون بلا حدود.

---

## 9. الفوترة — Paddle MoR (`payments.py`)

### 9.1 تدفق الاشتراك
`POST /api/subscribe/{plan}` (أو GET):
- الحارس `FLUXSWARM_PAYMENTS=1` مطلوب؛ وإلا `402 billing_gate_closed` (بوابة مطوّر مغلقة افتراضياً).
- `PaddleGateway.create_checkout()` → Paddle API v1 (`Bearer PADDLE_API_KEY`, `Paddle-Version: 1`) → `POST /transactions` مع `custom_data {plan, user_id}` + `PADDLE_PRICE_<PLAN>` → يرجع `checkout_url`.
- صفحة `/checkout` (Jinja): تحمّل SDK **v2** `https://cdn.paddle.com/paddle/v2/paddle.js` → `Paddle.Initialize` (client token) → `Paddle.Checkout.open({transactionId, displayMode: 'overlay'|'inline'})`. الشبكة تفرض CSP تسمح بـ `*.paddle.com` + iframe من `sandbox-buy.paddle.com` (في sandbox) مع أدوات تشخيص (`fsStatus`/`fsLog`/retrySdk مع 3 محاولات).
- يوجد `POST /api/subscribe/{plan}` → `{"checkout_url": ..., plan: ...}`.

### 9.2 الويب‌هوك — التحقق من التوقيع
`POST /api/payments/webhook` (دليل أن Signature-صادق فقط يُؤتمن):

```python
verify_paddle_signature(payload: bytes, sig_header, secret, now=None)
# Scheme 2 (الحالي): "ts=<unix>;h1=<hex>" => h1 = hex(hmac_sha256(secret, f"paddle-{int(ts)};{body_text}"))
#   مع نافذة replay ±300s (replay protection). الجسم = body.decode("utf-8","replace") بدقّة
#   (تحقّقنا: لا يجوز التوقيع على bytes مباشرة؛ {body} في f-string يحوّلها إلى b'...'!).
# Scheme 1 (التراثي): base64(hmac_sha256(body)).
```

- **Idempotency:** `record_payment_event(event_id=..., UNIQUE)` — إعادة تسليم الحدث لا تمنح مرتين (دليلنا: replay → `{"accepted":true,"deduplicated":true}`).
- **السمات (Attribution):** الأساسية من `custom_data {user_id, plan}`؛ **معادلات احتياطية** (أُضيفت لدفعات الـ dashboard): المستخدم عبر `customer.email`←`db.get_user_by_email`، والخطة عبر مطابقة price_id الرابح مع `PADDLE_PRICE_{STARTER,PRO,SCALE}`.
- **نتائج الحماية:**
  - `transaction.completed` → `upgrade_plan` + اعتمادات (بدون تخفيض معمول للأرصدة المدفوعة، وبلا تعبئة متكررة لنفس الفئة).
  - `refunded/refund/chargeback` → `downgrade_subscription("demo")`؛ ربط بمعاملة عبر `payment_user_by_txn` (الدفعة القديمة لا تحمل custom_data في adjustment).

### 9.3 درس Sandbox الموثّق (مهم للمراجع)
حدث `transaction.completed` حقيقي (`evt_01m17qtfpmdrwvbtzbrgmr2d16`, txn `txn_01m17nb6xkp8z2xrzjw1mtb8yy`, $99 Pro) رُفضت تسليماته كـ `bad_signature`. بعد تتبّع:
1. **السبب الجذري الأول:** الخادم الحي كان يحمل **سرّ ويب‌هوك قديماً** (قبل تحديث `.env` إلى `...c3LT`). النظام ثابت بمجرد مطابقة السر.
2. **السبب الجذري الثاني (فحصي):** المفحص كان يوقّع `f"paddle-{ts};{body}"` مع `body` بايتات ⇒ تتحول إلى `b'{...}'` — توقيع خاطئ هندسياً، لا علاقة له بالخادم.
النتيجة النهائية: **قبول الويب‌هوك، `demo@fluxswarm.ai ← pro/120`، سجلّ audit `ok`، Idempotency مؤكدة.** وإعادة تحقّق غير متصلة/متصلة 1:1.

---

## 10. الإحالات و السوق (`db.py` + مسارات main)

- `ref_code = "FLX-" + hex(16)`؛ المكافأة **25 اعتماداً مرة واحدة لكل بريد** عند أول اشتراك مدفوع يبلغ البوابة (بلا ازدواج).
- سوق القوالب: `POST /api/templates` يدخّن `agents[]` (أسماء عرض)؛ `buy` يخصم `price_credits` من المشتري ويمنح المؤلف 50%؛ `refund_template_purchase` يستعيد كامل المبلغ ويستقطع حصة المؤلف (عدم إنشاء اعتمادات على فشل).

---

## 11. حفظ البيانات والخصوصية (CCPA/CPRA)

- `GET /api/account/export` → حمولة كاملة عن المستخدم (users/projects/referrals/templates/purchases/payment_events/telegram_links).
- `DELETE /api/account` → حذف كامل مع حذف مفاتيح BYOK من vault (الفروع قبل المستخدم بترتيب FK-safe).
- صفحات قانونية ثنائية اللغة (AR/EN): `/privacy`, `/terms`, `/refund`, `/cookies`, `/acceptable-use`, `/pricing`, `/faq` + `sitemap.xml`/`robots.txt`.

---

## 12. الملاحظة (Observability & Ops)

- `/health` → {ok, version, db, hermes_bin_ok, limiter_backend, pid, uptime}.
- `/api/provider/health` → فحص (config/auth/unavailable/timeout/model_unavailable/success) **بدون استهلاك اعتمادات**.
- `audit.jsonl` — التتبّع أمني؛ `backup.py` snapshots (SHA-256) + `deploy/backup.sh`/`restore.sh`/`monitor.sh`. `rotate_secrets.py` لتدوير JWT+Fernet بأمان.
- Windows: `run.ps1` — محمّل `.env` (Key=Value، نزع الاقتباسات/المسافات)، فحص `/health`، إعادة إقلاع تلقائي، **مشهد مكرر واحد فقط** (بالبوابة).

---

## 13. النشر (Deployment)

- **`deploy/bootstrap.sh`** (Ubuntu VM): تثبيت Python+systemd+Caddy; يُنشئ nginx/Caddy; يعرض نطاق + بريد لتوليد TLS تلقائياً. `DEPLOY-US.md` موثّق بخط اختيار 2 vCPU/4GB→8vCPU/16GB لكبير التزامن.
- **Docker**: `Dockerfile` + `deploy/docker/docker-compose.beta.yml` + `entrypoint.sh` (ضع `FLUXSWARM_HERMES_BIN=/app/hermes/bin/hermes`).
- **CI**: `.github/workflows/ci.yml` — pytest + py_compile.
- **السرّيات:** كل الأسرار في `.env` المحلي (لايُرسل عبر شات عند المراجعة)؛ النشر الإنتاجي يتطلب `FLUXSWARM_JWT_SECRET & FLUXSWARM_FERNET_KEY` و`PADDLE_LIVE_*` ومفاتيح المزوّد.

---

## 14. متغيرات البيئة الرئيسية (env map)

| المتغير | الدور |
|---|---|
| `FLUXSWARM_DEMO_MODE` | يسمح بالأسرار المؤقتة + opencode-free كإفتراضي (dev فقط) |
| `FLUXSWARM_JWT_SECRET` / `FLUXSWARM_FERNET_KEY` | إلزاميان في الإنتاج |
| `FLUXSWARM_DEFAULT_PROVIDER/MODEL` + `FLUXSWARM_MODEL_<P>_` | رنّا تايم افتراضي الإنتاج |
| `FLUXSWARM_PAYMENTS`, `FLUXSWARM_PAYMENT_PROVIDER=paddle` | بوابة الفوترة |
| `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_CLIENT_TOKEN`, `PADDLE_PRICE_{STARTER,PRO,SCALE}`, `PADDLE_API_BASE` | Paddle (sandbox/live) |
| `FLUXSWARM_PUBLIC_BASE_URL`, `FLUXSWARM_CORS_ORIGINS`, `FLUXSWARM_TRUSTED_PROXIES`, `FLUXSWARM_CONTACT_EMAIL` | URL/سياسة |
| `REDIS_URL`/`FLUXSWARM_REDIS_URL` | حد متعدد العمال |
| `FLUXSWARM_HERMES_BIN`, `HERMES_HOME` | جسر Hermes |
| `FLUXSWARM_DEMO_DAILY_CAP`, `FLUXSWARM_DISPATCH_TIMEOUT_S`, `FLUXSWARM_KILL_SWITCH`, `FLUXSWARM_DB`, `FLUXSWARM_BYOK_STORE` | تشغيل/ضبط |

---

## 15. الاختبارات الحالية

`backend/tests/` — **75 اختباراً ناجحاً** وكذلك `py_compile` على main/payments. أبرزها: `test_paddle.py` (V1/V2 signature + replay window)، `test_paddle_flow_api.py` (checkout-page SDK v2، webhook، email/price fallback، idempotency/refund)، `test_db_refund`, `test_db_referral_race` (أقفال ضد السباق)، `test_ratelimit`, `test_audit`, `test_gate3_ux`, `test_fix_phase15`, `test_dispatch_completion`.

---

## 16. الحالة الحالية والخطوات التالية (معتمدة)

- ✅ دفع Sandbox حقيقي اكتمل؛ الويب‌هوك موقَّع يُقبل؛ `demo@fluxswarm.ai ← pro/120`.
- ✅ 75 اختباراً أخضر؛ كود نظيف بعد إزالة أدوات التشخيص المؤقتة؛ الخادم على pid 19628 قبل التنظيف الأخير.
- ⏳ **الترويج للإنتاج:** تبديل Paddle إلى Live (`PADDLE_API_BASE=https://api.paddle.com`, مفاتيح/أسعار Live)، عقدة Webhook بنفس التوقيع، فتح `FLUXSWARM_PAYMENTS=1`.
- ⏳ **النشر VPS:** `sudo bash deploy/bootstrap.sh --domain <domain> --email <email>` بعد إعداد `.env`؛ تحديد مزوّد/نموذج افتراضي للإنتاج.
- ⏳ `rotate_secrets.py`، وضع `.gitignore` (يشمل `backend/data/` و`.env`)، بوابة CI ثابت.

---

### للمراجعة في Kimi: نقاط يُستحسن سؤالها
1. هل `BEGIN IMMEDIATE` حول كل رصيد/أحداث إطلاق يكفي تحت خيوط uvicorn متعددة؟ (حالياً عملية واحدة؛ multi-worker يتطلب Redis + إعادة نظر في العزل).
2. هل نظام «المشرف القديم» (stale supervisor) في `run.ps1` مُغلَق بالكامل؟ (قتل بـ pid-الاستماع + إقصاء `$PID`).
3. هل يتسامح نمط Paddle scheme-2 (decode→encode) مع أي إعادة ترميز محتملة من البروكسي؟ (موثّق: لا؛ يجب تمرير البايتات كما هي).
4. هي العودة لترقية/تعبيئة «بدون تخفيض رصيد» (never claw back) — قرار منتج متعمد: راجع مخاطرة «تعبيئة بلا حدود عند تكرر الاشتراك» — مغلق لأن `upgrade_plan` يستخدم `max`.