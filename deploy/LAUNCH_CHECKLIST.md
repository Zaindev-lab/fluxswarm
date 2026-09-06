# FluxSwarm Production Launch Checklist

> جاهزية الإطلاق — United States & United Kingdom (CCPA/CPRA + UK GDPR).
> Version: v3.0  ·  Target: نشر الإنتاج على VPS مع مراقبة

## ما قبل الإطلاق (48 ساعة قبل)

- [ ] PostgreSQL 15+ يعمل مع النسخ الاحتياطي (`FLUXSWARM_DATABASE_URL` يحقق `db_postgres.init_db()`)
- [ ] Redis يعمل (للـ rate limiting متعدد العمليات — `REDIS_URL`/`FLUXSWARM_REDIS_URL`)
- [ ] KMS backend مُضبوط (AWS/Azure/HashiCorp) — `FLUXSWARM_KMS_BACKEND` غير `file` في الإنتاج
- [ ] جميع مفاتيح مزودي AI مُختبرة (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GOOGLE_API_KEY` — envguard يرفض الإنتاج بدون واحد منها)
- [ ] مفاتيح Paddle Live مُضبوطة (`FLUXSWARM_PAYMENTS=1` + `PADDLE_API_KEY`/`PADDLE_WEBHOOK_SECRET`/`PADDLE_CLIENT_TOKEN` + أسعار `PADDLE_PRICE_*`)
- [ ] `ALERT_WEBHOOK_URL` مضبوط (Slack/Discord incoming webhook)
- [ ] شهادة SSL مُثبتة (Let's Encrypt عبر Caddy/nginx)
- [ ] DNS النطاق يشير إلى VPS
- [ ] `FLUXSWARM_TRUSTED_PROXIES` يضم عناوين الـ proxy الفعلية
- [ ] `FLUXSWARM_MEM_TOTAL_MB=8192` + `deploy.resources.limits.memory` متطابقان (سقف `MAX_IN_PROGRESS`)

## يوم الإطلاق

- [ ] `docker compose -p fluxswarm -f deploy/docker/docker-compose.beta.yml up -d --build`
- [ ] `docker logs -f fluxswarm-app-1` (راقب الأخطاء أول 60 ثانية)
- [ ] `curl -f https://<your-domain>/health` → 200 (يتضمن `db`, `db_pool_*`, `provider_pool`, حدود demo)
- [ ] `curl -f https://<your-domain>/api/account/admt-notice` → 200 + JSON
- [ ] تسجيل مستخدم اختباري → نجاح (JWT + جلسة)
- [ ] إطلاق demo → نجاح، العمال يتقدمون (`/api/demo/launch` ثم `/api/demo/progress/<slug>`)
- [ ] اختبار Paddle checkout → نجاح (sandbox أولاً، ثم live)
- [ ] اختبار توقيع webhook (`/api/payments/webhook` بقيمة معروفة) → نجاح
- [ ] اختبار ADMT opt-out → 403 على `/api/projects/<slug>/dispatch`
- [ ] اختبار طلب مراجعة بشرية → يظهر في `/api/admin/human-review-queue`
- [ ] اختبار `/demo` (صفحة الهبوط) يقبل وصفاً ويظهر شريط التقدم

## ما بعد الإطلاق (الأسبوع الأول)

- [ ] راقب `audit.jsonl` للشواذ (الأحداث `reaper.error`, `demo.launch`, `auth.*`)
- [ ] تحقق من حصة المزود يومياً (عبر `/api/provider/health` أو لوحة المزود)
- [ ] راجع حدود demo (يجب ألا تتجاوز 20/يوم، و1/ساعة لكل IP)
- [ ] تحقق من اللوحات المختومة — `active_boards`/`sealed_boards` في `/health` (لا تتراكم)
- [ ] نسخ PostgreSQL احتياطي يومي
- [ ] رصد معدل الأخطاء (الهدف < 0.1%)
- [ ] تحقق من دوران `audit.jsonl` (100MB/10 ملفات/gzip بعد 7 أيام)

## خطة التراجع

في حال فشل حرج:
1. `docker compose -p fluxswarm down`
2. استعد PostgreSQL من النسخ الاحتياطي
3. ارجع إلى صورة Docker السابقة (`fluxswarm-beta:<previous-tag>`)
4. أبلغ المستخدمين عبر صفحة الحالة

## قيود جلسة التحقق (Session 3)

- [x] `pytest tests/ -q` → 400+ ناجح، 0 فاشل، 7 متخطى (اختبارات POSIX-only على Windows)
- [x] `docker build` لصورة الـ runner + صورة التطبيق (`fluxswarm-beta`) تنجح
- [x] `curl /health` → 200 (تكامل عبر compose على منفذ تجريبي)
- [x] فحص أمان `grep -r "sk-" backend --include="*.py"` (بدون test_/example) → 0 نتائج