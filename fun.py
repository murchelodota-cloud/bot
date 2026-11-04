"""Мелочи как у Ириса: «мешок» — кошелёк, «моя статья» — шуточная статья дня, «шар» — предсказание."""
import hashlib
import random
import time
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from db import db
from utils import reply, mention, resolve_target, split_args

router = Router()

ARTICLES = [
    ("105.0", "Убийство времени в чате"), ("111.1", "Умышленное причинение тяжкого кринжа"),
    ("115", "Причинение лёгкого вреда настроению Саррочки"), ("119", "Угроза мутом"),
    ("128.1", "Клевета на бывшую"), ("129", "Оскорбление чувств подкаблучников"),
    ("137", "Нарушение неприкосновенности личных переписок"), ("145", "Необоснованный отказ принять дуэль"),
    ("158", "Кража мемов"), ("159", "Мошенничество с сарра-коинами"), ("163", "Вымогательство похвалы"),
    ("165", "Причинение имущественного ущерба путём обмена на мишку"), ("167", "Умышленное уничтожение чата флудом"),
    ("171", "Незаконное предпринимательство: продажа стикеров"), ("174", "Отмывание массы"),
    ("200.1", "Контрабанда наличных денежных средств и (или) денежных инструментов"),
    ("207", "Заведомо ложное сообщение о рейде"), ("213", "Хулиганство с применением капса"),
    ("214", "Вандализм в закрепе"), ("228", "Незаконный оборот эмодзи в особо крупном размере"),
    ("242", "Незаконное распространение рилсов"), ("245", "Жестокое обращение с ботом"),
    ("272", "Неправомерный доступ к чужой бывшей"), ("282", "Возбуждение ненависти к понедельникам"),
    ("285", "Злоупотребление админскими полномочиями"), ("290", "Получение взятки сарра-коинами"),
    ("306", "Заведомо ложный донос на подкаблучника"), ("319", "Оскорбление Саррочки при исполнении"),
    ("327", "Подделка ачивок"), ("330", "Самоуправство в видеочате"), ("354", "Дезертирство из голосового"),
    ("357", "Уклонение от никяха"), ("20.21 КоАП", "Появление в чате в состоянии кринжа"),
    ("20.1 КоАП", "Мелкое хулиганство: три «бывшая» подряд"), ("5.61 КоАП", "Оскорбление вкуса в музыке"),
    ("12.9 КоАП", "Превышение скорости набора сообщений"), ("6.1.1 КоАП", "Побои рандомных прохожих +шлёпнуть"),
]
VERDICTS = ["приговаривается к статье", "обвиняется по статье", "задержан(а) по статье", "осуждён(а) по статье",
            "проходит подозреваемым по статье", "объявлен(а) в розыск по статье"]

BALL = [
    "да. точно да. я бы не врала.", "нет. и не спрашивай больше.", "возможно. я бы не рассчитывала.",
    "спроси ещё раз, когда я буду в настроении.", "определённо да. но ты пожалеешь.", "звёзды говорят нет. я тоже.",
    "да, если скажешь мне спасибо.", "мои источники говорят нет.", "перспективы туманные. как и всё тут.",
    "знаки указывают на да.", "лучше не знать. честно.", "однозначно. но не сегодня.",
    "нет, но мне нравится твоя наивность.", "да. и я буду рядом, когда это случится.", "ещё чего. нет.",
]


async def nm(chat_id: int, uid: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None) or fallback)


def _daily(seed: str, n: int) -> int:
    """Стабильное число на день: одна и та же статья на весь день для человека."""
    key = f"{time.strftime('%Y-%m-%d')}:{seed}"
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % n


@router.message(F.text.lower().regexp(r"^(?:[!+/]\s*)?(?:мешок|кошел[её]к|баланс)$"))
async def cmd_bag(msg: Message):
    chat_id, uid = msg.chat.id, msg.from_user.id
    await db.touch_user(chat_id, msg.from_user)
    row = await db.user(chat_id, uid)
    from handlers.coins import month_key
    coins = await db.coins_total(chat_id, uid)
    month = await db.coins_month(chat_id, uid, month_key())
    rank = await db.coins_rank(chat_id, uid, month_key())
    ach = len(await db.achievements(chat_id, uid))
    ds = await db.duel_stats(chat_id, uid)
    await reply(msg, "bag", user=await nm(chat_id, uid, msg.from_user.first_name), coins=coins, month=month, rank=rank or "—",
                mass=int(row["mass"]) if row else 0, ach=ach, wins=ds["wins"] if ds else 0)


@router.message(F.text.lower().regexp(r"^(?:[!+/]\s*)?(?:моя статья|статья|твоя статья)$"))
async def cmd_article(msg: Message):
    chat_id = msg.chat.id
    uid, name, _ = await resolve_target(msg)
    if not uid or (msg.text or "").lower().strip("!+/ ") == "моя статья":
        uid, name = msg.from_user.id, msg.from_user.first_name
    await db.touch_user(chat_id, msg.from_user)
    art = ARTICLES[_daily(f"{chat_id}:{uid}", len(ARTICLES))]
    verdict = VERDICTS[_daily(f"v:{chat_id}:{uid}", len(VERDICTS))]
    await reply(msg, "article", user=await nm(chat_id, uid, name), verdict=verdict, num=art[0], title=art[1])


@router.message(Command("шар", "ball", "8ball", "предскажи", prefix="!+/"))
async def cmd_ball(msg: Message):
    q = " ".join(split_args(msg)).strip()
    if not q:
        return await reply(msg, "ball_usage")
    await reply(msg, "ball", user=await nm(msg.chat.id, msg.from_user.id, msg.from_user.first_name), answer=random.choice(BALL))
