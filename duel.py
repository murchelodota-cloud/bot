"""Дуэли: !дуэль (ответом) [ставка коинов] — вызов; соперник принимает кнопкой; победитель случайный (масса чуть помогает)."""
import asyncio
import random
import time
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import say, ACHIEVEMENTS
from utils import reply, mention, resolve_target, split_args, style

router = Router()
_pending: dict[tuple[int, int, int], dict] = {}   # (chat, a, b) -> {stake, msg, ts}
_cooldown: dict[tuple[int, int], float] = {}      # (chat, user) -> ts
COOLDOWN = 60
WEAPONS = ["🔫 пистолеты", "⚔️ шпаги", "🪄 заклинания", "🥊 кулаки", "🍅 помидоры", "🎤 рэп-баттл", "📚 знания", "🧻 рулоны",
           "🐔 резиновые курицы", "🎮 геймпады", "💅 взгляды", "🍜 лапша", "🪑 стулья", "💃 танцы"]
FINISHES = ["одним движением", "с третьей попытки", "случайно", "красиво", "подло, но эффективно", "под музыку", "с закрытыми глазами",
            "пока никто не смотрел", "после долгой речи", "в последнюю секунду", "благодаря удаче", "чисто на характере"]


async def nm(chat_id: int, uid: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None) or fallback)


@router.message(Command("дуэль", "битва", "бой", "duel", "fight", prefix="!+/"))
async def cmd_duel(msg: Message, bot: Bot):
    chat_id = msg.chat.id
    await db.touch_user(chat_id, msg.from_user)
    uid, name, args = await resolve_target(msg)
    me_id = msg.from_user.id
    if not uid:
        return await reply(msg, "duel_usage")
    if uid == me_id:
        return await reply(msg, "duel_self", user=await nm(chat_id, me_id, msg.from_user.first_name))
    if uid == (await bot.get_me()).id:
        return await reply(msg, "duel_me", user=await nm(chat_id, me_id, msg.from_user.first_name))
    if time.time() - _cooldown.get((chat_id, me_id), 0) < COOLDOWN:
        return await reply(msg, "duel_cooldown", sec=int(COOLDOWN - (time.time() - _cooldown[(chat_id, me_id)])))
    stake = next((int(a) for a in args if a.isdigit()), 0)
    stake = max(0, min(stake, 1000))
    if stake:
        if await db.coins_total(chat_id, me_id) < stake:
            return await reply(msg, "duel_poor", user=await nm(chat_id, me_id, msg.from_user.first_name), stake=stake)
        if await db.coins_total(chat_id, uid) < stake:
            return await reply(msg, "duel_poor", user=await nm(chat_id, uid, name), stake=stake)
    key = (chat_id, me_id, uid)
    if key in _pending:
        return await reply(msg, "duel_exists")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚔️ принять", callback_data=f"duel:{me_id}:{uid}:{stake}:y"),
        InlineKeyboardButton(text="🏳️ струсить", callback_data=f"duel:{me_id}:{uid}:{stake}:n"),
    ]])
    text = say(await style(chat_id), "duel_challenge", chat_id=chat_id, user=await nm(chat_id, me_id, msg.from_user.first_name),
               target=await nm(chat_id, uid, name), stake=stake or "честь")
    sent = await msg.answer(text, reply_markup=kb)
    _pending[key] = {"stake": stake, "msg": sent.message_id, "ts": time.time()}
    await db.remember_bot_message(chat_id, sent.message_id)
    asyncio.create_task(_expire(bot, key))


async def _expire(bot: Bot, key):
    await asyncio.sleep(120)
    p = _pending.pop(key, None)
    if p:
        chat_id, a, b = key
        try:
            await bot.edit_message_reply_markup(chat_id, p["msg"], reply_markup=None)
            m = await bot.send_message(chat_id, say(await style(chat_id), "duel_expired", chat_id=chat_id, target=await nm(chat_id, b)))
            await db.remember_bot_message(chat_id, m.message_id)
        except TelegramBadRequest:
            pass


