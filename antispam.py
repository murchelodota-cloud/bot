"""Антифлуд, антиссылки для новичков, антирейд с капчей."""
import time
import re
import html
from collections import defaultdict, deque
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import say
from utils import reply, is_admin, mention, MUTE_PERMS, FULL_PERMS, NEWBIE_PERMS, style

router = Router()

LINK_RE = re.compile(r"(https?://|t\.me/|@\w{5,}|www\.)", re.I)
DEFAULT_BAD = ["хуй", "хуя", "хуе", "хуё", "хую", "пизд", "пидор", "пидар", "блядь", "бляди", "блять",
               "сука", "суки", "суке", "ебал", "ебёт", "ебет", "ебут", "ебан", "заеб", "ёбан", "мудак", "мудил",
               "гандон", "шлюх"]
MAT_RE = re.compile(r"\b(" + "|".join(DEFAULT_BAD) + r")\w*", re.I)
_custom_re: dict[int, re.Pattern | None] = {}   # кэш регулярок для своих слов по чатам


async def custom_bad_re(chat_id: int) -> re.Pattern | None:
    if chat_id not in _custom_re:
        words = await db.badwords(chat_id)
        _custom_re[chat_id] = re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\w*", re.I) if words else None
    return _custom_re[chat_id]


def invalidate_bad(chat_id: int):
    _custom_re.pop(chat_id, None)
_recent: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=6))   # флуд
_joins: dict[int, deque] = defaultdict(lambda: deque(maxlen=50))               # вступления
_raid_until: dict[int, float] = {}                                              # chat -> ts
_pending_captcha: set[tuple[int, int]] = set()
_dup: dict[tuple[int, int], tuple[str, int, float]] = {}                       # (chat,user) -> (текст, счёт, ts)


def raid_active(chat_id: int) -> bool:
    return _raid_until.get(chat_id, 0) > time.time()


# ---------- вступление ----------
@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_join(event: ChatMemberUpdated, bot: Bot):
    chat_id, user = event.chat.id, event.new_chat_member.user
    if user.is_bot:
        return
    await db.touch_user(chat_id, user)
    await db.log_event(chat_id, "join", f"🐣 пришёл(ла) {mention(user.id, user.first_name)}")
    s = await db.settings(chat_id)
    if s.get("welcome", 1) and not raid_active(chat_id):
        st = await style(chat_id)
        m = await bot.send_message(chat_id, say(st, "welcome", chat_id=chat_id, user=mention(user.id, user.first_name),
                                                chat=html.escape(event.chat.title or "чат")))
        await db.remember_bot_message(chat_id, m.message_id)
    if not s["antiraid"]:
        return
    now = time.time()
    _joins[chat_id].append(now)
    per_min = sum(1 for t in _joins[chat_id] if now - t < 60)
    st = await style(chat_id)
    if per_min >= int(s["raid_joins_per_min"]) and not raid_active(chat_id):
        _raid_until[chat_id] = now + 1800  # 30 минут режима рейда
        m = await bot.send_message(chat_id, say(st, "raid_on", chat_id=chat_id, n=per_min))
        await db.remember_bot_message(chat_id, m.message_id)
    if raid_active(chat_id):
        try:
            await bot.restrict_chat_member(chat_id, user.id, MUTE_PERMS)
        except TelegramBadRequest:
            return
        _pending_captcha.add((chat_id, user.id))
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="я не бот 🙋", callback_data=f"captcha:{user.id}")]])
        m = await bot.send_message(chat_id, say(st, "captcha", chat_id=chat_id, user=mention(user.id, user.first_name)), reply_markup=kb)
        await db.remember_bot_message(chat_id, m.message_id)


@router.callback_query(F.data.startswith("captcha:"))
async def on_captcha(cb: CallbackQuery, bot: Bot):
    uid = int(cb.data.split(":")[1])
    if cb.from_user.id != uid:
        return await cb.answer("это не твоя кнопка", show_alert=True)
    chat_id = cb.message.chat.id
    s = await db.settings(chat_id)
    _pending_captcha.discard((chat_id, uid))
    until = int(time.time()) + int(s["newbie_hours"]) * 3600
    try:
        await bot.restrict_chat_member(chat_id, uid, NEWBIE_PERMS, until_date=until)
    except TelegramBadRequest:
        pass
    st = await style(chat_id)
    try:
        await cb.message.edit_text(say(st, "captcha_ok", chat_id=chat_id, user=mention(uid, cb.from_user.first_name)))
    except TelegramBadRequest:
        pass
    await cb.answer()


