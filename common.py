import html
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message
from db import db
from phrases import say, ACHIEVEMENTS
from config import MASS_PER_THANKS
from utils import reply, deny, mention, split_args, style

router = Router()


@router.message(Command("start", "help"))
async def cmd_help(msg: Message):
    s = await db.settings(msg.chat.id)
    key = "start" if msg.chat.type == "private" and msg.text.startswith("/start") else "help"
    await reply(msg, key, n=s["warn_decay_days"])


@router.message(Command("thanks", "спасибо", prefix="/!"))
async def cmd_thanks(msg: Message):
    await db.touch_user(msg.chat.id, msg.from_user)
    await db.add_mass(msg.chat.id, msg.from_user.id, MASS_PER_THANKS)
    await db.bump_mood(msg.chat.id, 10)
    await reply(msg, "thanks")
    if await db.give_achievement(msg.chat.id, msg.from_user.id, "noticed"):
        await reply(msg, "achievement", user=mention(msg.from_user.id, msg.from_user.first_name),
                    ach=ACHIEVEMENTS["noticed"])


@router.message(Command("mood", "настроение", prefix="/!"))
async def cmd_mood(msg: Message):
    m = await db.mood(msg.chat.id)
    st = await style(msg.chat.id)
    comment_key = "mood_low" if m < 30 else "mood_mid" if m < 70 else "mood_high"
    await reply(msg, "mood", mood=int(m), comment=say(st, comment_key, chat_id=msg.chat.id))


@router.message(Command("style", "стиль", prefix="/!"))
async def cmd_style(msg: Message, bot: Bot):
    if await deny(msg, bot, "style"):
        return
    args = split_args(msg)
    if not args or args[0] not in ("pickme", "strict", "normal"):
        return await msg.answer("/style pickme | strict | normal")
    await db.set_setting(msg.chat.id, "style", args[0])
    await reply(msg, "style_set", style=args[0])


@router.message(Command("settings", "настройки", prefix="/!"))
async def cmd_settings(msg: Message, bot: Bot):
    if await deny(msg, bot, "settings"):
        return
    args = split_args(msg)
    s = await db.settings(msg.chat.id)
    if len(args) == 2 and args[0] in s:
        key, val = args
        try:
            cur = s[key]
            new = val if isinstance(cur, str) else (float(val) if isinstance(cur, float) else int(val))
        except ValueError:
            return await msg.answer("значение должно быть числом")
        await db.set_setting(msg.chat.id, key, new)
        s = await db.settings(msg.chat.id)
    listing = "\n".join(f"• {k} = {v}" for k, v in s.items())
    await reply(msg, "settings", list=html.escape(listing))


@router.message(Command("digest"))
async def cmd_digest(msg: Message, bot: Bot):
    if await deny(msg, bot, "digest"):
        return
    await send_digest(bot, msg.chat.id)


async def send_digest(bot: Bot, chat_id: int):
    st = await style(chat_id)
    active = await db.top_users(chat_id, "msgs")
    massive = await db.top_users(chat_id, "mass")
    warned = await db.most_warned(chat_id)
    fmt = lambda rows, f, suf: "\n".join(
        f"{i + 1}. {mention(r['user_id'], r['first_name'])} — {int(r[f])}{suf}" for i, r in enumerate(rows)
    ) or "—"
    text = say(
        st, "digest", chat_id=chat_id,
        active=fmt(active, "msgs", " сообщ."),
        massive=fmt(massive, "mass", " массы"),
        warned=(html.escape(warned["first_name"] or "") + f" ({warned['n']})") if warned else "никого, чудо",
    )
    sent = await bot.send_message(chat_id, text)
    await db.remember_bot_message(chat_id, sent.message_id)
    await db.mark_digest(chat_id)


# Ревность: в чат добавили другого бота
@router.message(F.new_chat_members)
async def on_new_members(msg: Message, bot: Bot):
    me = await bot.get_me()
    for u in msg.new_chat_members:
        if u.is_bot and u.id != me.id:
            await reply(msg, "jealous", user=html.escape(u.first_name))


