"""Видео по ссылке: !видео <url> (Instagram, TikTok, YouTube, VK и др.) — и автоскачивание reels/tiktok из обычных сообщений."""
import asyncio
import html
import logging
import os
import re
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.exceptions import TelegramBadRequest
from db import db
from utils import reply, mention, split_args
from config import MUSIC_DIR

router = Router()
URL_RE = re.compile(r"https?://\S+")
AUTO_RE = re.compile(r"https?://(?:www\.)?(?:instagram\.com/(?:reel|reels|p)/|vm\.tiktok\.com/|(?:www\.)?tiktok\.com/@[\w.]+/video/)\S+", re.I)
MAX_MB = 48  # лимит Telegram для ботов — 50 МБ
VIDEO_TIMEOUT = 180
_lock = asyncio.Lock()


def _download(url: str) -> dict:
    import yt_dlp
    out_dir = os.path.join(MUSIC_DIR, "video")
    os.makedirs(out_dir, exist_ok=True)
    opts = {
        "format": f"best[ext=mp4][filesize<{MAX_MB}M]/bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4]/b",
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "socket_timeout": 20,
        "retries": 2,
        "max_filesize": MAX_MB * 1024 * 1024,
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = next((e for e in info["entries"] if e), None) or {}
        path = None
        for ext in ("mp4", "mkv", "webm", "mov"):
            cand = os.path.join(out_dir, f"{info.get('id')}.{ext}")
            if os.path.exists(cand):
                path = cand
                break
        if not path:
            return {"error": "notfound"}
        if os.path.getsize(path) > MAX_MB * 1024 * 1024:
            os.remove(path)
            return {"error": "toobig"}
        return {"path": path, "title": (info.get("title") or "")[:200], "duration": int(info.get("duration") or 0),
                "width": info.get("width"), "height": info.get("height")}


async def fetch_and_send(msg: Message, url: str, quiet: bool = False):
    if _lock.locked():
        return await reply(msg, "video_busy")
    async with _lock:
        status = None if quiet else await reply(msg, "video_searching")
        res = {"error": "fail"}
        try:
            res = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, _download, url), timeout=VIDEO_TIMEOUT)
        except asyncio.TimeoutError:
            logging.warning("video %s: timeout", url)
        except Exception as e:
            logging.warning("video %s: %s", url, str(e)[:300])
        finally:
            if status:
                try:
                    await status.delete()
                except TelegramBadRequest:
                    pass
        if "error" in res:
            if quiet:
                return
            return await reply(msg, {"toobig": "video_toobig"}.get(res["error"], "video_fail"), mb=MAX_MB)
        try:
            from phrases import say
            from utils import style
            caption = say(await style(msg.chat.id), "video_done", chat_id=msg.chat.id,
                          user=mention(msg.from_user.id, msg.from_user.first_name), title=html.escape(res["title"]))
            await msg.answer_video(FSInputFile(res["path"]), caption=caption[:1024], duration=res["duration"] or None,
                                   width=res["width"], height=res["height"], supports_streaming=True)
        finally:
            try:
                os.remove(res["path"])
            except OSError:
                pass


@router.message(Command("видео", "video", "скачай", "скачать", "dl", prefix="!+/"))
async def cmd_video(msg: Message):
    text = " ".join(split_args(msg))
    if msg.reply_to_message and not URL_RE.search(text):
        text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    m = URL_RE.search(text)
    if not m:
        return await reply(msg, "video_usage")
    await fetch_and_send(msg, m.group(0))


@router.message(F.text.regexp(AUTO_RE).as_("m"))
async def auto_video(msg: Message, m: re.Match):
    """Ссылка на reels/tiktok в обычном сообщении — качаем сами (отключается /settings video_auto 0)."""
    s = await db.settings(msg.chat.id)
    if not s.get("video_auto", 1):
        return
    await fetch_and_send(msg, m.group(0), quiet=True)
