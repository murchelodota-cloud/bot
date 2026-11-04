import re
import time
import html
from aiogram import Bot
from aiogram.types import Message, ChatPermissions
from db import db
from phrases import say
from config import OWNER_IDS

MUTE_PERMS = ChatPermissions(can_send_messages=False)
FULL_PERMS = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True,
    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_invite_users=True,
)
NEWBIE_PERMS = ChatPermissions(can_send_messages=True, can_add_web_page_previews=False)


def mention(user_id: int, name: str | None) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name or "кто-то")}</a>'


def level(mass: float) -> int:
    # уровень растёт медленно: 0,20,50,100,180,300,460,660...
    lvl, need = 0, 20
    while mass >= need:
        mass -= need
        lvl += 1
        need = int(need * 1.4)
    return lvl


def warn_weight(mass: float) -> float:
    """Пред новичку весит больше, старожилу — меньше."""
    if mass < 50:
        return 1.5
    if mass < 200:
        return 1.0
    if mass < 500:
        return 0.8
    return 0.6


def vote_weight(mass: float) -> float:
    return round(1 + min(mass, 1000) / 250, 2)  # 1.0 ... 5.0


def parse_duration(text: str | None, default_hours: float = 1) -> int:
    """'30m', '2h', '3d' -> секунды"""
    if not text:
        return int(default_hours * 3600)
    m = re.fullmatch(r"(\d+)\s*([mhd]?)", text.strip().lower())
    if not m:
        return int(default_hours * 3600)
    n, unit = int(m.group(1)), m.group(2) or "h"
    return n * {"m": 60, "h": 3600, "d": 86400}[unit]


def human_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} д"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч"
    return f"{seconds // 60} мин"


# ---------- ранги (как в Ирисе) ----------
RANK_NAMES = {0: "участник", 1: "модератор", 2: "младший админ", 3: "старший админ", 4: "владелец"}
RANK_ALIASES = {
    "0": 0, "участник": 0, "снять": 0, "none": 0,
    "1": 1, "модер": 1, "модератор": 1, "mod": 1,
    "2": 2, "мл": 2, "младший": 2, "младшийадмин": 2, "junior": 2,
    "3": 3, "ст": 3, "старший": 3, "старшийадмин": 3, "senior": 3, "админ": 3, "admin": 3,
    "4": 4, "владелец": 4, "owner": 4,
}
# минимальный ранг для действия
NEED = {
    "warn": 1, "unwarn": 1, "mute": 1, "unmute": 1,
    "kick": 2, "court": 2, "tag": 2, "report_action": 2,
    "ban": 3, "unban": 3, "settings": 3, "style": 3, "phrase": 3, "digest": 3,
    "rank": 4,
}
MOD_MUTE_MAX = 2 * 3600   # модератор не может мутить дольше 2 часов


async def rank_of(bot: Bot, chat_id: int, user_id: int) -> int:
    """Ранг = максимум из выданного командой и статуса в Telegram (создатель=4, админ=3)."""
    if user_id in OWNER_IDS:
        return 4
    r = await db.rank(chat_id, user_id)
    if r >= 4:
        return r
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        if m.status == "creator":
            return 4
        if m.status == "administrator":
            return max(r, 3)
    except Exception:
        pass
    return r


async def has_rank(bot: Bot, chat_id: int, user_id: int, action: str) -> bool:
    return await rank_of(bot, chat_id, user_id) >= NEED[action]


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Любой ранг от модератора и выше (для антиспама и т.п.)."""
    return await rank_of(bot, chat_id, user_id) >= 1


async def deny(msg: Message, bot: Bot, action: str) -> bool:
    """True, если прав не хватает (и уже ответил)."""
    have = await rank_of(bot, msg.chat.id, msg.from_user.id)
    if have >= NEED[action]:
        return False
    await reply(msg, "no_rank", need=rank_name(NEED[action]), have=rank_name(have))
    return True


def rank_name(r: int) -> str:
    return RANK_NAMES.get(r, str(r))


async def style(chat_id: int) -> str:
    return (await db.settings(chat_id))["style"]


async def reply(msg: Message, key: str, **kw) -> Message:
    """Ответить фразой в стиле чата; при низком настроении Саррочка вздыхает."""
    st = await style(msg.chat.id)
    text = say(st, key, chat_id=msg.chat.id, **kw)
    if st == "pickme" and key not in ("mood", "me", "help", "settings"):
        if await db.mood(msg.chat.id) < 30:
            text += "\n" + say(st, "sigh", chat_id=msg.chat.id)
    sent = await msg.answer(text, disable_web_page_preview=True)
    await db.remember_bot_message(msg.chat.id, sent.message_id)
    return sent


def split_args(msg: Message) -> list[str]:
    return (msg.text or "").split()[1:]


async def resolve_target(msg: Message) -> tuple[int | None, str | None, list[str]]:
    """
    Возвращает (user_id, name, остальные аргументы).
    Цель: reply на сообщение, @username (из базы) или числовой id.
    """
    args = split_args(msg)
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, u.first_name, args
    if args:
        first = args[0]
        if first.startswith("@"):
            row = await db.find_user_by_username(msg.chat.id, first)
            if row:
                return row["user_id"], row["first_name"], args[1:]
        if first.isdigit():
            row = await db.user(msg.chat.id, int(first))
            return int(first), (row["first_name"] if row else first), args[1:]
    return None, None, args


async def check_achievements(msg: Message, user_row, msgs: int):
    chat_id, uid = msg.chat.id, user_row["user_id"]
    got = []
    if msgs == 100 and await db.give_achievement(chat_id, uid, "first100"):
        got.append("first100")
    hour = time.localtime().tm_hour
    if 3 <= hour < 5 and await db.give_achievement(chat_id, uid, "night_owl"):
        got.append("night_owl")
    if user_row["mass"] >= 500 and await db.give_achievement(chat_id, uid, "heavy"):
        got.append("heavy")
    from phrases import ACHIEVEMENTS
    for key in got:
        await db.log_event(chat_id, "ach", f"🏅 {mention(uid, user_row['first_name'])} — ачивка «{ACHIEVEMENTS[key]}»")
        await reply(msg, "achievement", user=mention(uid, user_row["first_name"]), ach=ACHIEVEMENTS[key])