# ---------- сообщения ----------
@router.message(F.chat.type.in_({"group", "supergroup"}), F.text | F.caption)
async def on_message(msg: Message, bot: Bot):
    s = await db.settings(msg.chat.id)
    if not msg.from_user:
        return
    from utils import rank_of
    rank = await rank_of(bot, msg.chat.id, msg.from_user.id)
    text0 = msg.text or msg.caption or ""
    # мат-фильтр для админов: только предупреждение, без удаления (выкл: /settings mat_admins 0)
    if rank >= 1 and s["matfilter"] and s.get("mat_admins", 1):
        cre = await custom_bad_re(msg.chat.id)
        if MAT_RE.search(text0) or (cre and cre.search(text0)):
            st = await style(msg.chat.id)
            m = await bot.send_message(msg.chat.id, say(st, "mat_soft", chat_id=msg.chat.id, user=mention(msg.from_user.id, msg.from_user.first_name)))
            await db.remember_bot_message(msg.chat.id, m.message_id)
        return
    if not s["antispam"] and not s["matfilter"]:
        return
    if rank >= 1:
        return
    key = (msg.chat.id, msg.from_user.id)
    now = time.time()
    _recent[key].append(now)
    # 6 сообщений за 4 секунды — флуд
    if s["antispam"] and len(_recent[key]) == 6 and now - _recent[key][0] < 4:
        _recent[key].clear()
        try:
            await bot.restrict_chat_member(msg.chat.id, msg.from_user.id, MUTE_PERMS, until_date=int(now) + 300)
        except TelegramBadRequest:
            pass
        return await reply(msg, "flood", user=mention(msg.from_user.id, msg.from_user.first_name))

    text = msg.text or msg.caption or ""
    st = await style(msg.chat.id)
    user_mention = mention(msg.from_user.id, msg.from_user.first_name)

    # одинаковые сообщения подряд: с третьего раза режем
    if s["antispam"] and s["dupfilter"] and len(text.strip()) >= 15:   # короткие «ок», «+» не трогаем
        norm = " ".join(text.lower().split())
        last = _dup.get(key)
        if last and last[0] == norm and now - last[2] < 300:
            count = last[1] + 1
        else:
            count = 1
        _dup[key] = (norm, count, now)
        if count == 3:
            try:
                await msg.delete()
            except TelegramBadRequest:
                pass
            m = await bot.send_message(msg.chat.id, say(st, "dup_warn", chat_id=msg.chat.id, user=user_mention))
            await db.remember_bot_message(msg.chat.id, m.message_id)
            return
        if count > 3:
            try:
                await msg.delete()
            except TelegramBadRequest:
                pass
            return

    # мат: мягкий режим — предупреждаем, жёсткий — удаляем
    cre = await custom_bad_re(msg.chat.id)
    if s["matfilter"] and (MAT_RE.search(text) or (cre and cre.search(text))):
        if s["mat_soft"]:
            m = await bot.send_message(msg.chat.id, say(st, "mat_soft", chat_id=msg.chat.id, user=user_mention))
            await db.remember_bot_message(msg.chat.id, m.message_id)
        else:
            try:
                await msg.delete()
            except TelegramBadRequest:
                return
            m = await bot.send_message(msg.chat.id, say(st, "mat_deleted", chat_id=msg.chat.id, user=user_mention))
            await db.remember_bot_message(msg.chat.id, m.message_id)
            return

    row = await db.user(msg.chat.id, msg.from_user.id)
    mass = row["mass"] if row else 0
    fwd_from_channel = msg.forward_origin is not None and getattr(msg.forward_origin, "chat", None) is not None
    if s["antispam"] and mass < 30 and (LINK_RE.search(text) or fwd_from_channel):
        try:
            await msg.delete()
        except TelegramBadRequest:
            return
        m = await bot.send_message(msg.chat.id, say(st, "spam_deleted", chat_id=msg.chat.id, user=user_mention))
        await db.remember_bot_message(msg.chat.id, m.message_id)


# ---------- управление запретными словами ----------
from aiogram.filters import Command
from utils import deny

badwords_router = Router()   # отдельный роутер: должен стоять ДО антиспама, иначе его перехватит общий хендлер


@badwords_router.message(Command("запретки", "badwords", "матслова", prefix="/!+"))
async def cmd_badwords(msg: Message, bot: Bot):
    custom = await db.badwords(msg.chat.id)
    s = await db.settings(msg.chat.id)
    text = (f"встроенный список (корни слов, {len(DEFAULT_BAD)} шт.): " + ", ".join(DEFAULT_BAD) +
            "\n\nсвои слова чата: " + (", ".join(html.escape(w) for w in custom) if custom else "пока нет") +
            f"\n\nфильтр: {'вкл' if s['matfilter'] else 'выкл'}, режим: {'мягкий (предупреждаю)' if s['mat_soft'] else 'жёсткий (удаляю)'}"
            "\n\n!запретка добавить слово · !запретка удалить слово · !запретка очистить · "
            "/settings matfilter 0|1 · /settings mat_soft 0|1")
    await msg.answer(text, disable_web_page_preview=True)


@badwords_router.message(Command("запретка", "badword", prefix="/!+"))
async def cmd_badword(msg: Message, bot: Bot):
    if await deny(msg, bot, "settings"):
        return
    args = (msg.text or "").split()[1:]
    if len(args) < 1:
        return await msg.answer("!запретка добавить слово · !запретка удалить слово · !запретка очистить")
    action = args[0].lower()
    word = " ".join(args[1:]).lower().strip()
    if action in ("очистить", "clear", "сброс"):
        await db.clear_badwords(msg.chat.id); invalidate_bad(msg.chat.id)
        return await msg.answer("свои запретки стёрла. встроенные остались, их я не отдаю.")
    if not word or len(word) < 2:
        return await msg.answer("слово-то какое? минимум 2 буквы.")
    if action in ("добавить", "add", "+"):
        ok = await db.add_badword(msg.chat.id, word); invalidate_bad(msg.chat.id)
        return await msg.answer(f"«{html.escape(word)}» " + ("теперь под запретом. буду следить. я всегда слежу." if ok else "уже в списке."))
    if action in ("удалить", "del", "-"):
        ok = await db.del_badword(msg.chat.id, word); invalidate_bad(msg.chat.id)
        return await msg.answer(f"«{html.escape(word)}» " + ("убрала из запретных. можно. но осторожно." if ok else "и не было в списке."))
    await msg.answer("!запретка добавить слово · !запретка удалить слово · !запретка очистить")
