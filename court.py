import asyncio
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import say, ACHIEVEMENTS
from utils import reply, deny, mention, resolve_target, vote_weight, style

router = Router()


def kb(court_id: int, yes: str, no: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ снять бан ({yes})", callback_data=f"court:{court_id}:yes"),
        InlineKeyboardButton(text=f"❌ оставить ({no})", callback_data=f"court:{court_id}:no"),
    ]])


@router.message(Command("court", "суд", prefix="/!"))
async def cmd_court(msg: Message, bot: Bot):
    if await deny(msg, bot, "court"):
        return
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "no_target")
    s = await db.settings(msg.chat.id)
    st = await style(msg.chat.id)
    minutes = int(s["court_minutes"])
    text = say(st, "court_open", chat_id=msg.chat.id, by=mention(msg.from_user.id, msg.from_user.first_name),
               target=mention(uid, name), time=minutes)
    sent = await msg.answer(text, reply_markup=kb(0, "0", "0"))
    court_id = await db.create_court(msg.chat.id, sent.message_id, uid, name, "unban")
    await sent.edit_reply_markup(reply_markup=kb(court_id, "0", "0"))
    await db.remember_bot_message(msg.chat.id, sent.message_id)
    asyncio.create_task(_finish_later(bot, court_id, minutes * 60))


@router.callback_query(F.data.startswith("court:"))
async def on_vote(cb: CallbackQuery, bot: Bot):
    _, cid, choice = cb.data.split(":")
    court = await db.court(int(cid))
    st = await style(cb.message.chat.id)
    if not court or court["closed"]:
        return await cb.answer(say(st, "court_closed", chat_id=cb.message.chat.id), show_alert=True)
    row = await db.touch_user(cb.message.chat.id, cb.from_user)
    await db.vote(court["id"], cb.from_user.id, choice, vote_weight(row["mass"]))
    t = await db.tally(court["id"])
    try:
        await cb.message.edit_reply_markup(reply_markup=kb(court["id"], str(t["yes"][0]), str(t["no"][0])))
    except TelegramBadRequest:
        pass
    await cb.answer(f"твой голос весит {vote_weight(row['mass'])}")


async def _finish_later(bot: Bot, court_id: int, delay: int):
    await asyncio.sleep(delay)
    court = await db.court(court_id)
    if not court or court["closed"]:
        return
    await db.close_court(court_id)
    chat_id = court["chat_id"]
    st = await style(chat_id)
    t = await db.tally(court_id)
    target = mention(court["target_id"], court["target_name"])
    won = t["yes"][0] > t["no"][0]
    if won:
        try:
            await bot.unban_chat_member(chat_id, court["target_id"], only_if_banned=True)
        except TelegramBadRequest:
            pass
        for uid in await db.court_voters(court_id, "yes"):
            if await db.give_achievement(chat_id, uid, "peacemaker"):
                u = await db.user(chat_id, uid)
                m = await bot.send_message(chat_id, say(st, "achievement", chat_id=chat_id, user=mention(uid, u["first_name"] if u else None),
                                                        ach=ACHIEVEMENTS["peacemaker"]))
                await db.remember_bot_message(chat_id, m.message_id)
    await db.log_event(chat_id, "court", f"⚖️ суд: {target} — {'разбанен' if won else 'остаётся в бане'} ({t['yes'][0]} / {t['no'][0]})")
    text = say(st, "court_yes" if won else "court_no", chat_id=chat_id, target=target, yes=t["yes"][0], no=t["no"][0])
    try:
        await bot.edit_message_reply_markup(chat_id, court["message_id"], reply_markup=None)
    except TelegramBadRequest:
        pass
    sent = await bot.send_message(chat_id, text)
    await db.remember_bot_message(chat_id, sent.message_id)
