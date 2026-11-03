import time
import html
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from db import db
from config import MASS_PER_WARN, REPORT_CHAT_ID
from utils import (reply, is_admin, mention, resolve_target, parse_duration,
                   human_duration, warn_weight, MUTE_PERMS, FULL_PERMS)

router = Router()


async def _prepare(msg: Message, bot: Bot):
    """Общая проверка: админ ли автор, есть ли цель, не админ ли цель."""
    if not await is_admin(bot, msg.chat.id, msg.from_user.id):
        await reply(msg, "not_admin")
        return None
    uid, name, args = await resolve_target(msg)
    if not uid:
        await reply(msg, "no_target")
        return None
    if await is_admin(bot, msg.chat.id, uid):
        await reply(msg, "target_admin")
        return None
    return uid, name, args


@router.message(Command("warn"))
async def cmd_warn(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot)):
        return
    uid, name, args = t
    reason = " ".join(args) or "без причины"
    row = await db.user(msg.chat.id, uid)
    mass = row["mass"] if row else 0
    s = await db.settings(msg.chat.id)
    total = await db.add_warn(msg.chat.id, uid, msg.from_user.id, reason, warn_weight(mass))
    await db.add_mass(msg.chat.id, uid, MASS_PER_WARN)
    user = mention(uid, name)
    await reply(msg, "warn", user=user, n=total, limit=s["warn_ban"], reason=html.escape(reason))
    try:
        if total >= s["warn_ban"]:
            await bot.ban_chat_member(msg.chat.id, uid)
            await db.clear_warns(msg.chat.id, uid)
            await reply(msg, "warn_autoban", user=user)
        elif total >= s["warn_mute"]:
            secs = int(s["automute_hours"] * 3600)
            await bot.restrict_chat_member(msg.chat.id, uid, MUTE_PERMS, until_date=int(time.time()) + secs)
            await reply(msg, "warn_automute", user=user, time=human_duration(secs))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("unwarn"))
async def cmd_unwarn(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot)):
        return
    uid, name, _ = t
    await db.clear_warns(msg.chat.id, uid)
    await msg.answer(f"преды {mention(uid, name)} сняты.")


@router.message(Command("warns"))
async def cmd_warns(msg: Message):
    uid, name, _ = await resolve_target(msg)
    if not uid:
        uid, name = msg.from_user.id, msg.from_user.first_name
    rows = await db.list_warns(msg.chat.id, uid)
    if not rows:
        return await msg.answer(f"у {mention(uid, name)} нет активных предов.")
    lines = [f"• {html.escape(r['reason'])} (вес {r['weight']}, {time.strftime('%d.%m', time.localtime(r['created_at']))})"
             for r in rows]
    total = await db.active_warns(msg.chat.id, uid)
    await msg.answer(f"преды {mention(uid, name)} — всего {total}:\n" + "\n".join(lines))


@router.message(Command("mute"))
async def cmd_mute(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot)):
        return
    uid, name, args = t
    secs = parse_duration(args[0] if args else None, default_hours=1)
    try:
        await bot.restrict_chat_member(msg.chat.id, uid, MUTE_PERMS, until_date=int(time.time()) + secs)
        await reply(msg, "mute", user=mention(uid, name), time=human_duration(secs))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("unmute"))
async def cmd_unmute(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot)):
        return
    uid, name, _ = t
    try:
        await bot.restrict_chat_member(msg.chat.id, uid, FULL_PERMS)
        await reply(msg, "unmute", user=mention(uid, name))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("ban"))
async def cmd_ban(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot)):
        return
    uid, name, args = t
    reason = " ".join(args) or "без причины"
    try:
        await bot.ban_chat_member(msg.chat.id, uid)
        await reply(msg, "ban", user=mention(uid, name), reason=html.escape(reason))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("unban"))
async def cmd_unban(msg: Message, bot: Bot):
    if not await is_admin(bot, msg.chat.id, msg.from_user.id):
        return await reply(msg, "not_admin")
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "no_target")
    try:
        await bot.unban_chat_member(msg.chat.id, uid, only_if_banned=True)
        await reply(msg, "unban", user=mention(uid, name))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("kick"))
async def cmd_kick(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot)):
        return
    uid, name, _ = t
    try:
        await bot.ban_chat_member(msg.chat.id, uid)
        await bot.unban_chat_member(msg.chat.id, uid)
        await reply(msg, "kick", user=mention(uid, name))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("report"))
async def cmd_report(msg: Message, bot: Bot):
    if not msg.reply_to_message:
        return await reply(msg, "no_target")
    if not REPORT_CHAT_ID:
        return await reply(msg, "report_no_chat")
    target = msg.reply_to_message.from_user
    link = ""
    if msg.chat.username:
        link = f"\nhttps://t.me/{msg.chat.username}/{msg.reply_to_message.message_id}"
    elif str(msg.chat.id).startswith("-100"):
        link = f"\nhttps://t.me/c/{str(msg.chat.id)[4:]}/{msg.reply_to_message.message_id}"
    text = (f"🚨 жалоба в «{html.escape(msg.chat.title or '')}»\n"
            f"от: {mention(msg.from_user.id, msg.from_user.first_name)}\n"
            f"на: {mention(target.id, target.first_name)} (id {target.id})\n"
            f"текст: {html.escape((msg.reply_to_message.text or msg.reply_to_message.caption or '[медиа]')[:300])}{link}")
    cid, uid = msg.chat.id, target.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔇 мут 1ч", callback_data=f"rep:m:{cid}:{uid}"),
        InlineKeyboardButton(text="⛔ бан", callback_data=f"rep:b:{cid}:{uid}"),
        InlineKeyboardButton(text="🙈 игнор", callback_data=f"rep:i:{cid}:{uid}"),
    ]])
    await bot.send_message(REPORT_CHAT_ID, text, reply_markup=kb)
    try:
        await msg.delete()
    except TelegramBadRequest:
        pass
    sent = await bot.send_message(msg.chat.id, (await _say(msg, "report_sent")))
    await db.remember_bot_message(msg.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("rep:"))
async def on_report_action(cb: CallbackQuery, bot: Bot):
    _, action, chat_id, uid = cb.data.split(":")
    chat_id, uid = int(chat_id), int(uid)
    if not await is_admin(bot, chat_id, cb.from_user.id):
        return await cb.answer("ты не админ того чата", show_alert=True)
    who = mention(cb.from_user.id, cb.from_user.first_name)
    try:
        if action == "m":
            await bot.restrict_chat_member(chat_id, uid, MUTE_PERMS, until_date=int(time.time()) + 3600)
            decision = f"🔇 мут на 1 ч — {who}"
        elif action == "b":
            await bot.ban_chat_member(chat_id, uid)
            decision = f"⛔ бан — {who}"
        else:
            decision = f"🙈 проигнорировано — {who}"
    except TelegramBadRequest as e:
        return await cb.answer(f"не получилось: {e.message}", show_alert=True)
    try:
        await cb.message.edit_text(cb.message.text + f"\n\n→ {decision}")
    except TelegramBadRequest:
        pass
    await cb.answer("готово")


async def _say(msg: Message, key: str) -> str:
    from phrases import say
    from utils import style
    return say(await style(msg.chat.id), key)
