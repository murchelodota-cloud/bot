"""Видео по ссылке: !видео <url> (Instagram, TikTok, YouTube, VK и др.) — и автоскачивание reels/tiktok из обычных сообщений."""
import asyncio
import html
import logging
import os
import re
import subprocess
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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
_cache: dict[str, dict] = {}   # file_id видео -> {path?, title}; путь живёт пока файл не удалён
KEEP_SECONDS = 1800            # сколько держим скачанный файл для кнопок


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
        from phrases import say
        from utils import style
        caption = say(await style(msg.chat.id), "video_done", chat_id=msg.chat.id,
                      user=mention(msg.from_user.id, msg.from_user.first_name), title=html.escape(res["title"]))
        track = await recognize_track(res["path"])
        if track:
            caption += f"\n\n🎧 распознан трек: {html.escape(track)}"
            res["track"] = track
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⭕ кружок", callback_data="vid:circle"),
            InlineKeyboardButton(text="🎵 аудио", callback_data="vid:audio"),
        ]])
        try:
            sent = await msg.answer_video(FSInputFile(res["path"]), caption=caption[:1024], duration=res["duration"] or None,
                                          width=res["width"], height=res["height"], supports_streaming=True, reply_markup=kb)
        except Exception:
            try:
                os.remove(res["path"])
            except OSError:
                pass
            raise
        key = f"{msg.chat.id}:{sent.message_id}"
        _cache[key] = {"path": res["path"], "title": res.get("track") or res["title"], "duration": res["duration"]}
        await db.remember_bot_message(msg.chat.id, sent.message_id)
        asyncio.get_running_loop().call_later(KEEP_SECONDS, _forget, key)


async def recognize_track(path: str) -> str | None:
    """Shazam без ключей (shazamio). Ошибки глотаем — это бонус, а не обязанность."""
    try:
        from shazamio import Shazam
    except ImportError:
        return None
    try:
        snippet = path.rsplit(".", 1)[0] + ".snip.mp3"
        await asyncio.get_running_loop().run_in_executor(None, lambda: subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "3", "-t", "15", "-i", path, "-vn", "-ac", "1", "-ar", "44100", snippet],
            check=True, timeout=60))
        out = await asyncio.wait_for(Shazam().recognize(snippet), timeout=20)
        try:
            os.remove(snippet)
        except OSError:
            pass
        tr = out.get("track") or {}
        if tr.get("title"):
            return f"{tr.get('subtitle', '')} — {tr['title']}".strip(" —")
    except Exception as e:
        logging.info("shazam: %s", str(e)[:120])
    return None


def _forget(key: str):
    d = _cache.pop(key, None)
    if d:
        try:
            os.remove(d["path"])
        except OSError:
            pass


def _to_circle(src: str) -> str:
    out = src.rsplit(".", 1)[0] + ".circle.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", "60",
                    "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=640:640", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out], check=True, timeout=240)
    return out


def _to_audio(src: str) -> str:
    out = src.rsplit(".", 1)[0] + ".audio.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-ar", "44100", "-ac", "2",
                    "-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3", out], check=True, timeout=240)
    return out


@router.callback_query(F.data.startswith("vid:"))
async def on_video_button(cb: CallbackQuery, bot: Bot):
    what = cb.data.split(":")[1]
    key = f"{cb.message.chat.id}:{cb.message.message_id}"
    d = _cache.get(key)
    if not d or not os.path.exists(d["path"]):
        return await cb.answer("видео уже не держу — скинь ссылку заново, сделаю.", show_alert=True)
    await cb.answer("делаю…")
    loop = asyncio.get_running_loop()
    try:
        if what == "circle":
            out = await loop.run_in_executor(None, _to_circle, d["path"])
            sent = await cb.message.reply_video_note(FSInputFile(out), length=640, duration=min(60, d["duration"] or 60))
        else:
            out = await loop.run_in_executor(None, _to_audio, d["path"])
            title, performer = (d["title"] or "аудио"), "из видео"
            if " — " in title:
                performer, title = title.split(" — ", 1)
            sent = await cb.message.reply_audio(FSInputFile(out), title=title[:64], performer=performer[:64])
        await db.remember_bot_message(cb.message.chat.id, sent.message_id)
        try:
            os.remove(out)
        except OSError:
            pass
    except Exception as e:
        logging.warning("video button %s: %s", what, str(e)[:200])
        try:
            await cb.message.reply("не получилось. ffmpeg обиделся. или я. скорее ffmpeg.")
        except Exception:
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
