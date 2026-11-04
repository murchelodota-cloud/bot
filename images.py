"""Картинки по запросу: !картинка запрос · «найди картинку …» / «покажи …» в обычном сообщении. Поиск — DuckDuckGo (без ключей)."""
import asyncio
import html
import logging
import random
import re
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, URLInputFile
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import say
from utils import reply, mention, split_args, style

router = Router()
NATURAL_RE = re.compile(
    r"^(?:саррочка|сарочка|сара|бот)?[,!\s]*"
    r"(?:найди|покажи|скинь|кинь|дай|хочу)\s+(?:мне\s+|нам\s+)?(?:картинку|картинки|фото|фотку|пикчу|пикча|изображение|картинка)"
    r"[,:!\s]+(.{2,100})$",
    re.I | re.S,
)
_lock = asyncio.Lock()


def _search(query: str, n: int = 12) -> list[str]:
    from ddgs import DDGS
    res = DDGS().images(query, max_results=n, safesearch="moderate")
    return [r.get("image") for r in res if r.get("image")]


async def send_image(msg: Message, query: str):
    chat_id = msg.chat.id
    if _lock.locked():
        return await reply(msg, "img_busy")
    async with _lock:
        try:
            urls = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, _search, query), timeout=25)
        except Exception as e:
            logging.warning("image %r: %s", query, str(e)[:200])
            urls = []
    if not urls:
        return await reply(msg, "img_notfound", q=html.escape(query))
    caption = say(await style(chat_id), "img_done", chat_id=chat_id, user=mention(msg.from_user.id, msg.from_user.first_name), q=html.escape(query))
    random.shuffle(urls)
    for url in urls[:6]:
        try:
            sent = await msg.answer_photo(URLInputFile(url, timeout=20), caption=caption[:1024])
            await db.remember_bot_message(chat_id, sent.message_id)
            return
        except (TelegramBadRequest, Exception) as e:  # битая ссылка / слишком большая — пробуем следующую
            logging.info("image skip %s: %s", url[:60], str(e)[:100])
    await reply(msg, "img_fail", q=html.escape(query))


@router.message(Command("картинка", "картинку", "фото", "пикча", "img", "image", "pic", prefix="!+/"))
async def cmd_image(msg: Message):
    q = " ".join(split_args(msg)).strip()
    if not q:
        return await reply(msg, "img_usage")
    await send_image(msg, q)


@router.message(F.text.regexp(NATURAL_RE).as_("m"))
async def natural_image(msg: Message, m: re.Match):
    s = await db.settings(msg.chat.id)
    if not s.get("img_natural", 1):
        return
    await send_image(msg, m.group(1).strip(" .!?"))
