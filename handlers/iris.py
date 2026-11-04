"""Ирис-пак: расширенная модерация, настройки чата, анкета, статистика, голосование за бан, созыв.
Ничего из старого не трогает — только добавляет команды. Все ответы — через phrases.py (ключи iris_*)."""
import asyncio
import html
import re
import time
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, ChatPermissions, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.event.bases import SkipHandler
from db import db
from phrases import say, ACHIEVEMENTS
from utils import (reply, deny, rank_of, rank_name, mention, resolve_target, split_args, parse_duration,
                   human_duration, style, MUTE_PERMS, FULL_PERMS, RANK_ALIASES, vote_weight)

router = Router()


async def nm(chat_id: int, uid: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None) or fallback)


def _args_text(msg: Message) -> str:
    """Текст после команды, включая переносы строк."""
    t = msg.text or ""
    parts = t.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


# ======================= МОДЕРАТОРЫ (+модер, повысить, понизить, снять, кто админ) =======================
@router.message(Command("модер", "админ", prefix="+!/"))
async def cmd_moder(msg: Message, bot: Bot):
    """+модер @юзер [1-4] — выдать ранг (по умолчанию модератор)."""
    if await deny(msg, bot, "rank"):
        return
    uid, name, args = await resolve_target(msg)
    if not uid:
        return await reply(msg, "rank_usage")
    lvl = 1
    for a in args:
        if a in RANK_ALIASES and RANK_ALIASES[a] > 0:
            lvl = RANK_ALIASES[a]
    me = await rank_of(bot, msg.chat.id, msg.from_user.id)
    if lvl >= me or await rank_of(bot, msg.chat.id, uid) >= me:
        return await reply(msg, "target_higher", user=await nm(msg.chat.id, uid, name))
    await db.set_rank(msg.chat.id, uid, lvl, msg.from_user.id)
    await reply(msg, "rank_set", user=await nm(msg.chat.id, uid, name), rank=rank_name(lvl))


@router.message(Command("повысить", "понизить", prefix="+!/"))
async def cmd_promote(msg: Message, bot: Bot):
    if await deny(msg, bot, "rank"):
        return
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "rank_usage")
    cur = await db.rank(msg.chat.id, uid)
    me = await rank_of(bot, msg.chat.id, msg.from_user.id)
    up = (msg.text or "").lower().lstrip("+!/").startswith("повысить")
    new = cur + 1 if up else cur - 1
    if new >= me or cur >= me:
        return await reply(msg, "target_higher", user=await nm(msg.chat.id, uid, name))
    new = max(0, new)
    await db.set_rank(msg.chat.id, uid, new, msg.from_user.id)
    if new == 0:
        return await reply(msg, "rank_removed", user=await nm(msg.chat.id, uid, name))
    await reply(msg, "rank_set", user=await nm(msg.chat.id, uid, name), rank=rank_name(new))


@router.message(Command("разжаловать", prefix="+!/"))
async def cmd_demote_full(msg: Message, bot: Bot):
    from handlers.moderation import cmd_unrank
    await cmd_unrank(msg, bot)


# ======================= ВАРНЫ: варнлист, мои варны, -варн, снять все варны =======================
@router.message(Command("варнлист", "warnlist", prefix="+!/"))
async def cmd_warnlist(msg: Message, bot: Bot):
    rows = await db.recent_warns(msg.chat.id, 20)
    if not rows:
        return await reply(msg, "iris_warnlist_empty")
    lines = []
    for r in rows:
        lines.append(f"• {await nm(msg.chat.id, r['user_id'])} — {html.escape(r['reason'])} "
                     f"({time.strftime('%d.%m %H:%M', time.localtime(r['created_at']))}, вес {r['weight']})")
    await reply(msg, "iris_warnlist", list="\n".join(lines))


@router.message(Command("мои", prefix="+!/"))
async def cmd_my(msg: Message, bot: Bot):
    """мои варны / мои преды / мои ачивки / мои коины"""
    sub = (split_args(msg) or [""])[0].lower()
    if sub in ("варны", "преды"):
        from handlers.moderation import cmd_warns
        return await cmd_warns(msg.model_copy(update={"text": "/warns"}))
    if sub == "ачивки":
        ach = await db.achievements(msg.chat.id, msg.from_user.id)
        return await msg.answer("🏆 твои ачивки:\n" + ("\n".join(f"🎗 {ACHIEVEMENTS.get(a, a)}" for a in ach) or "пока нет. но я верю."))
    if sub == "коины":
        from handlers.coins import cmd_coins
        return await cmd_coins(msg.model_copy(update={"text": "+коины"}))
    await msg.answer("мои варны · мои ачивки · мои коины")


