"""Музыка: /play — найти трек (YouTube / SoundCloud / ссылка) и прислать в чат аудиофайлом."""
import asyncio
import html
import logging
import os
import re
from collections import defaultdict
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.exceptions import TelegramBadRequest
from db import db
from utils import reply, mention, split_args
from config import MUSIC_MAX_MINUTES, MUSIC_DIR

router = Router()
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
URL_RE = re.compile(r"https?://\S+")
# «включи …», «поставь …», «саррочка, включи …», «хочу послушать …», «найди песню …»
NATURAL_RE = re.compile(
    r"^(?:саррочка|сарочка|сара|бот)?[,!\s]*"
    r"(?:включи|поставь|врубай|врубни|заиграй|найди|найти|поищи|скинь|кинь|дай|хочу послушать|"
    r"трек|песню|песня|музыку|музыка)"
    r"(?:\s+(?:мне|нам|пожалуйста|плиз|трек|песню|песня|музыку))*"
    r"[,:!\s]+(.{2,120})$",
    re.I | re.S,
)


def _ydl_opts(outtmpl: str) -> dict:
    return {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
    }


def _download(query: str) -> dict:
    """Синхронно: ищет и качает. Возвращает dict с path/title/artist/duration/id или ошибкой."""
    import yt_dlp
    os.makedirs(MUSIC_DIR, exist_ok=True)
    outtmpl = os.path.join(MUSIC_DIR, "%(id)s.%(ext)s")
    with yt_dlp.YoutubeDL(_ydl_opts(outtmpl)) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            entries = [e for e in info["entries"] if e]
            if not entries:
                return {"error": "notfound"}
            info = entries[0]
        duration = int(info.get("duration") or 0)
        if duration > MUSIC_MAX_MINUTES * 60:
            return {"error": "toolong", "title": info.get("title"), "duration": duration}
        info = ydl.extract_info(info.get("webpage_url") or info["url"], download=True)
        path = os.path.join(MUSIC_DIR, f"{info['id']}.mp3")
        if not os.path.exists(path):
            return {"error": "notfound"}
        return {
            "path": path, "id": info["id"],
            "title": info.get("track") or info.get("title") or "трек",
            "artist": info.get("artist") or info.get("uploader") or "",
            "duration": duration,
        }


@router.message(Command("play", "p", "трек", "плей", prefix="/!"))
async def cmd_play(msg: Message, bot: Bot):
    await play(msg, " ".join(split_args(msg)).strip())


@router.message(F.text.regexp(NATURAL_RE).as_("m"))
async def natural_play(msg: Message, m: re.Match):
    """«включи <трек>» без команды."""
    s = await db.settings(msg.chat.id)
    if not s.get("play_natural", 1):
        return
    await play(msg, m.group(1).strip(" .!?"))


async def play(msg: Message, query: str):
    if not query:
        return await reply(msg, "play_usage")
    lock = _locks[msg.chat.id]
    if lock.locked():
        return await reply(msg, "play_busy")
    async with lock:
        status = await reply(msg, "play_searching", q=html.escape(query[:80]))
        loop = asyncio.get_running_loop()
        res = {"error": "fail"}
        # сначала как есть (ссылка или YouTube), если YouTube отказал — SoundCloud
        attempts = [query] if URL_RE.search(query) else [query, f"scsearch1:{query}"]
        for q in attempts:
            try:
                res = await loop.run_in_executor(None, _download, q)
            except Exception as e:
                logging.warning("play %r: %s", q, str(e)[:300])
                res = {"error": "fail"}
            if "error" not in res or res["error"] == "toolong":
                break
        try:
            await status.delete()
        except TelegramBadRequest:
            pass
        if "error" in res:
            key = {"notfound": "play_notfound", "toolong": "play_toolong"}.get(res["error"], "play_fail")
            return await reply(msg, key, limit=MUSIC_MAX_MINUTES, title=html.escape(res.get("title") or ""))
        try:
            caption = (await _caption(msg)).format(user=mention(msg.from_user.id, msg.from_user.first_name))
            await msg.answer_audio(
                FSInputFile(res["path"]), title=res["title"][:64], performer=res["artist"][:64] or None,
                duration=res["duration"], caption=caption,
            )
        finally:
            try:
                os.remove(res["path"])
            except OSError:
                pass


async def _caption(msg: Message) -> str:
    from phrases import say
    from utils import style
    return say(await style(msg.chat.id), "play_done", chat_id=msg.chat.id)
