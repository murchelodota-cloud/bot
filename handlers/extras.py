"""Приятные мелочи: /remind, стикер-теги /tag, вопрос дня."""
import time
import html
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import say, QUESTIONS
from utils import (reply, is_admin, mention, split_args, parse_duration,
                   human_duration, style)

router = Router()


# ---------- напоминания ----------
@router.message(Command("remind"))
async def cmd_remind(msg: Message):
    args = split_args(msg)
    if len(args) < 2:
        return await reply(msg, "remind_usage")
    secs = parse_duration(args[0], default_hours=0)
    if secs <= 0:
        return await reply(msg, "remind_usage")
    text = " ".join(args[1:])[:300]
    await db.add_reminder(msg.chat.id, msg.from_user.id, text, int(time.time()) + secs)
    await reply(msg, "remind_set", time=human_duration(secs))


async def fire_due_reminders(bot: Bot):
    """Вызывается из фонового цикла: шлёт созревшие напоминания."""
    for r in await db.due_reminders(int(time.time())):
        try:
            st = await style(r["chat_id"])
            u = await db.user(r["chat_id"], r["user_id"])
            name = u["first_name"] if u else None
            sent = await bot.send_message(
                r["chat_id"],
                say(st, "remind_fired", chat_id=r["chat_id"], user=mention(r["user_id"], name), text=html.escape(r["text"])),
            )
            await db.remember_bot_message(r["chat_id"], sent.message_id)
        except TelegramBadRequest:
            pass
        await db.delete_reminder(r["id"])


# ---------- стикер-теги ----------
@router.message(Command("tag"))
async def cmd_tag(msg: Message, bot: Bot):
    args = split_args(msg)
    if not args:
        return await reply(msg, "tag_usage")
    action = args[0].lower()
    chat_id = msg.chat.id

    if action == "add":
        if not await is_admin(bot, chat_id, msg.from_user.id):
            return await reply(msg, "not_admin")
        name = args[1].lower().lstrip("#") if len(args) > 1 else None
        sticker = msg.reply_to_message and msg.reply_to_message.sticker
        if not name or not sticker:
            return await reply(msg, "tag_usage")
        await db.set_tag(chat_id, name, sticker.file_id)
        return await reply(msg, "tag_saved", name=html.escape(name))

    if action == "del":
        if not await is_admin(bot, chat_id, msg.from_user.id):
            return await reply(msg, "not_admin")
        name = args[1].lower().lstrip("#") if len(args) > 1 else None
        if not name:
            return await reply(msg, "tag_usage")
        if await db.del_tag(chat_id, name):
            return await reply(msg, "tag_deleted", name=html.escape(name))
        return await reply(msg, "tag_notfound")

    # /tag имя — прислать стикер
    name = action.lstrip("#")
    file_id = await db.get_tag(chat_id, name)
    if not file_id:
        return await reply(msg, "tag_notfound")
    await bot.send_sticker(chat_id, file_id, reply_to_message_id=msg.message_id)


@router.message(Command("tags"))
async def cmd_tags(msg: Message):
    names = await db.list_tags(msg.chat.id)
    if not names:
        return await reply(msg, "tags_empty")
    listing = ", ".join(names)
    await reply(msg, "tags_list", list=html.escape(listing))


# ---------- вопрос дня ----------
async def send_qotd(bot: Bot, chat_id: int):
    st = await style(chat_id)
    day = time.strftime("%Y-%m-%d")
    idx = (int(time.time()) // 86400 + abs(chat_id)) % len(QUESTIONS)
    sent = await bot.send_message(chat_id, say(st, "qotd", chat_id=chat_id, q=QUESTIONS[idx]))
    await db.remember_bot_message(chat_id, sent.message_id)
    await db.mark_qotd(chat_id, day)


async def qotd_due(bot: Bot, chat_id: int) -> bool:
    """Вопрос дня — не раньше qotd_hour по локальному времени сервера (см. TZ в docker-compose)."""
    s = await db.settings(chat_id)
    if not s["qotd"] or time.localtime().tm_hour < int(s["qotd_hour"]):
        return False
    return await db.qotd_due(chat_id, time.strftime("%Y-%m-%d"))
