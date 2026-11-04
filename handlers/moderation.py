import time
import html
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from db import db
from config import MASS_PER_WARN, REPORT_CHAT_ID, OWNER_IDS
from utils import (reply, deny, rank_of, rank_name, has_rank, mention, resolve_target, parse_duration,
                   human_duration, warn_weight, MUTE_PERMS, FULL_PERMS, MOD_MUTE_MAX, RANK_ALIASES, split_args)

router = Router()


async def _prepare(msg: Message, bot: Bot, action: str):
    """Общая проверка: хватает ли ранга, есть ли цель, не выше ли цель по рангу."""
    if await deny(msg, bot, action):
        return None
    uid, name, args = await resolve_target(msg)
    if not uid:
        await reply(msg, "no_target")
        return None
    me = await rank_of(bot, msg.chat.id, msg.from_user.id)
    target = await rank_of(bot, msg.chat.id, uid)
    if target >= me:
        await reply(msg, "target_higher", user=mention(uid, name))
        return None
    return uid, name, args


@router.message(Command("warn", "варн", "пред", prefix="/!"))
async def cmd_warn(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot, "warn")):
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
            await db.record_ban(msg.chat.id, uid, 0, "лимит предов")
            await db.clear_warns(msg.chat.id, uid)
            await reply(msg, "warn_autoban", user=user)
        elif total >= s["warn_mute"]:
            secs = int(s["automute_hours"] * 3600)
            await bot.restrict_chat_member(msg.chat.id, uid, MUTE_PERMS, until_date=int(time.time()) + secs)
            await db.record_mute(msg.chat.id, uid, int(time.time()) + secs, 0, "лимит предов")
            await reply(msg, "warn_automute", user=user, time=human_duration(secs))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("unwarn", "анварн", "снятьпред", "минуспред", prefix="/!"))
async def cmd_unwarn(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot, "unwarn")):
        return
    uid, name, _ = t
    await db.clear_warns(msg.chat.id, uid)
    await msg.answer(f"преды {mention(uid, name)} сняты.")


@router.message(Command("warns", "преды", "варны", prefix="/!"))
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


@router.message(Command("mute", "мут", "заткнись", prefix="/!"))
async def cmd_mute(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot, "mute")):
        return
    uid, name, args = t
    secs = parse_duration(args[0] if args else None, default_hours=1)
    if secs > MOD_MUTE_MAX and await rank_of(bot, msg.chat.id, msg.from_user.id) < 2:
        return await reply(msg, "mute_too_long", max=human_duration(MOD_MUTE_MAX))
    try:
        await bot.restrict_chat_member(msg.chat.id, uid, MUTE_PERMS, until_date=int(time.time()) + secs)
        await db.record_mute(msg.chat.id, uid, int(time.time()) + secs, msg.from_user.id, " ".join(args[1:])[:100])
        await reply(msg, "mute", user=mention(uid, name), time=human_duration(secs))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("unmute", "размут", "анмут", prefix="/!"))
async def cmd_unmute(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot, "unmute")):
        return
    uid, name, _ = t
    try:
        await bot.restrict_chat_member(msg.chat.id, uid, FULL_PERMS)
        await db.remove_mute(msg.chat.id, uid)
        await reply(msg, "unmute", user=mention(uid, name))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("ban", "бан", prefix="/!"))
async def cmd_ban(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot, "ban")):
        return
    uid, name, args = t
    reason = " ".join(args) or "без причины"
    try:
        await bot.ban_chat_member(msg.chat.id, uid)
        await db.record_ban(msg.chat.id, uid, msg.from_user.id, reason)
        await reply(msg, "ban", user=mention(uid, name), reason=html.escape(reason))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("unban", "разбан", "анбан", prefix="/!"))
async def cmd_unban(msg: Message, bot: Bot):
    if await deny(msg, bot, "unban"):
        return
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "no_target")
    try:
        await bot.unban_chat_member(msg.chat.id, uid, only_if_banned=True)
        await db.remove_ban(msg.chat.id, uid)
        await reply(msg, "unban", user=mention(uid, name))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("kick", "кик", "выгнать", prefix="/!"))
async def cmd_kick(msg: Message, bot: Bot):
    if not (t := await _prepare(msg, bot, "kick")):
        return
    uid, name, _ = t
    try:
        await bot.ban_chat_member(msg.chat.id, uid)
        await bot.unban_chat_member(msg.chat.id, uid)
        await reply(msg, "kick", user=mention(uid, name))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(str(e)))