@router.message(Command("варн", "пред", prefix="-"))
async def cmd_unwarn_last(msg: Message, bot: Bot):
    """-варн @юзер — снять последний пред."""
    if await deny(msg, bot, "unwarn"):
        return
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "no_target")
    ok = await db.remove_last_warn(msg.chat.id, uid)
    await reply(msg, "iris_warn_removed" if ok else "iris_warn_none", user=await nm(msg.chat.id, uid, name),
                left=await db.active_warns(msg.chat.id, uid))


@router.message(Command("снять", prefix="+!/"))
async def cmd_remove(msg: Message, bot: Bot):
    """снять все варны @юзер · снять варны 2 @юзер · снять @юзер (ранг)"""
    args = [a.lower() for a in split_args(msg)]
    if "варн" in " ".join(args) or "пред" in " ".join(args):
        if await deny(msg, bot, "unwarn"):
            return
        uid, name, _ = await resolve_target(msg)
        if not uid:
            return await reply(msg, "no_target")
        n = next((int(a) for a in args if a.isdigit()), None)
        if n is None or "все" in args:
            await db.clear_warns(msg.chat.id, uid)
        else:
            for _ in range(n):
                await db.remove_last_warn(msg.chat.id, uid)
        return await reply(msg, "iris_warn_removed", user=await nm(msg.chat.id, uid, name), left=await db.active_warns(msg.chat.id, uid))
    from handlers.moderation import cmd_unrank
    await cmd_unrank(msg, bot)


# ======================= МУТЫ / БАНЫ: муты, проверить мут, банлист, причина, амнистия, кик тихо =======================
@router.message(Command("муты", "mutes", prefix="+!/"))
async def cmd_mutes(msg: Message):
    rows = await db.active_mutes(msg.chat.id)
    if not rows:
        return await reply(msg, "iris_mutes_empty")
    lines = [f"🔇 {await nm(msg.chat.id, r['user_id'])} — ещё {human_duration(max(60, r['until'] - int(time.time())))}"
             + (f" ({html.escape(r['reason'])})" if r["reason"] else "") for r in rows]
    await reply(msg, "iris_mutes", list="\n".join(lines))


@router.message(Command("проверить", prefix="+!/"))
async def cmd_check(msg: Message, bot: Bot):
    """проверить мут @юзер"""
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "no_target")
    try:
        cm = await bot.get_chat_member(msg.chat.id, uid)
        muted = cm.status == "restricted" and not cm.can_send_messages
        banned = cm.status == "kicked"
    except TelegramBadRequest:
        muted = banned = False
    key = "iris_check_banned" if banned else "iris_check_muted" if muted else "iris_check_free"
    await reply(msg, key, user=await nm(msg.chat.id, uid, name))


@router.message(Command("банлист", "banlist", prefix="+!/"))
async def cmd_banlist(msg: Message):
    rows = await db.bans(msg.chat.id, 30)
    if not rows:
        return await reply(msg, "iris_banlist_empty")
    lines = [f"⛔ {await nm(msg.chat.id, r['user_id'])} — {html.escape(r['reason'] or 'без причины')} "
             f"({time.strftime('%d.%m', time.localtime(r['ts']))}, выдал {await nm(msg.chat.id, r['by_user'])})" for r in rows]
    await reply(msg, "iris_banlist", list="\n".join(lines))


@router.message(Command("причина", "reason", prefix="+!/"))
async def cmd_reason(msg: Message):
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "no_target")
    b = await db.ban_info(msg.chat.id, uid)
    if not b:
        return await reply(msg, "iris_reason_none", user=await nm(msg.chat.id, uid, name))
    await reply(msg, "iris_reason", user=await nm(msg.chat.id, uid, name), reason=html.escape(b["reason"] or "без причины"),
                by=await nm(msg.chat.id, b["by_user"]), date=time.strftime("%d.%m.%Y", time.localtime(b["ts"])))


