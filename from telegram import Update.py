import os
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from dotenv import load_dotenv
from telegram import Update, ChatInviteLink, ChatMember
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackContext,
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
            # prizes removed
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
        lines.append("Hali ball yo‘q. Birinchilardan bo‘ling! Shaxsiy havola: /mylink")
    else:
        lines.append("Yetakchilar ro‘yxati:")
        for i, (uid_str, score) in enumerate(ranking[:20], start=1):
            uid = int(uid_str)
            lines.append(f"{i}. {format_user_mention(uid)} — {score}")
    lines.append("")
    lines.append("Ball to‘plash usullari:")
    lines.append("• Odam qo‘shish (bevosita)")
    lines.append("• Shaxsiy taklif havolasi orqali: /mylink")
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
async def delete_message_job(context: CallbackContext):
    try:
        await context.bot.delete_message(
            chat_id=context.job.chat_id, message_id=context.job.data["message_id"]
        )
    except Exception:
        pass
def schedule_delete(context: CallbackContext, chat_id: int, message_id: int, seconds: int = 180):
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
    seconds: int = 180,
):
    msg = await update.effective_message.reply_text(
        text, parse_mode=parse_mode, disable_web_page_preview=True
    )
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
    # Announce top 3 after freezing
    scores = cs["scores"]
    ranking = sorted(scores.items(), key=lambda kv: (-kv[1], int(kv[0])))
    mentions = []
    for i, (uid_str, score) in enumerate(ranking[:3], start=1):
        uid = int(uid_str)
        mentions.append(f"{i}-o‘rin: {format_user_mention(uid)} ({score} ball)")
    if mentions:
        text = "<b>Tanlov yakunlandi!</b>\n\n"
        text += "\n".join(mentions)
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
async def konkurs_end_job(context: CallbackContext):
    chat_id = context.job.chat_id
    await end_contest(chat_id, context)
async def konkurs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await auto_clean_reply(update, context, "<i>Tanlovni faqat adminlar boshlashi mumkin.</i>")
        return
    chat = update.effective_chat
    cs = get_chat_state(chat.id)
    days = 7
    if context.args:
        try:
            days = max(1, min(30, int(context.args[0])))
        except ValueError:
            pass
    cs["active"] = True
    cs["scores"] = {}
    cs["links"] = {}
    cs["end_ts"] = int((now_utc() + timedelta(days=days)).timestamp())
    save_state(STATE)
    await update_pinned_leaderboard(chat.id, context)
    await auto_clean_reply(
        update,
        context,
        f"Tanlov {days} kun davom etadi! Shaxsiy havola uchun /mylink.\n\n<i>Ushbu xabar 3 daqiqadan so‘ng o‘chiriladi.</i>",
    )
    seconds_left = cs["end_ts"] - int(now_utc().timestamp())
    if seconds_left > 0:
        context.job_queue.run_once(
            konkurs_end_job,
            when=seconds_left,
            chat_id=chat.id,
            name=f"end_{chat.id}",
        )
async def konkurs_stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await auto_clean_reply(update, context, "<i>Tanlovni faqat adminlar to‘xtatishi mumkin.</i>")
        return
    chat = update.effective_chat
    await end_contest(chat.id, context)
    await auto_clean_reply(update, context, "Tanlov to‘xtatildi. Yakuniy yetakchilar ro‘yxati pin qilindi.\n\n<i>Ushbu xabar 3 daqiqadan so‘ng o‘chiriladi.</i>")
async def konkurs_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    text = await render_leaderboard_text(chat.id)
    await auto_clean_reply(
        update,
        context,
        text + "\n\n<i>Ushbu xabar 3 daqiqadan so‘ng o‘chiriladi.</i>",
    )
def parse_prizes_from_args(args) -> Dict[str, str]:
    raw = " ".join(args)
    parts = []
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
    else:
        parts = [p.strip() for p in raw.split()]
    prizes = {}
    for p in parts:
        if ":" in p:
            rank, name = p.split(":", 1)
            rank = rank.strip()
            name = name.strip()
            if rank:
                prizes[rank] = name
    return prizes
async def konkurs_prizes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This command is now disabled
    await auto_clean_reply(update, context, "<i>Sovrinlar funksiyasi o‘chirib qo‘yilgan.</i>")
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
        await update_pinned_leaderboard(chat.id, context)
    try:
        await msg.delete()
    except Exception:
        pass
async def remove_all_system_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    # Delete any system message
    try:
        await msg.delete()
    except Exception:
        pass
async def dev_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Tanlov ballari bot</b>\n"
        "Bu bot guruhda tanlovlarni boshqarish, ball to‘plash va yetakchilar ro‘yxatini yuritish uchun mo‘ljallangan.\n"
        "Adminlar tanlovni boshlashi, sovrinlarni o‘rnatishi va yakuniy natijalarni ko‘rishi mumkin.\n"
        "\n"
        "Bot dasturchisi: <b>@xoqon2003</b>"
    )
    await auto_clean_reply(update, context, text)
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set in environment")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("konkurs", konkurs_cmd))
    app.add_handler(CommandHandler("konkurs_stop", konkurs_stop_cmd))
    app.add_handler(CommandHandler("konkurs_status", konkurs_status_cmd))
    app.add_handler(CommandHandler("konkurs_prizes", konkurs_prizes_cmd))  # still present, but disabled
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    # Remove all system messages with a single handler
    sys_cleanup_filter = (
        filters.StatusUpdate.LEFT_CHAT_MEMBER
        | filters.StatusUpdate.PINNED_MESSAGE
        | filters.StatusUpdate.NEW_CHAT_TITLE
        | filters.StatusUpdate.NEW_CHAT_PHOTO
        | filters.StatusUpdate.DELETE_CHAT_PHOTO
        | filters.StatusUpdate.NEW_CHAT_DESCRIPTION
    )
    app.add_handler(MessageHandler(sys_cleanup_filter, remove_all_system_messages))
    app.add_handler(CommandHandler("dev", dev_cmd))
    app.run_polling()
if __name__ == "__main__":
    main()