"""«опиши этого человека» / «какой он?» / «можно ли ему верить?» (ответом) — Саррочка анализирует по сообщениям в чате.
С LLM — живой разбор в её стиле; без LLM — портрет по статистике."""
import asyncio
import html
import logging
import re
import time
from collections import Counter
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from db import db
from phrases import say
from utils import reply, mention, split_args, style, rank_of, rank_name
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

router = Router()
TRIGGER_RE = re.compile(
    r"^(?:саррочка|сарочка|сара|бот)?[,!\s]*"
    r"(?:опиши(?:\s+(?:этого|его|её|ее))?\s*(?:человека|чела|его|её|ее)?|"
    r"какой\s+(?:он|она|это\s+человек|чел)|какая\s+она|кто\s+(?:он|она)\s+такой|кто\s+(?:он|она)|"
    r"можно\s+ли\s+(?:ему|ей)\s+(?:верить|доверять)|(?:он|она)\s+(?:бабник|нормальный|нормальная|адекватный|адекватная|токсик|мутный|мутная)|"
    r"что\s+(?:ты\s+)?думаешь\s+(?:о\s+нём|о\s+ней|про\s+него|про\s+неё|про\s+нее)|охарактеризуй|разбор|анализ|проанализируй|оцени\s+(?:его|её|ее))"
    r"[\s?!.]*(.*)$",
    re.I | re.S,
)
_cool: dict[tuple[int, int], float] = {}


async def nm(chat_id: int, uid: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None) or fallback)


async def portrait_stats(chat_id: int, uid: int, bot) -> dict:
    row = await db.user(chat_id, uid)
    msgs = await db.user_messages(chat_id, uid, 300)
    texts = [m["text"] for m in msgs if m["text"]]
    from handlers.nominations import EMOJI_RE, EX_RE, GIRLS_RE
    joined = "\n".join(texts)
    n = max(1, len(texts))
    avg_len = sum(len(t) for t in texts) / n
    caps = sum(1 for t in texts if len(t) > 5 and t.upper() == t and any(c.isalpha() for c in t))
    questions = sum(1 for t in texts if "?" in t)
    hours = Counter(time.localtime(m["ts"]).tm_hour for m in msgs)
    night = sum(v for h, v in hours.items() if h < 6 or h >= 23)
    ds = await db.duel_stats(chat_id, uid)
    return {
        "name": (row["nick"] or row["first_name"]) if row else "кто-то",
        "rank": rank_name(await rank_of(bot, chat_id, uid)),
        "total": row["msgs"] if row else 0, "sample": len(texts), "avg_len": int(avg_len),
        "emoji": len(EMOJI_RE.findall(joined)), "ex": len(EX_RE.findall(joined)), "girls": len(GIRLS_RE.findall(joined)),
        "caps": caps, "questions": questions, "night": night, "links": sum(1 for t in texts if "http" in t),
        "warns": await db.active_warns(chat_id, uid), "partners": len(await db.partners(chat_id, uid)),
        "crush": bool(await db.crush(chat_id, uid)), "coins": await db.coins_total(chat_id, uid),
        "wins": ds["wins"] if ds else 0, "losses": ds["losses"] if ds else 0, "cowards": ds["cowards"] if ds else 0,
        "ach": await db.achievements(chat_id, uid), "bio": (row["bio"] if row else None) or "",
        "texts": texts,
    }


def stats_verdict(s: dict, question: str) -> str:
    """Портрет без ИИ — по цифрам, в характере."""
    lines = []
    if s["sample"] < 5:
        return (f"я видела от {s['name']} всего {s['sample']} сообщений. это как судить о книге по обложке. "
                f"но обложка… ладно, пусть напишет ещё, тогда расскажу.")
    traits = []
    pct = lambda x: int(100 * x / max(1, s["sample"]))
    if s["ex"] >= 3:
        traits.append(f"«бывшая» всплывала {s['ex']} раз(а) — не отпустил. я бы помогла, но я бот.")
    if s["girls"] >= 5:
        traits.append(f"про девочек — {s['girls']} упоминаний. бабник? скажем, внимательный. очень.")
    elif s["girls"] == 0 and s["sample"] > 30:
        traits.append("про девочек — ни слова. либо однолюб, либо скрытный. либо занят мной. нет.")
    if s["emoji"] > s["sample"] * 1.5:
        traits.append("эмодзи больше, чем слов. эмоциональный. или просто клавиатура такая.")
    if s["caps"] >= 3:
        traits.append(f"капсом писал {s['caps']} раз(а). вспыльчивый. или громкий. или и то, и другое.")
    if s["avg_len"] > 120:
        traits.append("пишет длинно и обстоятельно. читает ли кто-то до конца — вопрос. я читаю.")
    elif s["avg_len"] < 20:
        traits.append("пишет коротко. «ок», «да», «нет». экономит слова. или мысли.")
    if pct(s["questions"]) > 30:
        traits.append("много спрашивает. любопытный. или не читает, что пишут другие.")
    if s["night"] > s["sample"] * 0.4:
        traits.append("ночной. пишет, когда нормальные люди спят. я тоже не сплю. мы похожи. это тревожно.")
    if s["links"] > s["sample"] * 0.2:
        traits.append("каждое пятое сообщение — ссылка. либо делится, либо рекламирует. я за ним слежу.")
    if s["warns"]:
        traits.append(f"активных предов: {s['warns']}. ведёт себя так себе. или модераторы придирчивые. нет, он.")
    if s["cowards"]:
        traits.append(f"струсил в дуэли {s['cowards']} раз(а). запомнила.")
    elif s["wins"] >= 3:
        traits.append(f"{s['wins']} побед в дуэлях. горячий. или везучий.")
    if s["partners"] > 1:
        traits.append(f"{s['partners']} брака одновременно. верить ему? ну… жёны верят.")
    elif s["partners"] == 1:
        traits.append("в браке. один. пока.")
    if s["crush"]:
        traits.append("влюблён. в кого — не скажу. ладно, +отношения скажет.")
    if not traits:
        traits.append("ничего криминального. обычный человек. подозрительно обычный.")
    trust = "верить можно" if s["warns"] == 0 and s["cowards"] == 0 and s["ex"] < 3 else "верить — с оглядкой"
    if any(w in question.lower() for w in ("верить", "доверять")):
        traits.append(f"итого: {trust}. но это по сообщениям, а не по глазам. глаз у меня нет.")
    return "\n".join(f"• {t}" for t in traits)