@router.message(Command("амнистия", "amnesty", prefix="+!/"))
async def cmd_amnesty(msg: Message, bot: Bot):
    if await deny(msg, bot, "unban"):
        return
    rows = await db.bans(msg.chat.id, 500)
    n = 0
    for r in rows:
        try:
            await bot.unban_chat_member(msg.chat.id, r["user_id"], only_if_banned=True)
            n += 1
        except TelegramBadRequest:
            pass
    await db.clear_bans(msg.chat.id)
    await reply(msg, "iris_amnesty", n=n)


@router.message(Command("кик", "kick", prefix="+!/"))
async def cmd_kick_ext(msg: Message, bot: Bot):
    """кик тихо @юзер · кик молчунов 7 · кик неактив 30 — остальное уходит в обычный /kick."""
    args = [a.lower() for a in split_args(msg)]
    if not args or args[0] not in ("тихо", "молчунов", "неактив"):
        raise SkipHandler
    if await deny(msg, bot, "kick"):
        return
    chat_id = msg.chat.id
    if args[0] == "тихо":
        uid, name, _ = await resolve_target(msg.model_copy(update={"text": "/kick " + " ".join(args[1:])}))
        if not uid:
            return await reply(msg, "no_target")
        if await rank_of(bot, chat_id, uid) >= await rank_of(bot, chat_id, msg.from_user.id):
            return await reply(msg, "target_higher", user=await nm(chat_id, uid, name))
        try:
            await bot.ban_chat_member(chat_id, uid)
            await bot.unban_chat_member(chat_id, uid)
            await msg.delete()
            if msg.reply_to_message:
                await msg.reply_to_message.delete()
        except TelegramBadRequest:
            pass
        return
    days = next((int(a) for a in args[1:] if a.isdigit()), 7 if args[0] == "молчунов" else 30)
    if await rank_of(bot, chat_id, msg.from_user.id) < 3:
        return await reply(msg, "no_rank", need=rank_name(3), have=rank_name(await rank_of(bot, chat_id, msg.from_user.id)))
    victims = await (db.silent_users(chat_id, days) if args[0] == "молчунов" else db.inactive_users(chat_id, days))
    n = 0
    for uid in victims:
        if await rank_of(bot, chat_id, uid) >= 1:
            continue
        try:
            await bot.ban_chat_member(chat_id, uid)
            await bot.unban_chat_member(chat_id, uid)
            n += 1
            await asyncio.sleep(0.3)
        except TelegramBadRequest:
            pass
    await reply(msg, "iris_masskick", n=n, days=days)


# ======================= ЧИСТКА: -смс N, !пург =======================
@router.message(Command("смс", "sms", "del", prefix="-"))
async def cmd_delete(msg: Message, bot: Bot):
    """-смс — удалить сообщение, на которое ответил; -смс N — N сообщений выше; добавь «тихо» чтобы не отчитывалась."""
    if await deny(msg, bot, "warn"):
        return
    args = [a.lower() for a in split_args(msg)]
    quiet = "тихо" in args
    n = next((int(a) for a in args if a.isdigit()), 0)
    deleted = 0
    if msg.reply_to_message:
        try:
            await msg.reply_to_message.delete()
            deleted += 1
        except TelegramBadRequest:
            pass
    elif n:
        n = min(n, 100)
        for mid in range(msg.message_id - 1, msg.message_id - 1 - n, -1):
            try:
                await bot.delete_message(msg.chat.id, mid)
                deleted += 1
            except TelegramBadRequest:
                pass
    try:
        await msg.delete()
    except TelegramBadRequest:
        pass
    if not quiet and deleted:
        sent = await bot.send_message(msg.chat.id, say(await style(msg.chat.id), "iris_deleted", chat_id=msg.chat.id, n=deleted))
        await db.remember_bot_message(msg.chat.id, sent.message_id)


@router.message(Command("пург", "purge", prefix="+!/"))
async def cmd_purge(msg: Message, bot: Bot):
    """!пург (ответом) — удалить всё от того сообщения до этого; !пург N — только N сообщений после него."""
    if await deny(msg, bot, "kick"):
        return
    if not msg.reply_to_message:
        return await reply(msg, "no_target")
    args = [a.lower() for a in split_args(msg)]
    quiet = "тихо" in args
    n = next((int(a) for a in args if a.isdigit()), None)
    start = msg.reply_to_message.message_id
    end = min(msg.message_id, start + n) if n else msg.message_id
    deleted = 0
    for mid in range(start, end + 1):
        try:
            await bot.delete_message(msg.chat.id, mid)
            deleted += 1
        except TelegramBadRequest:
            pass
    if not quiet:
        sent = await bot.send_message(msg.chat.id, say(await style(msg.chat.id), "iris_deleted", chat_id=msg.chat.id, n=deleted))
        await db.remember_bot_message(msg.chat.id, sent.message_id)


