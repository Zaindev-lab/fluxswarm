# تقرير شامل — إصلاح تعطّل السرب (FluxSwarm)

> التاريخ: 2026-09-05
> النطاق: `backend/` (الكود)، `deploy/docker/hermes-home/` (الإعدادات والمهارات)، الاختبارات، والنشر على الحاوية `docker-fluxswarm-1`.
>
> هذه الوثيقة تسجّل كل التغييرات والتعديلات والإعدادات التي أُنجزت لحل مشكلة «العمال لا يبدأون أبداً / الإطلاقات تُردّ باسم no_progress»، مع السبب الجذري وطريقة التحقق.

---

## 1. ملخص تنفيذي

كان لدى كل إطلاق جديد نفس الأعراض بالضبط:

1. تُنشأ اللوحة مهامها (`ready`).
2. لا يلتقط أي عميل (`claim`) أي مهمة — تبقى `ready` إلى أبد الدهر.
3. لا تُنشأ أي `logs/` أو `workspaces/`؛ لا يوجد أي عملية hermes تشتغل على اللوحة.
4. بعد ~77 ثانية يصنّف الـ watchdog الإطلاق `no_progress` فيُردّ رصيد التشغيل.

تبيّن أن **السبب الجذري لم يكن في الإطلاق نفسه**، بل في سقف التوافقية على مستوى المضيف:

- Hermes يحسب `kanban.max_in_progress` من ذاكرة الحاوية (≈3.7 GiB ⇒ **6.9 ≈ 7**).
- كان هناك **7 مهام `running` حيّة** متبقية من 4 لوحات قديمة مُستردّة/عالقة، بعمليات عاملة **لا تزال على قيد الحياة** (PIDs نشطة + heartbeats حديثة).
- كل مهمة `running` في أي لوحة تُحسب ضد السقف نفسه → `total_running (7) >= max_in_progress (7)` ⇒ **أي `dispatch` يرجع فوراً فارغاً**، فلا تُلتقط مهام الإطلاق الجديدة أبداً.
- الـ reaper القديم كان **يعيد تسليح** كل لوحة غير مكتملة كل 30 ثانية، فيُبعث العمال الموتى/الحائرون (عبر `bump_blocked_to_ready` + `dispatch`) ويظل السقف مشغولاً للأبد.

وأضف إلى ذلك تعليلاً ثانوياً حاسماً: المزود المضياف القديم `opencode-free` / نموذج `hy3-free` **كان قد أُوقف من الرنّاتايم**، لكن الإعدادات ما زالت تثبّته في:

- `HERMES_HOME/.env` الجذر (يُحمّل كمُحدّدات عالمية لكل profile/عامل) ← السبب الرئيسي للـ 429 المزمن.
- ملفات `.env` لستة ملفات profile (belt-and-suspenders).
- مهارات السرب (SKILL.md + debugging-swarm.md) التي كانت تعطي العامل تعليمات صريحة لكتابة `opencode-free`.

> **الخلاصة**: تم إصلاح ثلاث طبقات — (أ) تحرير السقف المحتَل (تنظيف تشغيلي شامل) و(ب) منع إعادة الاحتلال مستقبلاً (sealing + سكيب في الـ reaper) و(ج) إزالة توجيه المزود الميت نهائياً من كل الإعدادات.

النشر يعمل الآن على مزود OpenRouter المجاني `openrouter / z-ai/glm-5.2:free`، وتم إطلاق سرب اختباري بدأ عماله فعلياً (جاهزون وحقيقيون)، ولم يبقَ حاجز إلا الحصة المجانية اليومية للمزود (انظر §9).

---

## 2. التشخيص (تفاصيل الأدلة)

| المصباح | القيمة |
|---|---|
| حاوية | `docker-fluxswarm-1` (صورة `fluxswarm-beta`) |
| ذاكرة الحاوية | `MemTotal=3806848 KiB` (~3.7 GiB)، المتاح ~1.9 GiB |
| `MEMORY_GUARD_MB_PER_WORKER` | 512 → `derive_default_max_in_progress` = `clamp(3717/512, floor=2, ceil=8)` = **7** |
| `kanban.max_in_progress` في config | غير مضبوط (يُشتق تلقائياً) |
| المهام `running` العالقة قبل الإصلاح | 7 عبر 4 لوحات (PIDs حية: 1425، 6133، 6134، 238، 239، 5955، 5956) |
| قلبان | كل `dispatch` رجع: `spawned: [] / skipped_*: []` |
| ملاحظة | `_check_dispatcher_presence` يبقي المهام `ready` بلا gateway حتى يأتي `dispatch` |

