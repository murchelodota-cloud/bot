"""Король и королева дня: вечером голосование среди самых активных за сутки, ночью итоги и +50 коинов."""
import time
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import say, ACHIEVEMENTS
from utils import reply, mention, style, deny

router = Router()


async def nm(chat_id: int, uid: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None) or fallback)


def today() -> str:
    return time.strftime("%Y-%m-%d")


async def candidates(chat_id: int) -> tuple[list, list]:
    """Топ-5 мужчин и топ-5 женщин по активности за сутки. Без пола — в короли."""
    rows = await db.top_period(chat_id, 1, 30)
    kings, queens = [], []
    for r in rows:
        u = await db.user(chat_id, r["user_id"])
        if not u:
            continue
        (queens if u["gender"] == "ж" else kings).append(r["user_id"])
    return kings[:5], queens[:5]


def kb(chat_id: int, kind: str, cands: list, names: dict, counts: dict) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{names[c][:24]} ({counts.get(c, 0)})", callback_data=f"royal:{kind}:{c}")] for c in cands]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def open_vote(bot: Bot, chat_id: int, force: bool = False):
    s = await db.settings(chat_id)
    if not force and (not s.get("royal", 1) or s.get("royal_day") == today()):
        return
    kings, queens = await candidates(chat_id)
    if not kings and not queens:
        return
    await db.set_setting(chat_id, "royal_day", today())
    await db.set_setting(chat_id, "royal_done", "")
    await db.royal_clear(chat_id, today())
    st = await style(chat_id)
    for kind, cands in (("king", kings), ("queen", queens)):
        if not cands:
            continue
        names = {c: await _plain(chat_id, c) for c in cands}
        text = say(st, "royal_open_king" if kind == "king" else "royal_open_queen", chat_id=chat_id,
                   list="\n".join([f"• {await nm(chat_id, c)}" for c in cands]), close=int(s.get("royal_close_hour", 23)))
        sent = await bot.send_message(chat_id, text, reply_markup=kb(chat_id, kind, cands, names, {}))
        await db.remember_bot_message(chat_id, sent.message_id)
        await db.set_setting(chat_id, f"royal_msg_{kind}", sent.message_id)


async def _plain(chat_id: int, uid: int) -> str:
    row = await db.user(chat_id, uid)
    return ((row["nick"] or row["first_name"]) if row else None) or "участник"


@router.callback_query(F.data.startswith("royal:"))
async def on_vote(cb: CallbackQuery, bot: Bot):
    _, kind, cand = cb.data.split(":")
    cand = int(cand)
    chat_id = cb.message.chat.id
    s = await db.settings(chat_id)
    if s.get("royal_day") != today() or s.get("royal_done"):
        return await cb.answer("голосование закрыто.", show_alert=True)
    if cb.from_user.id == cand:
        return await cb.answer("за себя нельзя. скромность — тоже корона.", show_alert=True)
    await db.touch_user(chat_id, cb.from_user)
    await db.royal_vote(chat_id, today(), kind, cb.from_user.id, cand)
    counts = await db.royal_counts(chat_id, today(), kind)
    # список кандидатов — из текущей клавиатуры
    cands = [int(b[0].callback_data.split(":")[2]) for b in cb.message.reply_markup.inline_keyboard] if cb.message.reply_markup else list(counts)
    names = {c: await _plain(chat_id, c) for c in cands}
    try:
        await cb.message.edit_reply_markup(reply_markup=kb(chat_id, kind, cands, names, counts))
    except TelegramBadRequest:
        pass
    await cb.answer("голос принят 👑")


async def close_vote(bot: Bot, chat_id: int, force: bool = False):
    s = await db.settings(chat_id)
    if s.get("royal_day") != today() or s.get("royal_done") == today():
        return
    if not force and time.localtime().tm_hour < int(s.get("royal_close_hour", 23)):
        return
    await db.set_setting(chat_id, "royal_done", today())
    st = await style(chat_id)
    bonus = int(s.get("royal_bonus", 50))
    results = []
    for kind in ("king", "queen"):
        mid = s.get(f"royal_msg_{kind}")
        if mid:
            try:
                await bot.edit_message_reply_markup(chat_id, int(mid), reply_markup=None)
            except TelegramBadRequest:
                pass
        counts = await db.royal_counts(chat_id, today(), kind)
        if not counts:
            continue
        winner = max(counts, key=counts.get)
        await db.give_coin(chat_id, 0, winner, "король/королева дня", bonus)
        await db.add_mass(chat_id, winner, 10)
        await db.give_achievement(chat_id, winner, "royal")
        await db.set_setting(chat_id, f"royal_{kind}", winner)
        await db.log_event(chat_id, "royal", f"{'👑 король' if kind == 'king' else '👸 королева'} дня — {await nm(chat_id, winner)} ({counts[winner]} голосов)")
        results.append(say(st, "royal_king" if kind == "king" else "royal_queen", chat_id=chat_id,
                           user=await nm(chat_id, winner), votes=counts[winner], bonus=bonus))
    if results:
        sent = await bot.send_message(chat_id, "\n\n".join(results))
        await db.remember_bot_message(chat_id, sent.message_id)


async def royal_tick(bot: Bot, chat_id: int):
    """Из часового цикла: открыть голосование в royal_vote_hour, закрыть в royal_close_hour."""
    s = await db.settings(chat_id)
    if not s.get("royal", 1):
        return
    h = time.localtime().tm_hour
    if h >= int(s.get("royal_vote_hour", 20)) and s.get("royal_day") != today():
        await open_vote(bot, chat_id)
    await close_vote(bot, chat_id)


@router.message(Command("король", "королева", "korol", "royal", prefix="!+/"))
async def cmd_royal(msg: Message, bot: Bot):
    """!король — кто сейчас король/королева; !король старт / !король стоп — админ открывает/закрывает голосование вручную."""
    from utils import split_args
    args = [a.lower() for a in split_args(msg)]
    if args and args[0] in ("старт", "start", "голосование"):
        if await deny(msg, bot, "kick"):
            return
        await open_vote(bot, msg.chat.id, force=True)
        return
    if args and args[0] in ("стоп", "итоги", "stop"):
        if await deny(msg, bot, "kick"):
            return
        await close_vote(bot, msg.chat.id, force=True)
        return
    s = await db.settings(msg.chat.id)
    k, q = s.get("royal_king"), s.get("royal_queen")
    if not k and not q:
        return await reply(msg, "royal_none")
    await reply(msg, "royal_current", king=await nm(msg.chat.id, int(k)) if k else "—", queen=await nm(msg.chat.id, int(q)) if q else "—")