# ======================= НАСТРОЙКИ ЧАТА: закреп, правила, приветствие, +чат/-чат, ссылки, чат-ссылка, название =======================
@router.message(Command("закреп", "пин", "pin", prefix="+!/"))
async def cmd_pin(msg: Message, bot: Bot):
    if await deny(msg, bot, "kick"):
        return
    if not msg.reply_to_message:
        return await reply(msg, "no_target")
    try:
        await bot.pin_chat_message(msg.chat.id, msg.reply_to_message.message_id, disable_notification=True)
        await reply(msg, "iris_pinned")
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(e.message))


@router.message(Command("открепить", "анпин", "unpin", prefix="+!/"))
async def cmd_unpin(msg: Message, bot: Bot):
    if await deny(msg, bot, "kick"):
        return
    try:
        if msg.reply_to_message:
            await bot.unpin_chat_message(msg.chat.id, msg.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(msg.chat.id)
        await reply(msg, "iris_unpinned")
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(e.message))


@router.message(Command("правила", "rules", prefix="+"))
async def cmd_rules_set(msg: Message, bot: Bot):
    if await deny(msg, bot, "settings"):
        return
    text = _args_text(msg)
    if not text:
        return await reply(msg, "iris_rules_usage")
    await db.set_setting(msg.chat.id, "rules", text[:3500])
    await reply(msg, "iris_rules_set")


@router.message(Command("правила", "rules", prefix="-"))
async def cmd_rules_del(msg: Message, bot: Bot):
    if await deny(msg, bot, "settings"):
        return
    await db.set_setting(msg.chat.id, "rules", "")
    await reply(msg, "iris_rules_removed")


@router.message(Command("правила", "rules", prefix="!/"))
async def cmd_rules(msg: Message):
    s = await db.settings(msg.chat.id)
    rules = s.get("rules") or ""
    if not rules:
        return await reply(msg, "iris_rules_none")
    await reply(msg, "iris_rules", rules=html.escape(rules))


@router.message(Command("приветствие", "welcome", prefix="+"))
async def cmd_welcome_set(msg: Message, bot: Bot):
    """+приветствие текст — то же, что /phrase add welcome, но заменяет все предыдущие."""
    if await deny(msg, bot, "phrase"):
        return
    text = _args_text(msg)
    if not text:
        return await reply(msg, "iris_welcome_usage")
    text = text.replace("{имя}", "{user}").replace("{чат}", "{chat}")
    import phrases
    await db.reset_custom_phrases(msg.chat.id, "welcome")
    await db.add_custom_phrase(msg.chat.id, "welcome", text[:400])
    phrases.CUSTOM.setdefault(msg.chat.id, {})["welcome"] = [text[:400]]
    await reply(msg, "iris_welcome_set")


@router.message(Command("приветствие", "welcome", prefix="-"))
async def cmd_welcome_off(msg: Message, bot: Bot):
    if await deny(msg, bot, "settings"):
        return
    await db.set_setting(msg.chat.id, "welcome", 0)
    await reply(msg, "iris_welcome_off")


@router.message(Command("чат", "chat", prefix="+-"))
async def cmd_chat_lock(msg: Message, bot: Bot):
    """-чат — закрыть чат для обычных участников, +чат — открыть."""
    if await deny(msg, bot, "settings"):
        return
    lock = (msg.text or "").startswith("-")
    try:
        await bot.set_chat_permissions(msg.chat.id, MUTE_PERMS if lock else FULL_PERMS)
        await reply(msg, "iris_chat_locked" if lock else "iris_chat_unlocked")
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(e.message))


@router.message(Command("ссылки", "links", prefix="+-"))
async def cmd_links(msg: Message, bot: Bot):
    if await deny(msg, bot, "settings"):
        return
    on = (msg.text or "").startswith("+")
    await db.set_setting(msg.chat.id, "antispam", 0 if on else 1)
    await reply(msg, "iris_links_on" if on else "iris_links_off")


@router.message(Command("чат-ссылка", "ссылка", "invite", prefix="+!/"))
async def cmd_invite(msg: Message, bot: Bot):
    if await deny(msg, bot, "kick"):
        return
    try:
        link = await bot.export_chat_invite_link(msg.chat.id)
        await msg.answer(f"🔗 {link}")
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(e.message))


