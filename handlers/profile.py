"""Масса, карточка, топ, реакции."""
import html
from typing import Any, Callable, Awaitable
from aiogram import Router, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message, MessageReactionUpdated, TelegramObject
from db import db
from phrases import say, ACHIEVEMENTS
from config import MASS_PER_MESSAGE, MASS_DAILY_CAP, MASS_PER_REACTION, MASS_PER_BOT_REACTION
from utils import reply, mention, level, resolve_target, check_achievements, style

router = Router()


class MassMiddleware(BaseMiddleware):
    """Считает сообщения и начисляет массу до того, как сработает любой хендлер."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        if isinstance(event, Message) and event.from_user and event.chat.type in ("group", "supergroup") \
                and not event.from_user.is_bot:
            row = await db.touch_user(event.chat.id, event.from_user)
            await db.add_mass(event.chat.id, event.from_user.id, MASS_PER_MESSAGE, daily_cap=MASS_DAILY_CAP)
            msgs = await db.inc_msgs(event.chat.id, event.from_user.id)
            await check_achievements(event, row, msgs)
        return await handler(event, data)


@router.message(Command("me"))
async def cmd_me(msg: Message):
    uid, name, _ = await resolve_target(msg)
    if not uid:
        uid, name = msg.from_user.id, msg.from_user.first_name
    row = await db.user(msg.chat.id, uid)
    if not row:
        return await msg.answer("я его ещё не видела. или он молчит. как и я, иногда.")
    ach = ", ".join(ACHIEVEMENTS.get(a, a) for a in await db.achievements(msg.chat.id, uid)) or "пока нет"
    await reply(msg, "me", user=mention(uid, name), mass=int(row["mass"]), level=level(row["mass"]),
                rank=await db.rank(msg.chat.id, uid), msgs=row["msgs"],
                warns=await db.active_warns(msg.chat.id, uid), ach=html.escape(ach))


@router.message(Command("top"))
async def cmd_top(msg: Message):
    rows = await db.top_users(msg.chat.id, "mass", 10)
    lines = [f"{i + 1}. {mention(r['user_id'], r['first_name'])} — {int(r['mass'])} (ур. {level(r['mass'])})"
             for i, r in enumerate(rows)]
    await msg.answer("⚖️ самые массивные:\n" + "\n".join(lines))


@router.message_reaction()
async def on_reaction(event: MessageReactionUpdated):
    if not event.user or event.user.is_bot or not event.new_reaction:
        return
    chat_id = event.chat.id
    if await db.is_bot_message(chat_id, event.message_id):
        row = await db.touch_user(chat_id, event.user)
        await db.add_mass(chat_id, event.user.id, MASS_PER_BOT_REACTION)
        await db.bump_mood(chat_id, 5)
        if await style(chat_id) == "pickme":
            sent = await event.bot.send_message(
                chat_id, say("pickme", "bot_reaction", user=mention(event.user.id, event.user.first_name)))
            await db.remember_bot_message(chat_id, sent.message_id)
        return
    # реакция на чужое сообщение: массу получает автор — но Bot API не говорит, кто автор.
    # Поэтому плюсуем тому, кто ставит (социальная активность), меньше.
    await db.touch_user(chat_id, event.user)
    await db.add_mass(chat_id, event.user.id, MASS_PER_REACTION / 3)