@router.message(Command("болтай", "скажи", "chatter", prefix="!+/"))
async def cmd_chatter(msg: Message):
    """!болтай — реплика из болтовни прямо сейчас."""
    await reply(msg, "chatter")


USER_COMMANDS = (
    "📋 <b>что можно всем</b> (пишется без слэша, как есть):\n\n"
    "👤 <b>обо мне</b>\n"
    "• <code>!кто я</code> — моя карточка с графиком активности\n"
    "• <code>!кто ты</code> (ответом) — карточка другого\n"
    "• <code>+ник Имя</code> — мой ник · <code>+ник снять</code>\n"
    "• <code>+описание текст</code> — описание в карточке\n"
    "• <code>+звание текст</code> · <code>+пол м|ж</code> · <code>+город Москва</code> · <code>+др 24.03</code> — поздравлю!\n"
    "• <code>!топ</code> — топ по массе · <code>!стата неделя</code> — топ по сообщениям · <code>!олды</code> · <code>!новички</code>\n"
    "• <code>!правила</code> — правила чата · <code>!гб</code> (ответом) — голосование за бан\n\n"
    "💍 <b>отношения</b>\n"
    "• <code>+брак</code> (ответом) — предложение: 🕌 никях или ⛪ венчание (или сразу <code>+никях</code> / <code>+венчание</code>); до 4 браков\n"
    "• развод — муж пишет «развод» три раза отдельными сообщениями · <code>+пара</code> · <code>+браки</code>\n"
    "• <code>+влюблен</code> (ответом) — влюбиться · <code>+разлюбил</code>\n"
    "• <code>+отношения</code> — кто у меня кто\n"
    "• <code>+люблю сарру</code> — признаться мне 🥺\n"
    "• <code>+поцеловать</code> · <code>+обнять</code> · <code>+погладить</code> · <code>+кусь</code> · <code>+шлёпнуть</code> (ответом; без ответа — мне 🙈)\n\n"
    "⚔️ <b>дуэли</b>\n"
    "• <code>!дуэль</code> (ответом) · <code>!дуэль 10</code> — на 10 коинов · <code>!дуэли</code> — топ\n\n"
    "🏅 <code>!номинации</code> — рубрики дня: 🫦 подкаблучник, 💔 ПЗР, 💅 пикми (итоги вечером, +10 коинов)\n"
    "👑 <code>!король</code> — король и королева дня (голосование каждый вечер, +50 коинов)\n"
    "📜 <code>!поумничай</code> — цитата великих · 📊 <code>!сводка1</code> / <code>!сводка24</code> / <code>!сводка7</code>\n\n"
    "🪙 <b>сарра-коины</b>\n"
    "• <code>+коины</code> — мой баланс · <code>+лидеры</code> — топ месяца\n"
    "• <code>!награды</code> — что дают за коины · <code>!обменять мишку</code> — забрать награду\n"
    "• +100 коинов за каждые 500 сообщений, 1-го числа — итоги и приз\n\n"
    "🎵 <b>музыка</b>\n"
    "• <code>трек название</code> / <code>включи название</code> / <code>найди название</code> — пришлю mp3\n"
    "• <code>!картинка запрос</code> / <code>найди картинку кот</code> — пришлю картинку\n"
    "• <code>!видео ссылка</code> — скачаю видео (инста, тикток, ютуб, вк); рилсы и тиктоки качаю сама\n\n"
    "🛠 <b>полезное</b>\n"
    "• <code>/remind 2h текст</code> — напомню\n"
    "• <code>/tag имя</code> — стикер по тегу · <code>/tags</code> — список\n"
    "• <code>!репорт</code> (ответом) — пожаловаться админам\n"
    "• <code>!спасибо</code> · <code>!настроение</code> · <code>!болтай</code>\n\n"
    "админские команды — в <code>/help</code>."
)


@router.message(Command("команды", "commands", "кмд", prefix="!+/"))
async def cmd_commands(msg: Message):
    sent = await msg.answer(USER_COMMANDS, disable_web_page_preview=True)
    await db.remember_bot_message(msg.chat.id, sent.message_id)