@router.message(Command("название", "title", prefix="+!/"))
async def cmd_title(msg: Message, bot: Bot):
    if await deny(msg, bot, "settings"):
        return
    text = _args_text(msg)
    if not text:
        return await msg.answer("!название Новое имя чата")
    try:
        await bot.set_chat_title(msg.chat.id, text[:128])
        await reply(msg, "iris_title_set", title=html.escape(text[:128]))
    except TelegramBadRequest as e:
        await reply(msg, "cant", reason=html.escape(e.message))


# ======================= АНКЕТА: звание, пол, город, др, о себе =======================
@router.message(Command("звание", "title_", "rank_title", prefix="+"))
async def cmd_title_set(msg: Message):
    await db.touch_user(msg.chat.id, msg.from_user)
    text = _args_text(msg)[:30]
    if not text:
        return await reply(msg, "iris_ptitle_usage")
    await db.set_user_field(msg.chat.id, msg.from_user.id, "title", text)
    await reply(msg, "iris_ptitle_set", user=await nm(msg.chat.id, msg.from_user.id, msg.from_user.first_name), title=html.escape(text))


@router.message(Command("звание", prefix="-"))
async def cmd_title_del(msg: Message):
    await db.set_user_field(msg.chat.id, msg.from_user.id, "title", None)
    await reply(msg, "iris_ptitle_removed", user=await nm(msg.chat.id, msg.from_user.id, msg.from_user.first_name))


@router.message(Command("пол", "gender", prefix="+!/"))
async def cmd_gender(msg: Message):
    await db.touch_user(msg.chat.id, msg.from_user)
    g = (_args_text(msg) or "").lower()[:1]
    if g not in ("м", "ж"):
        return await reply(msg, "iris_gender_usage")
    await db.set_user_field(msg.chat.id, msg.from_user.id, "gender", g)
    await reply(msg, "iris_gender_set", user=await nm(msg.chat.id, msg.from_user.id, msg.from_user.first_name),
                gender="мужской" if g == "м" else "женский")


@router.message(Command("город", "city", prefix="+!/"))
async def cmd_city(msg: Message):
    await db.touch_user(msg.chat.id, msg.from_user)
    text = _args_text(msg)[:40]
    if not text:
        return await reply(msg, "iris_city_usage")
    if text.lower() in ("снять", "удалить", "-"):
        await db.set_user_field(msg.chat.id, msg.from_user.id, "city", None)
        return await reply(msg, "iris_city_removed")
    await db.set_user_field(msg.chat.id, msg.from_user.id, "city", text)
    await reply(msg, "iris_city_set", user=await nm(msg.chat.id, msg.from_user.id, msg.from_user.first_name), city=html.escape(text))


@router.message(Command("др", "bday", "birthday", prefix="+!/"))
async def cmd_bday(msg: Message):
    """+др 24.03 или 24.03.1999 — Саррочка поздравит в этот день."""
    await db.touch_user(msg.chat.id, msg.from_user)
    text = _args_text(msg).strip()
    m = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?", text)
    if not m:
        return await reply(msg, "iris_bday_usage")
    d, mo = int(m.group(1)), int(m.group(2))
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return await reply(msg, "iris_bday_usage")
    y = m.group(3)
    y = (("19" if len(y) == 2 and int(y) > 30 else "20") + y) if y and len(y) == 2 else y
    val = f"{d:02d}.{mo:02d}" + (f".{y}" if y else "")
    await db.set_user_field(msg.chat.id, msg.from_user.id, "birthday", val)
    await reply(msg, "iris_bday_set", user=await nm(msg.chat.id, msg.from_user.id, msg.from_user.first_name), date=val)


@router.message(Command("о", "about", prefix="+!/"))
async def cmd_about(msg: Message):
    """о себе <текст> == +описание"""
    args = split_args(msg)
    if args and args[0].lower() == "себе":
        from handlers.whoami import cmd_bio
        return await cmd_bio(msg.model_copy(update={"text": "+описание " + " ".join(args[1:])}))
    raise SkipHandler


