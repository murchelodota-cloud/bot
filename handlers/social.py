"""Социалка через «+»: +ник, +брак, +развод, +пара, +браки, +влюблен, +люблю сарру."""
import html
import time
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import say, ACHIEVEMENTS
from utils import reply, mention, split_args, resolve_target, style

router = Router()
NICK_MAX = 24


async def name_of(chat_id: int, user_id: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, user_id)
    if row and row["nick"]:
        return row["nick"]
    return (row["first_name"] if row else None) or fallback or "кто-то"


async def m(chat_id: int, user_id: int, fallback: str | None = None) -> str:
    return mention(user_id, await name_of(chat_id, user_id, fallback))


# ---------- +ник ----------
@router.message(Command("ник", "nick", prefix="+/!"))
async def cmd_nick(msg: Message):
    await db.touch_user(msg.chat.id, msg.from_user)
    args = split_args(msg)
    if not args:
        row = await db.user(msg.chat.id, msg.from_user.id)
        if row and row["nick"]:
            return await reply(msg, "nick_show", user=mention(msg.from_user.id, msg.from_user.first_name), nick=html.escape(row["nick"]))
        return await reply(msg, "nick_usage")
    nick = " ".join(args).strip()
    if nick.lower() in ("снять", "удалить", "нет", "-", "none"):
        await db.set_nick(msg.chat.id, msg.from_user.id, None)
        return await reply(msg, "nick_removed", user=mention(msg.from_user.id, msg.from_user.first_name))
    if len(nick) > NICK_MAX or "<" in nick or ">" in nick:
        return await reply(msg, "nick_bad", max=NICK_MAX)
    await db.set_nick(msg.chat.id, msg.from_user.id, nick)
    await reply(msg, "nick_set", user=mention(msg.from_user.id, msg.from_user.first_name), nick=html.escape(nick))


# ---------- +брак ----------
@router.message(Command("брак", "marry", "женись", "выходи", prefix="+/!"))
async def cmd_marry(msg: Message):
    chat_id = msg.chat.id
    await db.touch_user(chat_id, msg.from_user)
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "marry_usage")
    if uid == msg.from_user.id:
        return await reply(msg, "marry_self")
    if await db.partner(chat_id, msg.from_user.id):
        return await reply(msg, "marry_already_me")
    if await db.partner(chat_id, uid):
        return await reply(msg, "marry_taken", user=await m(chat_id, uid, name))
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="да 💍", callback_data=f"marry:{msg.from_user.id}:{uid}:yes"),
        InlineKeyboardButton(text="нет 💔", callback_data=f"marry:{msg.from_user.id}:{uid}:no"),
    ]])
    st = await style(chat_id)
    text = say(st, "marry_proposal", chat_id=chat_id, user=await m(chat_id, msg.from_user.id, msg.from_user.first_name),
               target=await m(chat_id, uid, name))
    sent = await msg.answer(text, reply_markup=kb)
    await db.remember_bot_message(chat_id, sent.message_id)


@router.callback_query(F.data.startswith("marry:"))
async def on_marry(cb: CallbackQuery):
    _, a, b, ans = cb.data.split(":")
    a, b = int(a), int(b)
    chat_id = cb.message.chat.id
    if cb.from_user.id != b:
        return await cb.answer("это предложение не тебе. не завидуй.", show_alert=True)
    st = await style(chat_id)
    ma, mb = await m(chat_id, a), await m(chat_id, b, cb.from_user.first_name)
    if ans == "no":
        text = say(st, "marry_no", chat_id=chat_id, user=ma, target=mb)
    elif await db.partner(chat_id, a) or await db.partner(chat_id, b):
        text = say(st, "marry_taken", chat_id=chat_id, user=ma)
    else:
        await db.marry(chat_id, a, b)
        text = say(st, "marry_yes", chat_id=chat_id, user=ma, target=mb)
    try:
        await cb.message.edit_text(text)
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.message(Command("развод", "divorce", prefix="+/!"))
async def cmd_divorce(msg: Message):
    chat_id = msg.chat.id
    p = await db.partner(chat_id, msg.from_user.id)
    if not p:
        return await reply(msg, "no_partner")
    await db.divorce(chat_id, msg.from_user.id)
    await reply(msg, "divorce", user=await m(chat_id, msg.from_user.id, msg.from_user.first_name), target=await m(chat_id, p))


