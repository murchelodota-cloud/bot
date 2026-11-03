"""Антифлуд, антиссылки для новичков, антирейд с капчей."""
import time
import re
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
MAT_RE = re.compile(
    r"\b(хуй|хуя|хуе|хуё|хую|пизд|пидор|пидар|блядь|бляди|блять|сука|суки|суке|"
    r"ебал|ебёт|ебет|ебут|ебан|заеб|ёбан|мудак|мудил|гандон|шлюх)\w*",
    re.I,
)
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
    s = await db.settings(chat_id)
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
    if not s["antispam"] or not msg.from_user:
        return
    if await is_admin(bot, msg.chat.id, msg.from_user.id):
        return
    key = (msg.chat.id, msg.from_user.id)
    now = time.time()
    _recent[key].append(now)
    # 6 сообщений за 4 секунды — флуд
    if len(_recent[key]) == 6 and now - _recent[key][0] < 4:
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
    if s["dupfilter"] and len(text.strip()) >= 15:   # короткие «ок», «+» не трогаем
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
    if s["matfilter"] and MAT_RE.search(text):
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
    if mass < 30 and (LINK_RE.search(text) or fwd_from_channel):
        try:
            await msg.delete()
        except TelegramBadRequest:
            return
        m = await bot.send_message(msg.chat.id, say(st, "spam_deleted", chat_id=msg.chat.id, user=user_mention))
        await db.remember_bot_message(msg.chat.id, m.message_id)
