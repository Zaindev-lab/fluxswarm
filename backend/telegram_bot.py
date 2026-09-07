"""
FluxSwarm Telegram bot.

Accepts a natural-language goal from any chat, launches the ECC devops squad
(kanban swarm) on an isolated board, and replies with live progress + final
summary. Users who link their account via ``/link <code>`` (code issued at
GET /api/telegram/link) get swarms scoped to their account: one credit spent per
launch, parallelism capped by their plan, and refunded on launch failure.
Reads TELEGRAM_BOT_TOKEN from the environment (same as Hermes).
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import audit
import db
import hermes_client as hc

# Token: reuse the one already present in Hermes .env (loaded automatically below).
_HERMES_HOME = os.environ.get("HERMES_HOME") or str(hc.HERMES_HOME)


def _load_hermes_env():
    p = Path(_HERMES_HOME) / ".env"
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
    chat = update.effective_chat.id
    user = db.get_user_by_telegram_chat(chat)
    if user:
        plan = db.PLANS.get(user["plan"], db.PLANS["demo"])
        await update.message.reply_text(
            f"⚡ Welcome {user['name']}!\n\n"
            f"Your account is linked to this bot ({plan['name']} plan, "
            f"{user['credits']} credits left).\n"
            "Send a build goal and the agent squad will launch on your account, "
            "costing 1 credit."
        )
        return
    await update.message.reply_text(
        "⚡ Welcome to FluxSwarm!\n\n"
        "Link your account first: log in on the website → Dashboard → "
        "'Link Telegram' → paste the code here via /link <code>.\n"
        "Without linking, the bot runs in a free trial mode."
    )


async def link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = " ".join(context.args or []).strip()
    chat = update.effective_chat.id
    if not code:
        await update.message.reply_text(
            "Send the code like this:\n/link 4F8A2C\n"
            "You will find it in the Dashboard under 'Link Telegram' (valid 10 minutes)."
        )
        return
    user = db.consume_telegram_link_code(code, chat)
    if not user:
        await update.message.reply_text("❌ The code is invalid, expired or already used. Request a new one from the Dashboard.")
        return
    audit.audit("telegram.link", uid=user["id"], telegram_chat_id=chat, outcome="ok")
    plan = db.PLANS.get(user["plan"], db.PLANS["demo"])
    await update.message.reply_text(
        f"✅ Chat linked to {user['name']} ({plan['name']} plan).\n"
        "Send a goal now and the squad will launch on your account."
    )


async def _run_for_user(update: Update, context: ContextTypes.DEFAULT_TYPE, chat: int, goal: str):
    """Launch a swarm owned by the chat's linked account: spends one credit,
    scopes the board to the user, and caps parallelism by their plan."""
    user = db.get_user_by_telegram_chat(chat)
    if not user:
        await update.message.reply_text(
            "Send a goal like that, or link your account first via /link <code>."
        )
        return
    plan = db.PLANS.get(user["plan"], db.PLANS["demo"])
    if not db.deduct_credit(user["id"]):
        await update.message.reply_text(
            f"❌ Not enough credits in your account ({db.get_user_credits(user['id'])} left).\n"
            "Subscribe or buy credits on the website to continue."
        )
        return
    slug = f"u{user['id']}-tg-{int(time.time())}"
    db.add_project(user["id"], slug, goal[:60], goal)
    audit.audit("telegram.goal", uid=user["id"], telegram_chat_id=chat, goal=goal[:500], outcome="ok")
    await update.message.reply_text(
        f"🚀 Goal received — the squad is launching on your account ({plan['name']} plan).\n"
        f"Credit balance after deduction: {db.get_user_credits(user['id'])}"
    )
    try:
        hc.ensure_board(slug)
        swarm = hc.launch_swarm(slug, goal)
        hc.dispatch(slug, max_spawn=min(plan["parallel"], 8))
    except Exception as e:
        db.add_credit(user["id"], 1)  # do not charge for a failed launch
        audit.audit("telegram.goal", uid=user["id"], telegram_chat_id=chat, goal=goal[:500], outcome="failed")
        await update.message.reply_text(f"❌ Launch failed (credit refunded): {e}")
        return
    await update.message.reply_text(
        f"✅ Squad running on board {slug}\n"
        f"• Workers: {len(swarm.worker_ids)}\n• Reviewer + synthesizer waiting on outputs.\n"
        "I will send an update when it completes."
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
            await context.bot.send_message(chat, f"🏁 Squad completed!\n\n{summary}")
            return
    await context.bot.send_message(chat, "⏳ The squad is still running in the background. Check the FluxSwarm dashboard.")


async def handle_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal = (update.message.text or "").strip()
    if not goal:
        return
    chat = update.effective_chat.id
    if db.get_user_by_telegram_chat(chat):
        await _run_for_user(update, context, chat, goal)
        return
    # Anonymous mode: fully free, ad-hoc board (no account, no credits).
    slug = f"tg-{chat}-{int(time.time())}"
    await update.message.reply_text("🚀 Goal received — launching the agent squad (trial mode)...")
    try:
        hc.ensure_board(slug)
        swarm = hc.launch_swarm(slug, goal)
        hc.dispatch(slug, max_spawn=8)
    except Exception as e:
        await update.message.reply_text(f"❌ Launch failed: {e}")
        return
    await update.message.reply_text(
        f"✅ Squad running on board {slug}\n"
        f"• Workers: {len(swarm.worker_ids)}\n• Reviewer + synthesizer waiting on outputs.\n"
        "I will send an update when it completes."
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
            await context.bot.send_message(chat, f"🏁 Squad completed!\n\n{summary}")
            return
    await context.bot.send_message(chat, "⏳ The squad is still running in the background. Check the FluxSwarm dashboard.")


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / BOT_TOKEN not set in environment")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("link", link))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_goal))
    print("FluxSwarm Telegram bot polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
