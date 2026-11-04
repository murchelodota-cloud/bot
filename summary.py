"""Сводки: !сводка1 (час), !сводка24 (сутки), !сводка7 (неделя) — о чём говорили + что произошло.
С LLM_API_KEY в .env — связный пересказ в стиле Саррочки; без ключа — темы по ключевым словам."""
import asyncio
import html
import json
import logging
import re
import time
from collections import Counter, defaultdict
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from db import db
from phrases import say
from utils import reply, mention, split_args, style
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

router = Router()
WORD_RE = re.compile(r"[а-яёa-z]{4,}", re.I)
STOP = set("""
это этот эта эти того тоже тока тогда только потом просто очень когда если либо будет была были было есть надо нужно можно
нельзя который которая которые чтобы потому зачем почему здесь там тут где куда откуда сейчас вчера сегодня завтра ещё еще
уже всех всем всё все вот ведь даже хотя хоть пока типа короче ладно вообще конечно наверное может быть кажется думаю
знаю знаешь понял поняла привет пока спасибо пожалуйста давай давайте хорошо норм окей ок ага угу нет да нету
меня тебя себя него неё нее нас вас них мне тебе себе ему ней нам вам ним мной тобой собой ими
твой твоя твои твоё мой моя мои моё наш наша наши наше ваш ваша ваши ваше свой своя свои своё его её их
что как так или для при без про под над после перед через между один одна одно одни два две три
какой какая какие какое такой такая такие такое этом этой этих этим этими
сама само сами будут буду будешь будем будете бывает блин капец
""".split())


async def nm(chat_id: int, uid: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None) or fallback)


def _name(r) -> str:
    return r["nick"] or r["first_name"] or "кто-то"


async def keyword_digest(chat_id: int, msgs: list) -> str:
    """Темы по словам, самые обсуждаемые сообщения, пик, диалоги, ссылки/медиа."""
    if not msgs:
        return "тишина. ни одного сообщения. я одна тут дежурила."
    words = Counter()
    by_id = {}
    reply_count = Counter()
    pairs = Counter()
    hours = Counter()
    links = media = 0
    for r in msgs:
        text = r["text"] or ""
        by_id[r["msg_id"]] = r
        hours[time.localtime(r["ts"]).tm_hour] += 1
        if r["reply_to"]:
            reply_count[r["reply_to"]] += 1
            src = by_id.get(r["reply_to"])
            if src and src["user_id"] != r["user_id"]:
                pairs[tuple(sorted((src["user_id"], r["user_id"])))] += 1
        if "http" in text or "t.me/" in text:
            links += 1
        if r["has_media"]:
            media += 1
        for w in WORD_RE.findall(text.lower()):
            if w not in STOP and not w.startswith(("http", "www")):
                words[w] += 1
    out = []
    top_words = [w for w, n in words.most_common(10) if n >= 2]
    if top_words:
        out.append("🗣 о чём говорили: " + ", ".join(top_words))
    hot = [(mid, n) for mid, n in reply_count.most_common(3) if n >= 2 and mid in by_id]
    if hot:
        lines = []
        for mid, n in hot:
            r = by_id[mid]
            snippet = html.escape((r["text"] or "[медиа]")[:80]).replace("\n", " ")
            lines.append(f"  • {mention(r['user_id'], _name(r))}: «{snippet}» — {n} ответов")
        out.append("🔥 самое обсуждаемое:\n" + "\n".join(lines))
    if pairs:
        (a, b), n = pairs.most_common(1)[0]
        out.append(f"💬 больше всех переписывались: {await nm(chat_id, a)} и {await nm(chat_id, b)} ({n} ответов друг другу)")
    if hours:
        h, n = hours.most_common(1)[0]
        out.append(f"⏰ пик активности: {h:02d}:00–{h + 1:02d}:00 ({n} сообщений)")
    extras = []
    if links:
        extras.append(f"🔗 ссылок: {links}")
    if media:
        extras.append(f"🖼 медиа: {media}")
    if extras:
        out.append(" · ".join(extras))
    return "\n".join(out) or "говорили о чём-то, но ни одно слово не повторилось. загадочно."