async def birthday_check(bot: Bot, chat_id: int):
    """Вызывается из фонового цикла раз в час: поздравляет именинников после 10:00."""
    if time.localtime().tm_hour < 10:
        return
    today = time.strftime("%d.%m")
    s = await db.settings(chat_id)
    if s.get("bday_done") == time.strftime("%Y-%m-%d"):
        return
    await db.set_setting(chat_id, "bday_done", time.strftime("%Y-%m-%d"))
    for r in await db.birthdays_today(chat_id, today):
        await db.add_mass(chat_id, r["user_id"], 50)
        await db.give_coin(chat_id, 0, r["user_id"], "день рождения", 20)
        text = say(await style(chat_id), "iris_bday_today", chat_id=chat_id, user=await nm(chat_id, r["user_id"]))
        sent = await bot.send_message(chat_id, text)
        await db.remember_bot_message(chat_id, sent.message_id)


# ======================= СТАТИСТИКА: стата/топ с периодом, олды, новички, ники =======================
PERIODS = {"сутки": 1, "день": 1, "неделя": 7, "неделю": 7, "месяц": 30, "вся": 0, "всё": 0, "все": 0}


@router.message(Command("стата", "stats", "актив", prefix="+!/"))
async def cmd_stats(msg: Message):
    """стата [N] [сутки|неделя|месяц|вся] — топ по сообщениям."""
    args = [a.lower() for a in split_args(msg)]
    limit = next((int(a) for a in args if a.isdigit()), 10)
    period = next((PERIODS[a] for a in args if a in PERIODS), 1)
    await send_top(msg, period, min(limit, 50))


async def send_top(msg: Message, days: int, limit: int):
    rows = await db.top_period(msg.chat.id, days, limit)
    name = {0: "за всё время", 1: "за сутки", 7: "за неделю", 30: "за месяц"}.get(days, f"за {days} дн")
    if not rows:
        return await reply(msg, "iris_top_empty", period=name)
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'{i + 1}.'} {await nm(msg.chat.id, r['user_id'])} — {r['n']}" for i, r in enumerate(rows)]
    await reply(msg, "iris_top", period=name, list="\n".join(lines))


@router.message(Command("олды", "старожилы", "olds", prefix="+!/"))
async def cmd_olds(msg: Message):
    rows = await db.oldest(msg.chat.id, 15)
    now = int(time.time())
    lines = [f"{i + 1}. {await nm(msg.chat.id, r['user_id'])} — с {time.strftime('%d.%m.%Y', time.localtime(r['joined_at']))}"
             + (" 🔥" if now - (r["last_seen"] or 0) < 86400 else "") for i, r in enumerate(rows)]
    await reply(msg, "iris_olds", list="\n".join(lines) or "—")


@router.message(Command("новички", "newbies", prefix="+!/"))
async def cmd_newbies(msg: Message):
    rows = await db.newest(msg.chat.id, 15)
    lines = [f"{i + 1}. {await nm(msg.chat.id, r['user_id'])} — {time.strftime('%d.%m %H:%M', time.localtime(r['joined_at']))}" for i, r in enumerate(rows)]
    await reply(msg, "iris_newbies", list="\n".join(lines) or "—")


@router.message(Command("ники", "nicks", prefix="+!/"))
async def cmd_nicks(msg: Message):
    rows = await db.all_nicks(msg.chat.id)
    if not rows:
        return await reply(msg, "iris_nicks_empty")
    lines = [f"• {mention(r['user_id'], r['first_name'])} — {html.escape(r['nick'])}" for r in rows]
    text = say(await style(msg.chat.id), "iris_nicks", chat_id=msg.chat.id, list="\n".join(lines))
    await msg.answer(text[:4000], disable_web_page_preview=True)


@router.message(Command("кто", prefix="+!/"))
async def cmd_who_admin(msg: Message, bot: Bot):
    """кто админ / кто здесь власть → список рангов; остальное — обычный !кто."""
    args = [a.lower() for a in split_args(msg)]
    if args and args[0] in ("админ", "админы", "власть", "здесь", "модер", "модеры"):
        from handlers.moderation import cmd_ranks
        return await cmd_ranks(msg, bot)
    raise SkipHandler


# ======================= ГОЛОСОВАНИЕ ЗА БАН: гб @юзер =======================
_banvotes: dict[tuple[int, int], dict] = {}