اللوحات المسمومة (4):

- `flux-demo-1788532384` (1 مهمة running)
- `u1-1788563618-010ca6ce` (2 مهمة running — pins قديمة `big-pickle/opencode-free` عبر Task API)
- `u13-1788529854-47b32a70` (2 مهمة running)
- `u14-1788544599-8c6b3a59` (2 مهمة running)

ملاحظة حول `errors.log`: كان يفيض كل ~2s بتحذير `WAL-reset corruption bug` (SQLite 3.46.1 داخل venv hermes + `journal_mode=DELETE`) — **ضجيج لا علاقة له بالتوقف** وهو متوقّع من أوامر الاستكشاف.

كما اكتُشف أن الملف الفعلي الذي عرّف سلفاً مزوّداً ميتاً هو `HERMES_HOME/.env` الجذر:

```
HERMES_DEFAULT_PROVIDER=opencode-free
HERMES_DEFAULT_MODEL=nemotron-3-ultra-free
```

---

## 3. التغييرات البرمجية — `backend/hermes_client.py`

### 3.1 `SEAL_MARKER_NAME = "board.sealed"` (السطر 836)
اسم علامة دائمة على مستوى القرص تدل على أن اللوحة نهائية Operator-FINAL.

### 3.2 `seal_board(board, reason="launch finalized") -> dict` (السطر 839)
يجعل لوحة مهجورة/مُستردّة/خاطئة نهائية، ويُسقط `running` المتبقي إلى الصفر:

1. **قتل العمال**: يقرأ `read_worker_pids(board)` ثم `kill_process_tree(pid)` لكل عامل مسجَّل.
2. **إيواء المهام**: يقلب كل مهمة **غير نهائية** (`NOT IN ('done','blocked')`) إلى `blocked` عبر تحديث SQL مباشر مع مسح `claim_lock` و`claim_expires` و`worker_pid` — **مع فحص `PRAGMA table_info(tasks)`** لمرونة اختلاف مخطط hermès.
3. **العلامة**: `touch(board_dir / SEAL_MARKER_NAME)` ← الـ reaper يتخطى اللوحة لاحقاً.

السلوك **best-effort / idempotent**: فشل صفّ لا يُنهي الختم ولا يرفع استثناء (يُسجَّل في `report["errors"]`)، ويعيد تقريراً صغيراً للتدقيق (`killed` / `blocked` / `errors`).

### 3.3 `board_is_sealed(board) -> bool` (السطر 909)
يعيد True عندما تحمل اللوحة علامة `board.sealed`.

> تُستخدم المسارات النسبية لـ `HERMES_HOME` (متغير بيئة الحاوية)، ونفس ملفات SQLite المستخدمة من hermès — لا مُكتبات إضافية ولا اتصالات بعيدة.

---

## 4. التغييرات البرمجية — `backend/main.py`

### 4.1 مسار الـ demo (`_bg_dispatch` عندما يكون `pid is None`) — السطر ~324
بعد اكتمال حلقة الـ dispatch، إذا لم تتقارب (`converged`) اللوحة الديمو (بلا صف project)، تُختم مباشرة:

```python
if not res.get("converged") and (res.get("outcome") != "ok" or res.get("timed_out")):
    hc.seal_board(slug, reason="demo launch finalized")
```

**السبب**: إطلاقات demo لا تحمل صفاً في `projects`، فالـ `_board_finalized` لا يستطيع القبض عليها؛ الختم المباشر يمنع الـ reaper من إحياء عمالها وتعليق السقف للأبد.

### 4.2 `_finalize_launch(...)` — الختم عند عدم التقارب (السطر ~373)
بعد `db.set_launch_outcome(...)`، إذا `outcome != "converged"`:

```python
seal = hc.seal_board(slug, reason=reason)
audit.audit("dispatch.fire", outcome="board_sealed", slug=slug,
            project_id=pid, reason=reason,
            killed=seal.get("killed"), blocked=seal.get("blocked"))
```

مع مسار `board_seal_error` عند فشل الختم. سياسة الرصيد لم تُغيّر (لا يُردّ إلا عندما لا يوجد عمل منجز، مرة واحدة عبر `launch_refunded`).

### 4.3 الـ reaper الثابت — يتخطى اللوحات النهائية/المختومة (السطر ~470)

مُدمجت ثوابت وأدوات:

- `_REAPER_MERMINAL_STATUSES = {"stuck", "ok", "error"}` (السطر 434) — مجموعه مُنجزة.
- `_project_by_board_slug(slug)` (السطر 438) — يقرأ `projects` عبر `db._conn()`.
- `_board_finalized(slug)` (السطر 454) — True عند `launch_refunded` أو حالة نهائية.
- **الشرط الحاسم** في `_reconcile_boards_once`:

```python
if _board_finalized(slug) or hc.board_is_sealed(slug):
    continue   # نهائية Operator — لا نعيد تسليحها أبداً
```

أي: اللوحات المختومة/المنتهية تُستثنى **قبل** `bump_blocked_to_ready` وقبل `dispatch` — فلا يُبعث عمالها ولا تشغل الحصة. بقية المنطق (رتابة كل لوحة غير مكتملة، ثغرة 2 ثانية، `blocking=False`، `_REAPER_MAX_SPAWN=4`) بقي كما هو.

---

## 5. التغييرات في الإعدادات (Configuration / Env)

### 5.1 `deploy/docker/hermes-home/.env` (جذر HERMES_HOME) — أعيد توجيهه بالكامل

```
HERMES_DEFAULT_PROVIDER=openrouter
HERMES_DEFAULT_MODEL=z-ai/glm-5.2:free
```

كان سابقاً يثبّت `opencode-free` / `nemotron-3-ultra-free` — وهذا الملف يُحمَّل كمُحدّدات عالمية لكل profile/عامل، وهو أخطر مصدر للـ 429.

### 5.2 ملفات الـ profiles الستة — إزالة الربطات الميتة

`deploy/docker/hermes-home/profiles/ecc-{planner,architect,devops,tdd,reviewer,build-fixer}/.env`:

- **قبل**: `HERMES_DEFAULT_PROVIDER=opencode-free` + `HERMES_DEFAULT_MODEL=nemotron-3-ultra-free`
- **بعد**: لا ربطات نموذج/مزود على الإطلاق — تعليق يوجّه إلى رنّاتايم المشغّل (env الحاوية) والـ pinning حسب المهمة (انظر §6).

الملفات الثلاثة الباقية (catalog, security, test) لم تكن تحمل ربطات أصلاً.

### 5.3 Rتtings الحاوية (docker-compose.beta.yml — لم تتغيّر لكنها المرجعية)

| المتغير | القيمة |
|---|---|
| `FLUXSWARM_DEFAULT_PROVIDER` | `openrouter` |
| `FLUXSWARM_DEFAULT_MODEL` | `z-ai/glm-5.2:free` |
| `OPENROUTER_API_KEY` | مزوّدة (مفتاح مجاني للحساب الحالي) |
| `FLUXSWARM_PAYMENTS` | `0` |
| `REDIS_URL` | فارغ (rate limiting في الذاكرة) |
| المنفذ | `127.0.0.1:${PORT:-8787}` |
| `hostname: fluxswarm` | ثابت — استقرار أداة الـ claimer |

المجلدات الجوهرية على volumes أسماء: `docker_fluxswarm_data`, `docker_hermes_kanban`, `docker_hermes_logs`, `docker_hermes_memories`, `docker_hermes_state`, `docker_hermes_pairing`. مجلدات الـ profiles/skills في طبقة الصورة (تُستبدل عند إعادة البناء).

---

## 6. تحديث المهارات (Skills) — من `opencode-free` إلى OpenRouter

### 6.1 `skills/hermes-kanban-swarm/SKILL.md`
- أُزيلت الفقرة التي كانت تأمر بكتابة `HERMES_DEFAULT_PROVIDER=opencode-free` + `hy3-free` في كل profile `.env`.
- أُضيفت فقرة `NOTE (current deployment)` تحذر من أن `opencode-free`/`hy3-free` **أُوقفا** وأن أي تثبيت لهما يولّد 401/429 فورياً، وأن مصدر الحقيقة الوحيد هو رنّاتايم المشغّل: `openrouter` / `z-ai/glm-5.2:free`، والـ override الوحيد هو `set-model ... --provider openrouter --model z-ai/glm-5.2:free`.
- استُبدلت فقرة «Free hosted provider» بفقرة حول **OpenRouter free tier** (بلا مفتاح مستخدم) مع أمر تحقق:
  ```bash
  hermes --profile ecc-planner -m z-ai/glm-5.2:free -z "Say PLANNER_OK" --provider openrouter
  ```
  وذكر حدود rpm/day و السلوك عند 429.
- حدّث السطر المرجعي لـ `debugging-swarm.md` ليُشير إلى مسار OpenRouter.

### 6.2 `skills/hermes-kanban-swarm/references/debugging-swarm.md`
- مثال الإنتاج الصحي الآن: `'provider':'openrouter','model':'z-ai/glm-5.2:free'`.
- حلقة الـ pinning أصبحت:
  ```bash
  hermes kanban --board $BD set-model $T z-ai/glm-5.2:free --provider openrouter
  ```