async def llm_digest(chat_id: int, msgs: list, period: str) -> str | None:
    """Связный пересказ через OpenAI-совместимый API. None — если нет ключа или ошибка."""
    if not LLM_API_KEY or not msgs:
        return None
    import aiohttp
    lines = []
    for r in msgs[-400:]:
        t = (r["text"] or "").replace("\n", " ").strip()
        if not t or t.startswith(("/", "!", "+", "-")):
            continue
        lines.append(f"[{time.strftime('%H:%M', time.localtime(r['ts']))}] {_name(r)}: {t[:300]}")
    if len(lines) < 5:
        return None
    transcript = "\n".join(lines)[-14000:]
    system = ("Ты — Саррочка, бот-модератор телеграм-чата в стиле «пикми»: пишешь строчными буквами, слегка обиженно, "
              "ироничная, всё замечаешь, иногда жалуешься, что её никто не ценит. Составь дайджест переписки чата на русском. "
              "Формат: 4–8 коротких абзацев или пунктов — какие темы обсуждали, кто что предлагал, о чём спорили, что смешного было, "
              "кто был активнее всех. Упоминай людей по именам, как они написаны. Не выдумывай того, чего нет в переписке. "
              "Без вступлений вроде «вот дайджест». Не используй markdown-разметку. В конце — одна фраза от себя в своём стиле.")
    payload = {"model": LLM_MODEL, "temperature": 0.7, "max_tokens": 700,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": f"Переписка {period}:\n\n{transcript}"}]}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
            async with s.post(f"{LLM_BASE_URL}/chat/completions", json=payload,
                              headers={"Authorization": f"Bearer {LLM_API_KEY}"}) as resp:
                data = await resp.json()
                if resp.status != 200:
                    logging.warning("llm %s: %s", resp.status, str(data)[:300])
                    return None
                return html.escape(data["choices"][0]["message"]["content"].strip())[:3000]
    except Exception as e:
        logging.warning("llm: %s", str(e)[:200])
        return None


async def build_summary(chat_id: int, hours: int, period: str) -> dict:
    since = int(time.time()) - hours * 3600
    st = await db.summary_stats(chat_id, since)
    top = await db.summary_top(chat_id, since, 3)
    top_lines = [f"{await nm(chat_id, r['user_id'])} ({r['n']})" for r in top]
    msgs = await db.messages_since(chat_id, since, 600)
    narrative = await llm_digest(chat_id, msgs, period)
    talk = narrative or await keyword_digest(chat_id, msgs)
    events = await db.events(chat_id, since, 40)
    ev_lines = [f"{time.strftime('%H:%M' if hours <= 24 else '%d.%m %H:%M', time.localtime(e['ts']))} {e['text']}" for e in events]
    return {**st, "top": ", ".join(top_lines) or "—", "talk": talk,
            "events": "\n".join(ev_lines) or "событий не было. только разговоры. и я."}


@router.message(Command("сводка", "сводка1", "сводка24", "сводка7", "summary", "дайджест", prefix="!+/"))
async def cmd_summary(msg: Message):
    cmd = (msg.text or "").split()[0][1:].lower()
    args = split_args(msg)
    num = cmd.replace("сводка", "").replace("summary", "").replace("дайджест", "") or (args[0] if args else "24")
    hours = {"1": 1, "24": 24, "7": 168, "неделя": 168, "день": 24, "час": 1, "сутки": 24}.get(num, 24)
    label = {1: "за час", 24: "за сутки", 168: "за неделю"}[hours]
    status = await reply(msg, "summary_wait")
    d = await build_summary(msg.chat.id, hours, label)
    try:
        await status.delete()
    except Exception:
        pass
    text = say(await style(msg.chat.id), "summary", chat_id=msg.chat.id, period=label, **d)
    while text:
        cut = text.rfind("\n", 0, 4000) if len(text) > 4000 else len(text)
        sent = await msg.answer(text[:cut], disable_web_page_preview=True)
        await db.remember_bot_message(msg.chat.id, sent.message_id)
        text = text[cut:].lstrip("\n")
