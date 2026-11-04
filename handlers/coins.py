"""Сарра-коины: +похвала (ответом) — подарить коин; +коины — баланс; +лидеры — топ месяца; итоги месяца с наградами."""
import html
import time
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from db import db
from phrases import say, ACHIEVEMENTS
from utils import reply, mention, split_args, resolve_target, style, deny

router = Router()


def month_key(ts: int | None = None) -> str:
    return time.strftime("%Y-%m", time.localtime(ts or time.time()))


def prev_month_key() -> str:
    y, mo = map(int, month_key().split("-"))
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


async def nm(chat_id: int, uid: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, uid)
    name = (row["nick"] or row["first_name"]) if row else fallback
    return mention(uid, name or fallback)


@router.message(Command("похвала", "похвалить", "коин", "коины+", "респект", "coin", "praise", prefix="+/!"))
async def cmd_praise(msg: Message, bot: Bot):
    """+похвала [N] [причина] — ответом. Только владелец чата (ранг 4). По умолчанию 1 коин."""
    chat_id = msg.chat.id
    await db.touch_user(chat_id, msg.from_user)
    if await deny(msg, bot, "rank"):   # rank == ранг «владелец»
        return
    uid, name, args = await resolve_target(msg)
    if not uid:
        return await reply(msg, "coin_usage")
    n, note = 1, args
    if args and args[0].lstrip("-").isdigit():
        n, note = int(args[0]), args[1:]
    n = max(-10000, min(10000, n))
    if n == 0:
        return await reply(msg, "coin_usage")
    await db.give_coin(chat_id, msg.from_user.id, uid, " ".join(note)[:100], n)
    total = await db.coins_total(chat_id, uid)
    me = await nm(chat_id, msg.from_user.id, msg.from_user.first_name)
    target = await nm(chat_id, uid, name)
    if n < 0:
        return await reply(msg, "coin_taken", user=me, target=target, n=-n, total=total)
    await reply(msg, "coin_given", user=me, target=target, n=n, total=total)
    if total >= 50 and await db.give_achievement(chat_id, uid, "coins50"):
        await reply(msg, "achievement", user=target, ach=ACHIEVEMENTS["coins50"])


@router.message(Command("штраф", "забрать", "fine", prefix="+/!"))
async def cmd_fine(msg: Message, bot: Bot):
    """+штраф N — снять коины (владелец)."""
    if await deny(msg, bot, "rank"):
        return
    uid, name, args = await resolve_target(msg)
    if not uid or not args or not args[0].isdigit():
        return await reply(msg, "coin_usage")
    n = min(int(args[0]), 10000)
    await db.give_coin(msg.chat.id, msg.from_user.id, uid, " ".join(args[1:])[:100], -n)
    await reply(msg, "coin_taken", user=await nm(msg.chat.id, msg.from_user.id, msg.from_user.first_name),
                target=await nm(msg.chat.id, uid, name), n=n, total=await db.coins_total(msg.chat.id, uid))


@router.message(Command("коины", "баланс", "coins", prefix="+/!"))
async def cmd_coins(msg: Message):
    chat_id = msg.chat.id
    uid, name, _ = await resolve_target(msg)
    if not uid:
        uid, name = msg.from_user.id, msg.from_user.first_name
    total = await db.coins_total(chat_id, uid)
    month = await db.coins_month(chat_id, uid, month_key())
    rank = await db.coins_rank(chat_id, uid, month_key())
    await reply(msg, "coin_balance", user=await nm(chat_id, uid, name), total=total, month=month, rank=rank)


@router.message(Command("лидеры", "топкоинов", "leaders", prefix="+/!"))
async def cmd_leaders(msg: Message):
    chat_id = msg.chat.id
    rows = await db.coins_top(chat_id, month_key(), 10)
    if not rows:
        return await reply(msg, "coin_top_empty")
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'{i + 1}.'} {await nm(chat_id, r['user_id'])} — {r['n']} 🪙"
             for i, r in enumerate(rows)]
    s = await db.settings(chat_id)
    prize = s.get("month_prize") or "вечная слава и ачивка"
    await reply(msg, "coin_top", list="\n".join(lines), prize=html.escape(prize))


@router.message(Command("приз", "prize", prefix="+/!"))
async def cmd_prize(msg: Message, bot: Bot):
    """+приз текст — что получает победитель месяца (старший админ+)."""
    if await deny(msg, bot, "settings"):
        return
    text = " ".join(split_args(msg)).strip()
    if not text:
        s = await db.settings(msg.chat.id)
        return await reply(msg, "prize_show", prize=html.escape(s.get("month_prize") or "вечная слава и ачивка"))
    await db.set_setting(msg.chat.id, "month_prize", text[:200])
    await reply(msg, "prize_set", prize=html.escape(text[:200]))


async def month_results(bot: Bot, chat_id: int, month: str | None = None):
    """Итоги месяца: объявляет топ-3, выдаёт награды. Вызывается из фонового цикла 1-го числа."""
    month = month or prev_month_key()
    rows = await db.coins_top(chat_id, month, 3)
    if not rows:
        return
    st = await style(chat_id)
    s = await db.settings(chat_id)
    prize = html.escape(s.get("month_prize") or "вечная слава и ачивка")
    bonus = [int(s["month_bonus_mass"]), int(s["month_bonus_mass"]) // 2, int(s["month_bonus_mass"]) // 4]
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        await db.add_mass(chat_id, r["user_id"], bonus[i])
        lines.append(f"{medals[i]} {await nm(chat_id, r['user_id'])} — {r['n']} 🪙 (+{bonus[i]} массы)")
    winner = rows[0]["user_id"]
    await db.give_achievement(chat_id, winner, "month_winner")
    await db.set_setting(chat_id, "month_winner", winner)
    text = say(st, "month_results", chat_id=chat_id, month=month, list="\n".join(lines),
               winner=await nm(chat_id, winner), prize=prize)
    sent = await bot.send_message(chat_id, text)
    await db.remember_bot_message(chat_id, sent.message_id)


async def month_results_due(chat_id: int) -> bool:
    s = await db.settings(chat_id)
    return s.get("month_awarded") != month_key() and time.localtime().tm_mday >= 1


async def mark_month_awarded(chat_id: int):
    await db.set_setting(chat_id, "month_awarded", month_key())