@router.message(Command("пара", "partner", prefix="+/!"))
async def cmd_partner(msg: Message):
    chat_id = msg.chat.id
    uid, name, _ = await resolve_target(msg)
    if not uid:
        uid, name = msg.from_user.id, msg.from_user.first_name
    p = await db.partner(chat_id, uid)
    if not p:
        return await reply(msg, "partner_none", user=await m(chat_id, uid, name))
    row = await db.marriage(chat_id, uid)
    days = (int(time.time()) - row["since"]) // 86400
    await reply(msg, "partner_show", user=await m(chat_id, uid, name), target=await m(chat_id, p), days=days)


@router.message(Command("браки", "marriages", "пары", prefix="+/!"))
async def cmd_marriages(msg: Message):
    chat_id = msg.chat.id
    rows = await db.marriages(chat_id)
    if not rows:
        return await reply(msg, "marriages_empty")
    lines = []
    for r in rows:
        days = (int(time.time()) - r["since"]) // 86400
        lines.append(f"💍 {await m(chat_id, r['a'])} + {await m(chat_id, r['b'])} — {days} дн.")
    await reply(msg, "marriages_list", list="\n".join(lines))


# ---------- +влюблен ----------
@router.message(Command("влюблен", "влюблён", "влюблена", "crush", prefix="+/!"))
async def cmd_crush(msg: Message):
    chat_id = msg.chat.id
    await db.touch_user(chat_id, msg.from_user)
    uid, name, _ = await resolve_target(msg)
    me = await m(chat_id, msg.from_user.id, msg.from_user.first_name)
    if not uid:
        t = await db.crush(chat_id, msg.from_user.id)
        if not t:
            return await reply(msg, "crush_none", user=me)
        return await reply(msg, "crush_show", user=me, target=await m(chat_id, t))
    if uid == msg.from_user.id:
        return await reply(msg, "crush_self", user=me)
    await db.set_crush(chat_id, msg.from_user.id, uid)
    target = await m(chat_id, uid, name)
    if await db.crush(chat_id, uid) == msg.from_user.id:
        return await reply(msg, "crush_mutual", user=me, target=target)
    await reply(msg, "crush_set", user=me, target=target)


@router.message(Command("разлюбил", "разлюбила", "uncrush", prefix="+/!"))
async def cmd_uncrush(msg: Message):
    await db.set_crush(msg.chat.id, msg.from_user.id, None)
    await reply(msg, "crush_removed", user=await m(msg.chat.id, msg.from_user.id, msg.from_user.first_name))


# ---------- +люблю сарру ----------
@router.message(Command("люблю", "обожаю", "love", prefix="+/!"))
async def cmd_love(msg: Message):
    chat_id = msg.chat.id
    args = " ".join(split_args(msg)).lower()
    me = await m(chat_id, msg.from_user.id, msg.from_user.first_name)
    if not any(w in args for w in ("сар", "сарр", "бот", "тебя")):
        # «+люблю @кого-то» — считаем как влюблённость
        uid, name, _ = await resolve_target(msg)
        if uid and uid != msg.from_user.id:
            await db.set_crush(chat_id, msg.from_user.id, uid)
            return await reply(msg, "crush_set", user=me, target=await m(chat_id, uid, name))
        return await reply(msg, "love_usage")
    await db.touch_user(chat_id, msg.from_user)
    n = await db.love_sarra(chat_id, msg.from_user.id)
    await db.bump_mood(chat_id, 15)
    await db.add_mass(chat_id, msg.from_user.id, 3)
    await reply(msg, "love_sarra", user=me, n=n)
    if await db.give_achievement(chat_id, msg.from_user.id, "lover"):
        await reply(msg, "achievement", user=me, ach=ACHIEVEMENTS["lover"])
