"""Рубрики дня: подкаблучник (упоминает девочек), ПЗР (пишет «бывшая»), пикми (больше всех эмодзи).
Считается по каждому сообщению, итоги — вечером вместе с королём дня; !номинации — текущие лидеры."""
import re
import time
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from db import db
from phrases import say, ACHIEVEMENTS
from utils import reply, mention, style

router = Router()
_ex_cool: dict[tuple[int, int], float] = {}   # антиспам реакции на «бывшая»

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF❤❣♥☺☹\U0001F000-\U0001F2FF]"
)
EX_RE = re.compile(r"\bбывш(?:ая|ей|ую|ие|их|им|ими|его|ий|ему|ем|ой|ею)?\b", re.I)
GIRLS_RE = re.compile(r"\b(девочк[аиеуой]+|девушк[аиеуой]+|девчонк[аиеуой]+|девчат[аы]|подруг[аиеуой]+|красотк[аиеуой]+|малышк[аиеуой]+|жен[аыеу]|жёны|тян(?:очк[аи])?)\b", re.I)

KINDS = {
    "henpecked": ("🫦 подкаблучник дня", "nom_henpecked"),
    "ex": ("💔 ПЗР дня", "nom_ex"),
    "pickme": ("💅 пикми дня", "nom_pickme"),
}


def today() -> str:
    return time.strftime("%Y-%m-%d")


async def nm(chat_id: int, uid: int) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None))


async def analyze(msg: Message):
    """Вызывается из middleware для каждого сообщения."""
    text = msg.text or msg.caption or ""
    if not text or text.startswith(("/", "!", "+", "-")):
        return
    chat_id, uid = msg.chat.id, msg.from_user.id
    day = today()
    # пикми: эмодзи
    n_emoji = len(EMOJI_RE.findall(text))
    if n_emoji:
        await db.nom_add(chat_id, day, uid, "pickme", n_emoji)
    # ПЗР: «бывшая» во всех падежах
    n_ex = len(EX_RE.findall(text))
    if n_ex:
        await db.nom_add(chat_id, day, uid, "ex", n_ex)
        s = await db.settings(chat_id)
        if s.get("ex_react", 1) and time.time() - _ex_cool.get((chat_id, uid), 0) >= int(s.get("ex_react_cooldown", 0)):
            _ex_cool[(chat_id, uid)] = time.time()
            total = sum(r["n"] for r in await db.nom_top(chat_id, day, "ex", 50) if r["user_id"] == uid)
            await reply(msg, "ex_react", user=await nm(chat_id, uid), n=total)
    # подкаблучник: слова про девочек + ответы/упоминания девушек чата
    n_girls = len(GIRLS_RE.findall(text))
    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id != uid:
        t = await db.user(chat_id, msg.reply_to_message.from_user.id)
        if t and t["gender"] == "ж":
            n_girls += 1
    for ent in (msg.entities or []):
        if ent.type == "text_mention" and ent.user:
            t = await db.user(chat_id, ent.user.id)
            if t and t["gender"] == "ж":
                n_girls += 1
        elif ent.type == "mention":
            uname = text[ent.offset + 1: ent.offset + ent.length]
            t = await db.find_user_by_username(chat_id, uname)
            if t and t["gender"] == "ж":
                n_girls += 1
    if n_girls:
        await db.nom_add(chat_id, day, uid, "henpecked", n_girls)


async def standings_text(chat_id: int, day: str) -> str:
    parts = []
    for kind, (title, _) in KINDS.items():
        rows = await db.nom_top(chat_id, day, kind, 3)
        if rows:
            parts.append(f"{title}:\n" + "\n".join([f"  {i + 1}. {await nm(chat_id, r['user_id'])} — {r['n']}" for i, r in enumerate(rows)]))
    return "\n\n".join(parts)


@router.message(Command("номинации", "рубрики", "nominations", "подкаблучник", "пзр", "пикми", prefix="!+/"))
async def cmd_nominations(msg: Message):
    text = await standings_text(msg.chat.id, today())
    if not text:
        return await reply(msg, "nom_empty")
    await reply(msg, "nom_standings", list=text)


async def nominations_close(bot: Bot, chat_id: int, force: bool = False):
    """Итоги дня — из часового цикла после nom_hour (по умолчанию 23)."""
    s = await db.settings(chat_id)
    if not s.get("nominations", 1):
        return
    day = today()
    if not force and (time.localtime().tm_hour < int(s.get("nom_hour", 23)) or s.get("nom_done") == day):
        return
    await db.set_setting(chat_id, "nom_done", day)
    st = await style(chat_id)
    bonus = int(s.get("nom_bonus", 10))
    out = []
    for kind, (title, key) in KINDS.items():
        rows = await db.nom_top(chat_id, day, kind, 1)
        if not rows:
            continue
        uid, n = rows[0]["user_id"], rows[0]["n"]
        await db.give_coin(chat_id, 0, uid, title, bonus)
        await db.give_achievement(chat_id, uid, "nom_" + kind)
        await db.log_event(chat_id, "nom", f"{title} — {await nm(chat_id, uid)} ({n})")
        out.append(say(st, key, chat_id=chat_id, user=await nm(chat_id, uid), n=n, bonus=bonus))
    if out:
        sent = await bot.send_message(chat_id, say(st, "nom_header", chat_id=chat_id) + "\n\n" + "\n\n".join(out))
        await db.remember_bot_message(chat_id, sent.message_id)


@router.message(Command("итогидня", "nomclose", prefix="!+/"))
async def cmd_nom_close(msg: Message, bot: Bot):
    from utils import deny
    if await deny(msg, bot, "kick"):
        return
    await db.set_setting(msg.chat.id, "nom_done", "")
    await nominations_close(bot, msg.chat.id, force=True)