@router.message(Command("report", "репорт", "жалоба", prefix="/!"))
async def cmd_report(msg: Message, bot: Bot):
    s = await db.settings(msg.chat.id)
    report_chat = int(s.get("report_chat") or 0) or REPORT_CHAT_ID
    if not report_chat:
        return await reply(msg, "report_no_chat")
    uid, name, args = await resolve_target(msg)
    if not uid:
        return await reply(msg, "no_target")
    src = msg.reply_to_message
    link = ""
    if src:
        if msg.chat.username:
            link = f"\nhttps://t.me/{msg.chat.username}/{src.message_id}"
        elif str(msg.chat.id).startswith("-100"):
            link = f"\nhttps://t.me/c/{str(msg.chat.id)[4:]}/{src.message_id}"
    quoted = html.escape((src.text or src.caption or "[медиа]")[:300]) if src else "—"
    comment = html.escape(" ".join(args))[:200] if args else ""
    text = (f"🚨 жалоба в «{html.escape(msg.chat.title or '')}»\n"
            f"от: {mention(msg.from_user.id, msg.from_user.first_name)}\n"
            f"на: {mention(uid, name)} (id {uid})\n"
            f"текст: {quoted}" + (f"\nкомментарий: {comment}" if comment else "") + link)
    cid = msg.chat.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔇 мут 1ч", callback_data=f"rep:m:{cid}:{uid}"),
        InlineKeyboardButton(text="⛔ бан", callback_data=f"rep:b:{cid}:{uid}"),
        InlineKeyboardButton(text="🙈 игнор", callback_data=f"rep:i:{cid}:{uid}"),
    ]])
    try:
        await bot.send_message(report_chat, text, reply_markup=kb)
    except TelegramBadRequest as e:
        return await reply(msg, "cant", reason=html.escape(f"не могу писать в чат жалоб ({e.message}). проверь /settings report_chat и что я там есть"))
    try:
        await msg.delete()
    except TelegramBadRequest:
        pass
    sent = await bot.send_message(msg.chat.id, (await _say(msg, "report_sent")))
    await db.remember_bot_message(msg.chat.id, sent.message_id)


@router.message(Command("id", "айди", "chatid", prefix="/!"))
async def cmd_id(msg: Message):
    """!id — id этого чата (для /settings report_chat) и твой id."""
    await msg.answer(f"id чата: <code>{msg.chat.id}</code>\nтвой id: <code>{msg.from_user.id}</code>\n\n"
                     f"чтобы жалобы шли сюда — в основном чате: <code>/settings report_chat {msg.chat.id}</code>")


@router.callback_query(F.data.startswith("rep:"))
async def on_report_action(cb: CallbackQuery, bot: Bot):
    _, action, chat_id, uid = cb.data.split(":")
    chat_id, uid = int(chat_id), int(uid)
    if not await has_rank(bot, chat_id, cb.from_user.id, "report_action"):
        return await cb.answer("нужен ранг младшего админа того чата", show_alert=True)
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


@router.message(Command("rank", "setrank", "ранг", prefix="/!"))
async def cmd_rank(msg: Message, bot: Bot):
    uid, name, args = await resolve_target(msg)
    if not uid:
        return await reply(msg, "rank_usage")
    if not args:  # просто показать
        r = await rank_of(bot, msg.chat.id, uid)
        return await reply(msg, "rank_show", user=mention(uid, name), rank=rank_name(r))
    if await deny(msg, bot, "rank"):
        return
    key = "".join(args).lower().replace("ё", "е").replace(" ", "")
    key = {"младшийадмин": "младший", "старшийадмин": "старший"}.get(key, key)
    if key not in RANK_ALIASES:
        return await reply(msg, "rank_usage")
    new = RANK_ALIASES[key]
    me = await rank_of(bot, msg.chat.id, msg.from_user.id)
    target = await rank_of(bot, msg.chat.id, uid)
    if uid == msg.from_user.id or target >= me or new >= me and msg.from_user.id not in OWNER_IDS:
        return await reply(msg, "target_higher", user=mention(uid, name))
    await db.set_rank(msg.chat.id, uid, new, msg.from_user.id)
    if new == 0:
        return await reply(msg, "rank_removed", user=mention(uid, name))
    await reply(msg, "rank_set", user=mention(uid, name), rank=rank_name(new))


@router.message(Command("unrank", "снятьранг", prefix="/!"))
async def cmd_unrank(msg: Message, bot: Bot):
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "rank_usage")
    if await deny(msg, bot, "rank"):
        return
    if await rank_of(bot, msg.chat.id, uid) >= await rank_of(bot, msg.chat.id, msg.from_user.id):
        return await reply(msg, "target_higher", user=mention(uid, name))
    await db.set_rank(msg.chat.id, uid, 0, msg.from_user.id)
    await reply(msg, "rank_removed", user=mention(uid, name))


@router.message(Command("ranks", "admins", "ранги", "админы", prefix="/!"))
async def cmd_ranks(msg: Message, bot: Bot):
    rows = await db.list_ranks(msg.chat.id)
    lines = [f"• {mention(r['user_id'], r['first_name'])} — {rank_name(r['rank'])}" for r in rows]
    try:
        for a in await bot.get_chat_administrators(msg.chat.id):
            if not a.user.is_bot and all(r["user_id"] != a.user.id for r in rows):
                lines.insert(0, f"• {mention(a.user.id, a.user.first_name)} — {rank_name(4 if a.status == 'creator' else 3)} (Telegram)")
    except TelegramBadRequest:
        pass
    if not lines:
        return await reply(msg, "rank_list_empty")
    await reply(msg, "rank_list", list="\n".join(lines))


async def _say(msg: Message, key: str) -> str:
    from phrases import say
    from utils import style
    return say(await style(msg.chat.id), key, chat_id=msg.chat.id)