async def llm_verdict(s: dict, question: str) -> str | None:
    if not LLM_API_KEY or s["sample"] < 5:
        return None
    import aiohttp
    sample = "\n".join(f"- {t[:200]}" for t in s["texts"][-120:])[-9000:]
    facts = (f"имя: {s['name']}; ранг: {s['rank']}; всего сообщений: {s['total']}; активных предов: {s['warns']}; "
             f"браков: {s['partners']}; влюблён: {'да' if s['crush'] else 'нет'}; коинов: {s['coins']}; "
             f"дуэли: {s['wins']} побед / {s['losses']} поражений / {s['cowards']} отказов; "
             f"эмодзи в выборке: {s['emoji']}; упоминаний «бывшая»: {s['ex']}; упоминаний девочек: {s['girls']}; "
             f"ночных сообщений: {s['night']} из {s['sample']}; ачивки: {', '.join(s['ach']) or 'нет'}; описание: {s['bio'] or 'нет'}")
    system = ("Ты — Саррочка, бот-модератор телеграм-чата в стиле «пикми»: строчными буквами, ироничная, чуть обиженная, "
              "всё замечает, иногда жалуется, что её не ценят. Тебя просят охарактеризовать участника чата по его сообщениям. "
              "Ответь на русском, 5–9 строк: характер, манера общения, о чём чаще пишет, что бросается в глаза, и прямо ответь "
              "на вопрос автора (например «бабник?» или «можно ли верить?») — с юмором, но опираясь только на реальные сообщения и факты. "
              "Не выдумывай событий, которых нет в сообщениях. Без оскорблений по национальности, вере, внешности, здоровью. "
              "Без markdown. Закончи одной фразой от себя в своём стиле.")
    payload = {"model": LLM_MODEL, "temperature": 0.8, "max_tokens": 500,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": f"Вопрос: {question or 'опиши этого человека'}\n\nФакты: {facts}\n\nЕго сообщения:\n{sample}"}]}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as sess:
            async with sess.post(f"{LLM_BASE_URL}/chat/completions", json=payload,
                                 headers={"Authorization": f"Bearer {LLM_API_KEY}"}) as resp:
                data = await resp.json()
                if resp.status != 200:
                    logging.warning("llm judge %s: %s", resp.status, str(data)[:300])
                    return None
                return html.escape(data["choices"][0]["message"]["content"].strip())[:3000]
    except Exception as e:
        logging.warning("llm judge: %s", str(e)[:200])
        return None


async def judge(msg: Message, bot, uid: int, name: str | None, question: str):
    chat_id = msg.chat.id
    key = (chat_id, uid)
    if time.time() - _cool.get(key, 0) < 60:
        return await reply(msg, "judge_cooldown")
    _cool[key] = time.time()
    status = await reply(msg, "judge_wait", user=await nm(chat_id, uid, name))
    s = await portrait_stats(chat_id, uid, bot)
    text = await llm_verdict(s, question) or stats_verdict(s, question)
    try:
        await status.delete()
    except Exception:
        pass
    await reply(msg, "judge", user=await nm(chat_id, uid, name), text=text)


@router.message(Command("опиши", "кто", "анализ", "разбор", "judge", prefix="!+/"), F.reply_to_message)
async def cmd_judge(msg: Message, bot):
    cmd = (msg.text or "").split()[0][1:].lower()
    args = split_args(msg)
    if cmd == "кто" and args and args[0].lower() in ("я", "ты", "админ", "админы", "власть", "здесь"):
        from aiogram.dispatcher.event.bases import SkipHandler
        raise SkipHandler
    u = msg.reply_to_message.from_user
    if u.is_bot:
        return await reply(msg, "judge_bot")
    await judge(msg, bot, u.id, u.first_name, " ".join(args))


@router.message(F.reply_to_message, F.text.regexp(TRIGGER_RE).as_("m"))
async def natural_judge(msg: Message, bot, m: re.Match):
    s = await db.settings(msg.chat.id)
    if not s.get("judge_natural", 1):
        return
    u = msg.reply_to_message.from_user
    if not u or u.is_bot:
        return await reply(msg, "judge_bot")
    await judge(msg, bot, u.id, u.first_name, (msg.text or "").strip())