@router.callback_query(F.data.startswith("duel:"))
async def on_duel(cb: CallbackQuery, bot: Bot):
    _, a, b, stake, ans = cb.data.split(":")
    a, b, stake = int(a), int(b), int(stake)
    chat_id = cb.message.chat.id
    if cb.from_user.id != b:
        return await cb.answer("это не твоя дуэль. смотри и учись.", show_alert=True)
    p = _pending.pop((chat_id, a, b), None)
    if not p:
        return await cb.answer("вызов уже не актуален.", show_alert=True)
    st = await style(chat_id)
    ma, mb = await nm(chat_id, a), await nm(chat_id, b, cb.from_user.first_name)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    if ans == "n":
        await db.duel_result(chat_id, a, b, coward=True)
        await cb.answer()
        await db.log_event(chat_id, "duel", f"🏳️ {mb} струсил(а) перед {ma}")
        m = await bot.send_message(chat_id, say(st, "duel_coward", chat_id=chat_id, user=ma, target=mb))
        return await db.remember_bot_message(chat_id, m.message_id)
    # шанс: 50/50 плюс небольшое влияние массы (не больше 65/35)
    ra, rb = await db.user(chat_id, a), await db.user(chat_id, b)
    ma_, mb_ = (ra["mass"] if ra else 0) + 10, (rb["mass"] if rb else 0) + 10
    pa = max(0.35, min(0.65, ma_ / (ma_ + mb_)))
    winner, loser = (a, b) if random.random() < pa else (b, a)
    _cooldown[(chat_id, a)] = _cooldown[(chat_id, b)] = time.time()
    if stake:
        await db.give_coin(chat_id, loser, winner, "дуэль", stake)
        await db.give_coin(chat_id, 0, loser, "дуэль (проигрыш)", -stake)
    w, l = await db.duel_result(chat_id, winner, loser)
    await db.add_mass(chat_id, winner, 3)
    await db.log_event(chat_id, "duel", f"⚔️ дуэль: {await nm(chat_id, winner)} победил(а) {await nm(chat_id, loser)}" + (f" (ставка {stake} 🪙)" if stake else ""))
    await cb.answer()
    intro = say(st, "duel_start", chat_id=chat_id, user=ma, target=mb, weapon=random.choice(WEAPONS))
    m = await bot.send_message(chat_id, intro)
    await db.remember_bot_message(chat_id, m.message_id)
    await asyncio.sleep(2.5)
    text = say(st, "duel_win", chat_id=chat_id, winner=await nm(chat_id, winner), loser=await nm(chat_id, loser),
               how=random.choice(FINISHES), stake=stake, wins=w)
    if not stake:
        text = text.replace(" (+{} 🪙)".format(stake), "")
    m = await bot.send_message(chat_id, text)
    await db.remember_bot_message(chat_id, m.message_id)
    if w in (10, 50) and await db.give_achievement(chat_id, winner, f"duel{w}"):
        m2 = await bot.send_message(chat_id, say(st, "achievement", chat_id=chat_id, user=await nm(chat_id, winner), ach=ACHIEVEMENTS[f"duel{w}"]))
        await db.remember_bot_message(chat_id, m2.message_id)


@router.message(Command("дуэли", "duels", prefix="!+/"))
async def cmd_duels(msg: Message):
    rows = await db.duel_top(msg.chat.id, 10)
    if not rows:
        return await reply(msg, "duel_top_empty")
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'{i + 1}.'} {await nm(msg.chat.id, r['user_id'])} — {r['wins']} побед / {r['losses']} поражений"
             + (f" / {r['cowards']} 🏳️" if r["cowards"] else "") for i, r in enumerate(rows)]
    await reply(msg, "duel_top", list="\n".join(lines))