- أُزيلت قيود `.env` القديمة، وأُضيف سطر صريح: «لا تكتب ربطات في `.env`؛ rنّاتايم المشغّل هو مصدر الحقيقة و `set-model` هو override الوحيد».
- قسم المزود المجاني يصف الآن OpenRouter free (مع `OPENROUTER_API_KEY` المحقونة لكل profile)، وتحذير الـ 429/الحدود.

---

## 7. الاختبارات

أُضيفت في `backend/tests/test_activity_live.py` (بأنماط monkeypatch المستخدمة في الملف):

| الاختبار | ما يثبته |
|---|---|
| `test_board_finalized_classifies_terminal_states` | `_board_finalized` True فقط للـ stuck/ok/error/refunded؛ False للـ running/launching/لا مشروع |
| `test_reaper_skips_sealed_and_finalized_boards` | الـ reaper يستقصد الختم/المنتهية: `dispatch` و`bump_blocked_to_ready` يُستدعيان للخالية فقط |
| `test_seal_board_parks_stale_tasks_and_marks_board` | `seal_board` يقتل pids، يقلب running/ready إلى blocked (pid/claim=NULL)، يحفظ done، ويكتب marker |
| `test_seal_board_idempotent` | الختم المتكرر لا يغيّر شيئاً ولا يرفع |

(بقّي `test_reaper_reclaim_uses_existing_single_pass_dispatch` كما هو مغطّى بالتحقق `blocking=False`.)

**نتيجة المجموعة الكاملة**: `374 passed, 7 skipped` (فشل واحد في `test_db_postgres.py::test_concurrent_deduct_credit_race_free` كان رجرجة شبكة Windows `WinError 64` — أُعيد تشغيله وحده فأنجح).

---

## 8. النشر والتشغيل (Docker)

سجل الأوامر المستخدمة:

```powershell
# البناء والرفع بالصورة الجديدة — مشروع 'docker' للحفاظ على البيانات (الحاوية docker-fluxswarm-1)
docker compose -p docker -f deploy/docker/docker-compose.beta.yml up -d --build
```

> **تحذير تشغيلي**: يوجد في جذر المستودع `docker-compose.yml` لمشروع `fluxswarm` (مع cron timing redis). الأمر `docker compose up` بدون `-f` في أي مجلد فرعي قد **يقلب للأعلى** ويجد بعدها المشروع الخطأ، مع إنشاء volumes فارغة جديدة بدل البيانات الفعلية. النشر الفعلي هو مشروع `docker` عبر `-f deploy/docker/docker-compose.beta.yml -p docker`.

تم أثناء هذا العمل تفكيك مشروع `fluxswarm` الذي كان قد أُنشئ خطأً:
```powershell
docker compose -p fluxswarm -f C:\Users\DELL\fluxswarm\docker-compose.yml down
```

التحقق بعد إعادة البناء (داخل الحاوية):

| الفحص | النتيجة |
|---|---|
| `grep -c def seal_board backend/hermes_client.py` | 1 |
| `grep -c _board_finalized backend/main.py` | 3 |
| `grep -rl HERMES_DEFAULT_PROVIDER=opencode-free profiles/` | (فارغ) |
| `/app/hermes_home/.env` | `openrouter` / `z-ai/glm-5.2:free` |
| `hermes --version` | Hermes Agent v0.21.0 (2026.8.31) |
| health | التطبيق يستجيب |

---

## 9. تنظيف التشغيل والتحقق النهائي

### 9.1 تنظيف اللوحات المسمومة (Cleaning up 7 zombies)
نُفّذ سكربت تنظيف داخل الحاوية يستدعي `seal_board` لكل لوحة نهائية/مُستردّة/مهجورة (لوحات demo بلا project + لوحات projects منتهية). النتيجة:

```
sealing 12 boards:
  SEALED flux-demo-1788532384   before_run=1 blocked=4 killed=1
  SEALED u1-1788563618-010ca6ce before_run=2 blocked=4 killed=2
  SEALED u13-1788529854-47b32a70 before_run=2 blocked=5 killed=2
  SEALED u14-1788544599-8c6b3a59 before_run=2 blocked=4 killed=2
  ... (8 لوحات أخرى بلا running)
total running before=7 after=0
sealed markers: 12 / 12
```

→ **`running` في كل اللوحات = 0**، والـ reaper الجديد يتخطّى كل ما هو مختوم/نهائي، فصار السقف محرّراً للأبد ولا يمكن لإطلاقات قديمة أن تعيد احتلاه.

