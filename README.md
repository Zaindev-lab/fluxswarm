# ⚡ FluxSwarm

منصة SaaS حقيقية لبناء المشاريع بالوكلاء (agentic SaaS)، مدعومة بسرب **ECC devops**
داخل Hermes. كل مستخدم يملك مساحة معزولة، وكل مشروع = مجلس كانبان يُطلق عليه سرب
من 6 وكلاء: `ecc-planner` · `ecc-architect` · `ecc-devops` · `ecc-tdd` (متوازي)
← `ecc-reviewer` (تحقق) ← `ecc-build-fixer` (تجميع).

## الميزات المضافة
- 🔐 **مصادقة المستخدمين** — تسجيل/دخول عبر JWT (سر من env/ملف، لا ثابت في الكود)،
  كلمات مرور **Argon2id** (ترقية تلقائية للحسابات القديمة sha256 عند الدخول)،
  **حدود لمحاولات الدخول** (5 فشل لكل ip+بريد ثم 429 + خنق عام لكل IP)،
  كل مستخدم معزول على مجالس كانبان خاصة به (`u<id>-*`).
- 💳 **الباقات والأسعار** — Demo (مجاني) / Starter $29 / Pro $99 / Scale $299،
  كل باقة برصيد وتنفيذ متوازي مختلف (نقطة `/api/plans`).
- 🔗 **برنامج إحالات قوي** — كل مستخدم له رمز `FLX-XXXXXXXX` ورابط `?ref=`؛
  عند اشتراك صديق بباقة مدفوعة يكسب المُحيل 25 رصيداً.
- ▶️ **وضع Demo** — زر تجربة فورية تطلق سرباً جاهزاً (`/api/demo/launch`).
- 🤖 **بوت تيليغرام** — يستقبل الهدف عبر الدردشة، يطلق السرب، ويردّ بالتقدّم والنتائج
  (يتصل بـ `aiforsaashermes_bot` الجاهز).
- 🔑 **BYOK (Bring Your Own Key)** — في الباقات المدفوعة يجلب المستخدم مفتاح مزوّده
  (Anthropic/OpenAI/Gemini/Kimi) فيدفع هو لتوكناته؛ FluxSwarm تتقاضى رسوم التنسيق فقط.
  المفاتيح **مشفّرة** (Fernet) ولا تُعرض أبداً بشكل واضح. هذا يجعل الخسارة على التوكنات
  **مستحيلة** — وهي الصيغة الآمنة اقتصادياً لإطلاق SaaS حقيقي.

## التشغيل المحلي
```bash
cd backend
pip install -r requirements.txt
export HERMES_HOME="C:/Users/DELL/AppData/Local/hermes"
bash run.sh          # يشغّل المنصة + البوت معاً
# أو منفصلين:
python -m uvicorn main:app --host 127.0.0.1 --port 8787
HERMES_HOME=... python telegram_bot.py
```
افتح http://127.0.0.1:8787 — جرّب 「تجربة Demo」 مباشرة، أو سجّل حساباً.

## النشر (Docker)
```bash
docker compose up --build
```
يركّب مجلد Hermes لتشغيل السرب، والمنصة على :8787.

## نقاط النهاية
| الطريقة | المسار | الوصف |
|--------|-------|-------|
| GET | `/api/plans` | قائمة الباقات |
| GET | `/api/squad` | تشكيلة السرب |
| GET | `/api/demo/launch` | إطلاق سرب تجريبي |
| POST | `/api/auth/register` | تسجيل |
| POST | `/api/auth/login` | دخول (يرجع JWT) |
| GET | `/api/me` | بيانات المستخدم (محمي) |
| GET/POST | `/api/projects` | قائمة/إنشاء مشروع (محمي، يستهلك رصيداً) |
| GET | `/api/projects/{slug}/tasks` | بطاقات المشروع (محمي) |
| GET | `/api/referrals` | رمز الإحالة ورابطه (محمي) |
| POST | `/api/subscribe/{plan}` | ترقية الباقة (محمي) |
| WS | `/ws/{slug}?token=...` | بث حيّ للبطاقات |
