import os
import json
from datetime import datetime, timedelta, timezone
from typing import Dict
from dotenv import load_dotenv
from telegram import Update, ChatMember
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
STATE_FILE = "contest_state.json"
def now_utc() -> datetime:
    return datetime.now(timezone.utc)
def load_state() -> Dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
def save_state(state: Dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
STATE = load_state()
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return False
    member: ChatMember = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in ("administrator", "creator")
def get_chat_state(chat_id: int) -> Dict:
    key = str(chat_id)
    if key not in STATE:
        STATE[key] = {
            "active": False,
            "end_ts": 0,
            "scores": {},
            "pinned_message_id": None,
            "links": {},
        }
    return STATE[key]
def time_left_str(end_ts: int) -> str:
    delta = max(0, end_ts - int(now_utc().timestamp()))
    td = timedelta(seconds=delta)
    days = td.days
    hours = td.seconds // 3600
    minutes = (td.seconds % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days} kun")
    if hours:
        parts.append(f"{hours} soat")
    if minutes or not parts:
        parts.append(f"{minutes} daqiqa")
    return " ".join(parts)
def format_user_mention(user_id: int) -> str:
    return f'<a href="tg://user?id={user_id}">user_{user_id}</a>'
async def render_leaderboard_text(chat_id: int) -> str:
    cs = get_chat_state(chat_id)
    scores = cs["scores"]
    end_ts = cs["end_ts"]
    active = cs["active"]
    ranking = sorted(scores.items(), key=lambda kv: (-kv[1], int(kv[0])))
    lines = []
    if active:
        lines.append("🏆 Tanlov boshlandi!")
        lines.append(f"⏳ Qolgan vaqt: {time_left_str(end_ts)}")
    else:
        lines.append("🏁 Tanlov tugadi")
    lines.append("")
    if not ranking:
        lines.append("Hali ball yo‘q. Birinchilardan bo‘ling!")
    else:
        lines.append("Yetakchilar ro‘yxati:")
        for i, (uid_str, score) in enumerate(ranking[:20], start=1):
            uid = int(uid_str)
            lines.append(f"{i}. {format_user_mention(uid)} — {score}")
    lines.append("")
    return "\n".join(lines)
async def update_pinned_leaderboard(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    cs = get_chat_state(chat_id)
    text = await render_leaderboard_text(chat_id)
    if cs.get("pinned_message_id"):
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=cs["pinned_message_id"],
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
        except Exception:
            cs["pinned_message_id"] = None
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    cs["pinned_message_id"] = msg.message_id
    save_state(STATE)
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass
async def ensure_pinned_leaderboard(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    cs = get_chat_state(chat_id)
    text = await render_leaderboard_text(chat_id)
    if cs.get("pinned_message_id"):
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=cs["pinned_message_id"],
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
        except Exception:
            cs["pinned_message_id"] = None
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    cs["pinned_message_id"] = msg.message_id
    save_state(STATE)
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass
async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    job = getattr(context, "job", None)
    if not job:
        return
    try:
        await context.bot.delete_message(
            chat_id=job.chat_id, message_id=job.data["message_id"]
        )
    except Exception:
        pass
def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, seconds: int = 60):
    context.job_queue.run_once(
        delete_message_job,
        when=seconds,
        chat_id=chat_id,
        data={"message_id": message_id},
    )
async def auto_clean_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    parse_mode: str = ParseMode.HTML,
    seconds: int = 60,
    skip_delete: bool = False,
):
    msg = await update.effective_message.reply_text(
        text, parse_mode=parse_mode, disable_web_page_preview=True
    )
    if not skip_delete:
        schedule_delete(context, msg.chat.id, msg.message_id, seconds)
        try:
            schedule_delete(context, update.effective_message.chat.id, update.effective_message.message_id, seconds)
        except Exception:
            pass
    return msg
async def end_contest(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    cs = get_chat_state(chat_id)
    cs["active"] = False
    for link_url, meta in list(cs["links"].items()):
        if not meta.get("revoked"):
            try:
                await context.bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=link_url)
            except Exception:
                pass
            meta["revoked"] = True
    save_state(STATE)
    await update_pinned_leaderboard(chat_id, context)
    # Cancel periodic update job
    if hasattr(context, "job_queue"):
        jobs = context.job_queue.get_jobs_by_name(f"periodic_{chat_id}")
        for job in jobs:
            job.schedule_removal()
    scores = cs["scores"]
    ranking = sorted(scores.items(), key=lambda kv: (-kv[1], int(kv[0])))
    mentions = []
    for i, (uid_str, score) in enumerate(ranking[:3], start=1):
        uid = int(uid_str)
        mentions.append(f"{i}-o‘rin: {format_user_mention(uid)} ({score} ball)")
    text = "<b>Tanlov yakunlandi!</b>\n\n"
    if mentions:
        text += "\n".join(mentions)
    else:
        text += "Hech kim ishtirok etmadi."
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
async def periodic_leaderboard_update(context: ContextTypes.DEFAULT_TYPE):
    job = getattr(context, "job", None)
    if not job:
        return
    chat_id = job.chat_id
    cs = get_chat_state(chat_id)
    if cs.get("active"):
        await ensure_pinned_leaderboard(chat_id, context)
async def konkurs_end_job(context: ContextTypes.DEFAULT_TYPE):
    job = getattr(context, "job", None)
    if not job:
        return
    chat_id = job.chat_id
    await end_contest(chat_id, context)
async def konkurs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    cs = get_chat_state(chat.id)
    days = 7
    cs["active"] = True
    cs["scores"] = {}
    cs["links"] = {}
    cs["end_ts"] = int((now_utc() + timedelta(days=days)).timestamp())
    save_state(STATE)
    await ensure_pinned_leaderboard(chat.id, context)  # This will send and pin the leaderboard table
    await auto_clean_reply(
        update,
        context,
        f"Tanlov {days} kun davom etadi!\n\n<i>Ushbu xabar 1 daqiqadan so‘ng o‘chiriladi.</i>",
        skip_delete=False,
    )
    seconds_left = cs["end_ts"] - int(now_utc().timestamp())
    if seconds_left > 0:
        context.job_queue.run_once(
            konkurs_end_job,
            when=seconds_left,
            chat_id=chat.id,
            name=f"end_{chat.id}",
        )
        # Start periodic update every minute
        context.job_queue.run_repeating(
            periodic_leaderboard_update,
            interval=60,
            first=0,
            chat_id=chat.id,
            name=f"periodic_{chat.id}",
        )
async def konkurs_stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cs = get_chat_state(update.effective_chat.id)
    if not cs.get("active"):
        msg = await auto_clean_reply(
            update,
            context,
            "<i>Hozircha to‘xtatiladigan tanlov yo‘q.</i>",
            skip_delete=False,
        )
        try:
            schedule_delete(context, update.effective_message.chat.id, update.effective_message.message_id, 60)
        except Exception:
            pass
        return
    if not await is_admin(update, context):
        msg = await auto_clean_reply(update, context, "<i>Tanlovni faqat adminlar to‘xtatishi mumkin.</i>", skip_delete=False)
        try:
            schedule_delete(context, update.effective_message.chat.id, update.effective_message.message_id, 60)
        except Exception:
            pass
        return
    chat = update.effective_chat
    await end_contest(chat.id, context)
    # Auto-delete this notification message and the admin's command after 1 min
    msg = await auto_clean_reply(
        update,
        context,
        "Tanlov to‘xtatildi. Yakuniy yetakchilar ro‘yxati pin qilindi.\n\n<i>Ushbu xabar 1 daqiqadan so‘ng o‘chiriladi.</i>",
        skip_delete=False,
    )
    try:
        schedule_delete(context, update.effective_message.chat.id, update.effective_message.message_id, 60)
    except Exception:
        pass

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Assalomu alaykum! Bu bot guruhda konkurslarni boshqaradi. /konkurs buyrug'ini ishlatib boshlang.",
        parse_mode=ParseMode.HTML,
    )