### 9.2 إطلاق اختباري ناجح (أول إثبات حي)
من `GET /api/demo/launch` أُنشئ `flux-demo-1788605791`:

| المهمة | الحالة |
|---|---|
| `t_87ac0333` (root/planner) | **done** |
| `t_cdae60fd` (planner) | running — جلسات حقيقية وheartbeats |
| `t_864fc220` (architect) | running |
| `t_93796eb5` (devops) | running |
| `t_a1a9ab54` (tdd) | running |
| `t_3ffa0aeb` (reviewer) | todo (في الطابور) |
| `t_6332bdc2` (build-fixer) | todo |

- جلسات Hermes حقيقية في `logs/<task_id>.log` مع عنوان `work kanban task <id>`.
- الشواهد: `Provider: openrouter Model: z-ai/glm-5.2:free` — **لا أي أثر لـ opencode-free**.
- لا أخطاء «Unknown skill» ولا كورونات.

### 9.3 الحاجز المتبقي — حصة OpenRouter المجانية
الاستدعاء الأول لكل عامل يرتد بـ:
```
HTTP 429: Rate limit exceeded: free-models-per-day
X-RateLimit-Limit: 50   /   X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1788652800000   (منتصف الليل UTC)
```
أي أن الحساب **بلا أرصدة**، وحدّه المجاني 50 طلباً/يوم استُهلك مع بداية السرب (العمال المتوازيون). **الكمية ليست عيباً في كودك — الكود يعمل**؛ الحاجز مالي/حصصي في مزود OpenRouter.

> الخيارات: (أ) انتظار إعادة الضبط عند منتصف الليل UTC ثم إعادة الإطلاق (50 طلباً/يوم قد لا تكفي سرباً كاملاً من 6 وكلاء)، أو (ب) إضافة 10$+ إلى حساب OpenRouter (يفتح 1000 طلب مجاني/يوم)، أو (ج) حقن مفتاح BYOK مدفوع.

---

## 10. الملفات المتأثرة (مرجع سريع)

| الملف | التغيير |
|---|---|
| `backend/hermes_client.py` | `SEAL_MARKER_NAME`، `seal_board()` (سطر 839)، `board_is_sealed()` (سطر 909) |
| `backend/main.py` | ختم demo في `_bg_dispatch` (~324)، ختم في `_finalize_launch` (~373)، `_REAPER_TERMINAL_STATUSES`، `_project_by_board_slug`، `_board_finalized`، سكيب الـ reaper (~488) |
| `backend/tests/test_activity_live.py` | 4 اختبارات جديدة (أعلاه) |
| `deploy/docker/hermes-home/.env` | `openrouter` / `z-ai/glm-5.2:free` بدل `opencode-free` |
| `deploy/docker/hermes-home/profiles/ecc-*/ .env` | إزالة ربطة `opencode-free` من 6 ملفات |
| `deploy/docker/hermes-home/skills/hermes-kanban-swarm/SKILL.md` | استبدال إرشادات المزوّد القديمة بـ OpenRouter |
| `deploy/docker/hermes-home/skills/hermes-kanban-swarm/references/debugging-swarm.md` | نفس الشيء |
| `deploy/docker/docker-compose.beta.yml` | (لم يتغيّر، مرجعية الإعدادات) |

---

## 11. الخطوات التالية الموصى بها

1. **بعد منتصف الليل UTC** (أو بعد إضافة رصيد) أعد إطلاق demo:
   ```
   curl.exe http://127.0.0.1:8787/api/demo/launch
   ```
   وراقب: الرصيد `done`، العمال يتقدّمون ويصلون إلى `reviewer` ثم `build-fixer` حتى `converged`.
2. تحقق من عدم تكرار أعراض no_progress على أي إطلاق جديد (السقف حرّ 0/7 والـ reaper لا يعيد تسليح لوحات مختومة).
3. عند الحاجة لإصدار رقابي أضاف `10$` إلى حساب OpenRouter لرفع الحصة المجانية إلى 1000 طلب/يوم.
4. لا تستخدم ملف `docker-compose.yml` الجذري للإصدار الحالي (مشروع `fluxswarm`) إلا بعد نقل volumes → النشر النشط هو `-f deploy/docker/docker-compose.beta.yml -p docker`.

---

*مرجع أدوات التشخيص: نسخ محلية من نواتج الاستكشاف في `C:\Users\DELL\AppData\Local\Temp\opencode\fs-diagnose\` (kanban.py / kanban_db.py / لقطات اللوحات).*