@router.message(Command("гб", "голосбан", "banvote", prefix="+!/"))
async def cmd_banvote(msg: Message, bot: Bot):
    """гб @юзер [голосов] — голосование за бан (по умолчанию 5 голосов, 10 минут). гб стоп @юзер — отменить."""
    args = [a.lower() for a in split_args(msg)]
    chat_id = msg.chat.id
    if args and args[0] == "стоп":
        if await deny(msg, bot, "kick"):
            return
        uid, name, _ = await resolve_target(msg.model_copy(update={"text": "/x " + " ".join(args[1:])}))
        v = _banvotes.pop((chat_id, uid), None) if uid else None
        return await reply(msg, "iris_gb_stopped" if v else "no_target")
    uid, name, rest = await resolve_target(msg)
    if not uid:
        return await reply(msg, "iris_gb_usage")
    if await rank_of(bot, chat_id, uid) >= 1:
        return await reply(msg, "target_higher", user=await nm(chat_id, uid, name))
    need = next((int(a) for a in rest if a.isdigit()), 5)
    if (chat_id, uid) in _banvotes:
        return await reply(msg, "iris_gb_exists")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"⛔ за бан (0/{need})", callback_data=f"gb:{uid}:{need}")]])
    text = say(await style(chat_id), "iris_gb_open", chat_id=chat_id, user=await nm(chat_id, msg.from_user.id, msg.from_user.first_name),
               target=await nm(chat_id, uid, name), need=need)
    sent = await msg.answer(text, reply_markup=kb)
    _banvotes[(chat_id, uid)] = {"voters": set(), "msg": sent.message_id, "need": need}
    await db.remember_bot_message(chat_id, sent.message_id)
    asyncio.create_task(_gb_expire(bot, chat_id, uid, sent.message_id))


async def _gb_expire(bot: Bot, chat_id: int, uid: int, mid: int):
    await asyncio.sleep(600)
    v = _banvotes.pop((chat_id, uid), None)
    if v:
        try:
            await bot.edit_message_reply_markup(chat_id, mid, reply_markup=None)
            m = await bot.send_message(chat_id, say(await style(chat_id), "iris_gb_failed", chat_id=chat_id, target=await nm(chat_id, uid)))
            await db.remember_bot_message(chat_id, m.message_id)
        except TelegramBadRequest:
            pass


@router.callback_query(F.data.startswith("gb:"))
async def on_gb(cb: CallbackQuery, bot: Bot):
    _, uid, need = cb.data.split(":")
    uid, need = int(uid), int(need)
    chat_id = cb.message.chat.id
    v = _banvotes.get((chat_id, uid))
    if not v:
        return await cb.answer("голосование закрыто", show_alert=True)
    if cb.from_user.id == uid:
        return await cb.answer("за себя нельзя. даже если очень хочется.", show_alert=True)
    v["voters"].add(cb.from_user.id)
    n = len(v["voters"])
    if n >= need:
        _banvotes.pop((chat_id, uid), None)
        try:
            await bot.ban_chat_member(chat_id, uid)
            await db.record_ban(chat_id, uid, 0, "голосование чата")
            await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        m = await bot.send_message(chat_id, say(await style(chat_id), "iris_gb_done", chat_id=chat_id, target=await nm(chat_id, uid), n=n))
        await db.remember_bot_message(chat_id, m.message_id)
        return await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"⛔ за бан ({n}/{need})", callback_data=f"gb:{uid}:{need}")]]))
    except TelegramBadRequest:
        pass
    await cb.answer(f"голос принят ({n}/{need})")


# ======================= СОЗЫВ: созвать всех / позвать =======================
@router.message(Command("созвать", "позвать", "all", "общий", prefix="+!/"))
async def cmd_call(msg: Message, bot: Bot):
    """созвать всех [текст] — упомянуть всех, кого я видела (младший админ+). позвать (ответом) — упомянуть автора."""
    if await deny(msg, bot, "kick"):
        return
    chat_id = msg.chat.id
    text = _args_text(msg)
    for w in ("всех", "сбор", "онлайн"):
        text = re.sub(rf"^{w}\s*", "", text, flags=re.I)
    users = await db.all_users(chat_id, 200)
    if not users:
        return await reply(msg, "iris_call_empty")
    head = say(await style(chat_id), "iris_call", chat_id=chat_id, text=html.escape(text) or "общий сбор!")
    sent = await msg.answer(head)
    await db.remember_bot_message(chat_id, sent.message_id)
    chunk = []
    for r in users:
        chunk.append(mention(r["user_id"], r["nick"] or r["first_name"]))
        if len(chunk) == 5:
            await msg.answer(", ".join(chunk))
            chunk = []
            await asyncio.sleep(1.2)
    if chunk:
        await msg.answer(", ".join(chunk))