async def konkurs_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    cs = get_chat_state(chat.id)
    if not cs.get("active"):
        msg = await auto_clean_reply(
            update,
            context,
            "<i>Hozircha tanlov yo‘q. Yangi tanlov boshlanishini kuting.</i>",
            skip_delete=False,  # Enable auto-delete
        )
        # Also schedule deletion of the admin's command message (if not already handled)
        try:
            schedule_delete(context, update.effective_message.chat.id, update.effective_message.message_id, 60)
        except Exception:
            pass
        return
    text = await render_leaderboard_text(chat.id)
    await auto_clean_reply(
        update,
        context,
        text + "\n\n<i>Ushbu xabar 1 daqiqadan so‘ng o‘chiriladi.</i>",
        skip_delete=True,  # Do not auto-delete leaderboard/status replies
    )
def credit_invite(chat_id: int, inviter_id: int, count: int = 1):
    cs = get_chat_state(chat_id)
    if not cs["active"]:
        return
    scores = cs["scores"]
    scores[str(inviter_id)] = scores.get(str(inviter_id), 0) + count
    save_state(STATE)
async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    cs = get_chat_state(chat.id)
    if not msg or not msg.new_chat_members:
        return
    credited = False
    for member in msg.new_chat_members:
        if msg.from_user and msg.from_user.id != member.id:
            inviter_id = msg.from_user.id
            credit_invite(chat.id, inviter_id, 1)
            credited = True
        else:
            link_url = None
            try:
                if getattr(msg, "invite_link", None):
                    link_url = msg.invite_link.invite_link
            except Exception:
                link_url = None
            if link_url and link_url in cs["links"]:
                inviter_id = cs["links"][link_url]["creator_id"]
                credit_invite(chat.id, inviter_id, 1)
                credited = True
    if cs["active"] and credited:
        await ensure_pinned_leaderboard(chat.id, context)  # Update the pinned leaderboard in real time
    try:
        await msg.delete()
    except Exception:
        pass


