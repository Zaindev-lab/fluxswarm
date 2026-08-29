"""
FluxSwarm Telegram bot.

Accepts a natural-language goal from any chat, launches the ECC devops squad
(kanban swarm) on an isolated board, and replies with live progress + final
summary. Reads TELEGRAM_BOT_TOKEN from the environment (same as Hermes).
"""
from __future__ import annotations

import asyncio
import os
import time

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import db
import hermes_client as hc

# Token: reuse the one already present in Hermes .env (loaded automatically below).
def _load_hermes_env():
    p = os.path.join("C:/Users/DELL/AppData/Local/hermes", ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)

_load_hermes_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ أهلاً بك في FluxSwarm!\n\n"
        "أرسل هدف بناء وسأطلق سرب الوكلاء (planner/architect/devops/tdd/reviewer/build-fixer).\n"
        "مثال:\nابنِ تطبيق FastAPI لإدارة المهام مع اختبارات و CI/CD"
    )


async def handle_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal = (update.message.text or "").strip()
    if not goal:
        return
    chat = update.effective_chat.id
    slug = f"tg-{chat}-{int(time.time())}"
    await update.message.reply_text("🚀 تم استلام الهدف — جارٍ إطلاق سرب الوكلاء...")
    try:
        hc.ensure_board(slug)
        swarm = hc.launch_swarm(slug, goal)
        hc.dispatch(slug, max_spawn=8)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ عند الإطلاق: {e}")
        return
    await update.message.reply_text(
        f"✅ السرب يعمل على المجلس {slug}\n"
        f"• عمال: {len(swarm.worker_ids)}\n• مُراجع + مُركّب بانتظار المخرجات.\n"
        "سأرسل تحديثاً عند الاكتمال."
    )
    # Poll the board and notify on completion.
    for _ in range(60):  # up to ~10 min
        await asyncio.sleep(10)
        try:
            tasks = hc.list_tasks(slug)
        except Exception:
            continue
        done = sum(1 for t in tasks if t.get("state") == "done")
        if done >= len(tasks):
            summary = "\n".join(f"• {t['assignee']}: {t['state']}" for t in tasks)
            await context.bot.send_message(chat, f"🏁 اكتمل السرب!\n\n{summary}")
            return
    await context.bot.send_message(chat, "⏳ لا يزال السرب يعمل في الخلفية. تحقّق من لوحة FluxSwarm.")


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / BOT_TOKEN not set in environment")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_goal))
    print("FluxSwarm Telegram bot polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
