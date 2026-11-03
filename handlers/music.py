"""Музыка: /play — найти трек (YouTube / SoundCloud / ссылка) и прислать в чат аудиофайлом."""
import asyncio
import html
import logging
import os
import re
from collections import defaultdict
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.exceptions import TelegramBadRequest
from db import db
from utils import reply, mention, split_args
from config import MUSIC_MAX_MINUTES, MUSIC_DIR

router = Router()
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
URL_RE = re.compile(r"https?://\S+")


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


@router.message(Command("play", "p"))
async def cmd_play(msg: Message, bot: Bot):
    query = " ".join(split_args(msg)).strip()
    if not query:
        return await reply(msg, "play_usage")
    lock = _locks[msg.chat.id]
    if lock.locked():
        return await reply(msg, "play_busy")
    async with lock:
        status = await reply(msg, "play_searching", q=html.escape(query[:80]))
        try:
            res = await asyncio.get_running_loop().run_in_executor(None, _download, query)
        except Exception as e:
            logging.warning("play %r: %s", query, e)
            res = {"error": "fail"}
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