async def cleanup_system_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    should_delete = False
    if msg.left_chat_member:
        should_delete = True
    if msg.pinned_message:
        should_delete = True
    if msg.new_chat_title:
        should_delete = True
    if msg.new_chat_photo or msg.delete_chat_photo:
        should_delete = True

    if should_delete:
        try:
            await msg.delete()
        except Exception:
            pass

async def dev_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Just remove</b>\n"
        "Bu bot guruhda konkurs o`tkazishi,hamda shu bilan birgalikda barcha tizim habarlarini\n Masalan Guruhga qo`shildi, Guruhni tark etdi, Habar qadaldi va hokazolardan ham tozalab turadi.\n"
        "Adminlar konkursni boshlashi mumkin.\n"
        "\n"
        "Bot dasturchisi: <b>@xoqon2003</b>"
    )
    await auto_clean_reply(update, context, text)

    
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set in environment")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("konkurs", konkurs_cmd))
    app.add_handler(CommandHandler("konkurs_stop", konkurs_stop_cmd))
    app.add_handler(CommandHandler("konkurs_status", konkurs_status_cmd))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    sys_cleanup_filter = (
        filters.StatusUpdate.LEFT_CHAT_MEMBER
        | filters.StatusUpdate.PINNED_MESSAGE
        | filters.StatusUpdate.NEW_CHAT_TITLE
        | filters.StatusUpdate.NEW_CHAT_PHOTO
        | filters.StatusUpdate.DELETE_CHAT_PHOTO
    )
    app.add_handler(MessageHandler(sys_cleanup_filter, cleanup_system_messages))
    # Allow /dev for everyone (no admin check)
    app.add_handler(CommandHandler("dev", dev_cmd, block=False))
    app.run_polling()
if __name__ == "__main__":
    main()