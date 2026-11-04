"""Социалка через «+»: +ник, +брак, +развод, +пара, +браки, +влюблен, +люблю сарру."""
import html
import time
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import say, ACHIEVEMENTS
from utils import reply, mention, split_args, resolve_target, style, deny

router = Router()
NICK_MAX = 24


async def name_of(chat_id: int, user_id: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, user_id)
    if row and row["nick"]:
        return row["nick"]
    return (row["first_name"] if row else None) or fallback or "кто-то"


async def m(chat_id: int, user_id: int, fallback: str | None = None) -> str:
    return mention(user_id, await name_of(chat_id, user_id, fallback))


# ---------- +ник ----------
@router.message(Command("ник", "nick", prefix="+/!"))
async def cmd_nick(msg: Message):
    await db.touch_user(msg.chat.id, msg.from_user)
    args = split_args(msg)
    if not args:
        row = await db.user(msg.chat.id, msg.from_user.id)
        if row and row["nick"]:
            return await reply(msg, "nick_show", user=mention(msg.from_user.id, msg.from_user.first_name), nick=html.escape(row["nick"]))
        return await reply(msg, "nick_usage")
    nick = " ".join(args).strip()
    if nick.lower() in ("снять", "удалить", "нет", "-", "none"):
        await db.set_nick(msg.chat.id, msg.from_user.id, None)
        return await reply(msg, "nick_removed", user=mention(msg.from_user.id, msg.from_user.first_name))
    if len(nick) > NICK_MAX or "<" in nick or ">" in nick:
        return await reply(msg, "nick_bad", max=NICK_MAX)
    await db.set_nick(msg.chat.id, msg.from_user.id, nick)
    await reply(msg, "nick_set", user=mention(msg.from_user.id, msg.from_user.first_name), nick=html.escape(nick))


async def can_marry(chat_id: int, uid: int) -> bool:
    s = await db.settings(chat_id)
    return len(await db.partners(chat_id, uid)) < max(1, int(s.get("max_spouses", 1)))


KINDS = {"никях": "🕌 никях", "венчание": "⛪ венчание"}
KIND_ALIASES = {"никях": "никях", "никах": "никях", "nikah": "никях", "венчание": "венчание", "венчать": "венчание", "церковь": "венчание"}


def kind_label(kind: str | None) -> str:
    return KINDS.get(kind or "", "💍 брак")


# ---------- +брак ----------
@router.message(Command("брак", "marry", "женись", "выходи", "никях", "венчание", prefix="+/!"))
async def cmd_marry(msg: Message):
    """+брак (ответом) — предложение; +брак никях / +брак венчание — сразу с типом."""
    chat_id = msg.chat.id
    await db.touch_user(chat_id, msg.from_user)
    uid, name, args = await resolve_target(msg)
    if not uid:
        return await reply(msg, "marry_usage")
    if uid == msg.from_user.id:
        return await reply(msg, "marry_self")
    if await db.marriage_with(chat_id, msg.from_user.id, uid):
        return await reply(msg, "marry_same", user=await m(chat_id, uid, name))
    if not await can_marry(chat_id, msg.from_user.id):
        return await reply(msg, "marry_already_me")
    if not await can_marry(chat_id, uid):
        return await reply(msg, "marry_taken", user=await m(chat_id, uid, name))
    cmd = (msg.text or "").split()[0][1:].lower()
    kind = KIND_ALIASES.get(cmd) or next((KIND_ALIASES[a.lower()] for a in args if a.lower() in KIND_ALIASES), None)
    if not kind:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🕌 никях", callback_data=f"mkind:{msg.from_user.id}:{uid}:никях"),
            InlineKeyboardButton(text="⛪ венчание", callback_data=f"mkind:{msg.from_user.id}:{uid}:венчание"),
        ]])
        sent = await msg.answer(say(await style(chat_id), "marry_kind", chat_id=chat_id,
                                    user=await m(chat_id, msg.from_user.id, msg.from_user.first_name)), reply_markup=kb)
        await db.remember_bot_message(chat_id, sent.message_id)
        return
    await send_proposal(msg, msg.from_user.id, msg.from_user.first_name, uid, name, kind)


async def send_proposal(msg: Message, a: int, an: str | None, b: int, bn: str | None, kind: str):
    chat_id = msg.chat.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="да 💍", callback_data=f"marry:{a}:{b}:yes:{kind}"),
        InlineKeyboardButton(text="нет 💔", callback_data=f"marry:{a}:{b}:no:{kind}"),
    ]])
    st = await style(chat_id)
    text = say(st, "marry_proposal", chat_id=chat_id, user=await m(chat_id, a, an), target=await m(chat_id, b, bn), kind=kind_label(kind))
    sent = await msg.answer(text, reply_markup=kb)
    await db.remember_bot_message(chat_id, sent.message_id)


@router.callback_query(F.data.startswith("mkind:"))
async def on_marry_kind(cb: CallbackQuery):
    _, a, b, kind = cb.data.split(":")
    a, b = int(a), int(b)
    if cb.from_user.id != a:
        return await cb.answer("выбирает тот, кто делает предложение.", show_alert=True)
    try:
        await cb.message.delete()
    except TelegramBadRequest:
        pass
    await send_proposal(cb.message, a, cb.from_user.first_name, b, None, kind)
    await cb.answer()


@router.callback_query(F.data.startswith("marry:"))
async def on_marry(cb: CallbackQuery):
    parts = cb.data.split(":")
    _, a, b, ans = parts[:4]
    kind = parts[4] if len(parts) > 4 else "никях"
    a, b = int(a), int(b)
    chat_id = cb.message.chat.id
    if cb.from_user.id != b:
        return await cb.answer("это предложение не тебе. не завидуй.", show_alert=True)
    st = await style(chat_id)
    ma, mb = await m(chat_id, a), await m(chat_id, b, cb.from_user.first_name)
    if ans == "no":
        text = say(st, "marry_no", chat_id=chat_id, user=ma, target=mb)
    elif not await can_marry(chat_id, a) or not await can_marry(chat_id, b) or await db.marriage_with(chat_id, a, b):
        text = say(st, "marry_taken", chat_id=chat_id, user=ma)
    else:
        await db.marry(chat_id, a, b, kind)
        text = say(st, "marry_yes", chat_id=chat_id, user=ma, target=mb, kind=kind_label(kind))
    try:
        await cb.message.edit_text(text)
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.message(Command("поженить", "женить", "marryall", prefix="+/!"))
async def cmd_marry_force(msg: Message, bot: Bot):
    """!поженить @юзер @юзер — админ женит без согласия (младший админ+). Или ответом + один @юзер."""
    if await deny(msg, bot, "court"):   # court == ранг «младший админ»
        return
    chat_id = msg.chat.id
    args = split_args(msg)
    ids: list[tuple[int, str | None]] = []
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        ids.append((u.id, u.first_name))
    for a in args:
        row = await db.find_user_by_username(chat_id, a) if a.startswith("@") else (await db.user(chat_id, int(a)) if a.isdigit() else None)
        if row:
            ids.append((row["user_id"], row["first_name"]))
    ids = list(dict.fromkeys(ids))[:2]
    if len(ids) < 2:
        return await reply(msg, "marry_force_usage")
    (a, an), (b, bn) = ids
    if a == b:
        return await reply(msg, "marry_self")
    if await db.marriage_with(chat_id, a, b):
        return await reply(msg, "marry_same", user=await m(chat_id, b, bn))
    if not await can_marry(chat_id, a):
        return await reply(msg, "marry_taken", user=await m(chat_id, a, an))
    if not await can_marry(chat_id, b):
        return await reply(msg, "marry_taken", user=await m(chat_id, b, bn))
    kind = next((KIND_ALIASES[x.lower()] for x in args if x.lower() in KIND_ALIASES), "никях")
    await db.marry(chat_id, a, b, kind)
    await reply(msg, "marry_forced", kind=kind_label(kind), by=await m(chat_id, msg.from_user.id, msg.from_user.first_name),
                user=await m(chat_id, a, an), target=await m(chat_id, b, bn))


@router.message(Command("развести", "divorceall", prefix="+/!"))
async def cmd_divorce_force(msg: Message, bot: Bot):
    """!развести @юзер — админ расторгает чужой брак."""
    if await deny(msg, bot, "court"):
        return
    uid, name, _ = await resolve_target(msg)
    if not uid:
        return await reply(msg, "marry_force_usage")
    ps = await db.partners(msg.chat.id, uid)
    if not ps:
        return await reply(msg, "partner_none", user=await m(msg.chat.id, uid, name))
    await db.divorce(msg.chat.id, uid)
    await reply(msg, "divorce", user=await m(msg.chat.id, uid, name), target=", ".join([await m(msg.chat.id, p) for p in ps]))


@router.message(Command("развод", "divorce", prefix="+/!"))
async def cmd_divorce(msg: Message):
    """+развод — расторгнуть брак; при многожёнстве: +развод @юзер (или ответом) — с кем именно."""
    chat_id = msg.chat.id
    ps = await db.partners(chat_id, msg.from_user.id)
    if not ps:
        return await reply(msg, "no_partner")
    uid, name, _ = await resolve_target(msg)
    if len(ps) > 1 and (not uid or uid not in ps):
        return await reply(msg, "divorce_which", list=", ".join([await m(chat_id, p) for p in ps]))
    other = uid if uid in ps else ps[0]
    await db.divorce(chat_id, msg.from_user.id, other)
    await reply(msg, "divorce", user=await m(chat_id, msg.from_user.id, msg.from_user.first_name), target=await m(chat_id, other))


@router.message(Command("пара", "partner", prefix="+/!"))
async def cmd_partner(msg: Message):
    chat_id = msg.chat.id
    uid, name, _ = await resolve_target(msg)
    if not uid:
        uid, name = msg.from_user.id, msg.from_user.first_name
    ps = await db.partners(chat_id, uid)
    if not ps:
        return await reply(msg, "partner_none", user=await m(chat_id, uid, name))
    if len(ps) == 1:
        row = await db.marriage_with(chat_id, uid, ps[0])
        return await reply(msg, "partner_show", user=await m(chat_id, uid, name), target=await m(chat_id, ps[0]),
                           days=duration(int(time.time()) - row["since"]))
    lines = []
    for p in ps:
        row = await db.marriage_with(chat_id, uid, p)
        lines.append(f"{kind_label(row['kind'])}: {await m(chat_id, p)} — {duration(int(time.time()) - row['since'])}")
    await reply(msg, "partners_show", user=await m(chat_id, uid, name), n=len(ps), list="\n".join(lines))


WEDDINGS = [  # (минимум лет, эмодзи, название)
    (0, "🫶", "Молодожёны"), (1, "🤍", "Ситцевая свадьба"), (2, "🧻", "Бумажная свадьба"), (3, "👞", "Кожаная свадьба"),
    (4, "🧵", "Льняная свадьба"), (5, "🪵", "Деревянная свадьба"), (6, "⚙️", "Чугунная свадьба"), (7, "🥉", "Медная свадьба"),
    (8, "🥫", "Жестяная свадьба"), (9, "🏺", "Фаянсовая свадьба"), (10, "🌹", "Розовая свадьба"), (15, "💎", "Хрустальная свадьба"),
    (20, "🍽", "Фарфоровая свадьба"), (25, "🥈", "Серебряная свадьба"), (50, "🥇", "Золотая свадьба"),
]


def wedding_title(years: int) -> tuple[str, str]:
    cur = WEDDINGS[0]
    for w in WEDDINGS:
        if years >= w[0]:
            cur = w
    return cur[1], cur[2]


def duration(seconds: int) -> str:
    """3 года 5 мес · 23 дня 1 ч · 2 часа 27 мин"""
    y, rem = divmod(seconds, 365 * 86400)
    mo, rem = divmod(rem, 30 * 86400)
    d, rem = divmod(rem, 86400)
    h, rem = divmod(rem, 3600)
    mi = rem // 60
    def pl(n, one, few, many):
        return f"{n} " + (one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many)
    if y:
        return pl(y, "год", "года", "лет") + (f" {mo} мес" if mo else "")
    if mo:
        return f"{mo} мес" + (f" {d} дн" if d else "")
    if d:
        return pl(d, "день", "дня", "дней") + (f" {h} ч" if h else "")
    if h:
        return pl(h, "час", "часа", "часов") + (f" {mi} мин" if mi else "")
    return f"{mi} мин"


@router.message(Command("браки", "marriages", "пары", prefix="+/!"))
async def cmd_marriages(msg: Message):
    chat_id = msg.chat.id
    rows = sorted(await db.marriages(chat_id), key=lambda r: r["since"])
    if not rows:
        return await reply(msg, "marriages_empty")
    now = int(time.time())
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for i, r in enumerate(rows, 1):
        age = now - r["since"]
        emoji, title = wedding_title(age // (365 * 86400))
        head = f"{emoji} <b>{title}</b>"
        if head not in groups:
            groups[head] = []
            order.append(head)
        k = KINDS.get(r["kind"] or "", "").split(" ")[0]
        groups[head].append(f"{i}. {await m(chat_id, r['a'])} + {await m(chat_id, r['b'])} ({duration(age)}) {k}".rstrip())
    text = "💍 <b>Браки этого чата</b>\n\n" + "\n\n".join(h + "\n" + "\n".join(groups[h]) for h in order)
    # длинные списки — кусками по 4000 символов
    while text:
        cut = text.rfind("\n", 0, 4000) if len(text) > 4000 else len(text)
        sent = await msg.answer(text[:cut], disable_web_page_preview=True)
        await db.remember_bot_message(chat_id, sent.message_id)
        text = text[cut:].lstrip("\n")


# ---------- +влюблен ----------
@router.message(Command("влюблен", "влюблён", "влюблена", "crush", prefix="+/!"))
async def cmd_crush(msg: Message):
    chat_id = msg.chat.id
    await db.touch_user(chat_id, msg.from_user)
    uid, name, _ = await resolve_target(msg)
    me = await m(chat_id, msg.from_user.id, msg.from_user.first_name)
    if not uid:
        t = await db.crush(chat_id, msg.from_user.id)
        if not t:
            return await reply(msg, "crush_none", user=me)
        return await reply(msg, "crush_show", user=me, target=await m(chat_id, t))
    if uid == msg.from_user.id:
        return await reply(msg, "crush_self", user=me)
    await db.set_crush(chat_id, msg.from_user.id, uid)
    target = await m(chat_id, uid, name)
    if await db.crush(chat_id, uid) == msg.from_user.id:
        return await reply(msg, "crush_mutual", user=me, target=target)
    await reply(msg, "crush_set", user=me, target=target)


@router.message(Command("разлюбил", "разлюбила", "uncrush", prefix="+/!"))
async def cmd_uncrush(msg: Message):
    await db.set_crush(msg.chat.id, msg.from_user.id, None)
    await reply(msg, "crush_removed", user=await m(msg.chat.id, msg.from_user.id, msg.from_user.first_name))


# ---------- +люблю сарру ----------
@router.message(Command("люблю", "обожаю", "love", prefix="+/!"))
async def cmd_love(msg: Message):
    chat_id = msg.chat.id
    args = " ".join(split_args(msg)).lower()
    me = await m(chat_id, msg.from_user.id, msg.from_user.first_name)
    if not any(w in args for w in ("сар", "сарр", "бот", "тебя")):
        # «+люблю @кого-то» — считаем как влюблённость
        uid, name, _ = await resolve_target(msg)
        if uid and uid != msg.from_user.id:
            await db.set_crush(chat_id, msg.from_user.id, uid)
            return await reply(msg, "crush_set", user=me, target=await m(chat_id, uid, name))
        return await reply(msg, "love_usage")
    await db.touch_user(chat_id, msg.from_user)
    n = await db.love_sarra(chat_id, msg.from_user.id)
    await db.bump_mood(chat_id, 15)
    await db.add_mass(chat_id, msg.from_user.id, 3)
    await reply(msg, "love_sarra", user=me, n=n)
    if await db.give_achievement(chat_id, msg.from_user.id, "lover"):
        await reply(msg, "achievement", user=me, ach=ACHIEVEMENTS["lover"])


# ---------- +отношения: сводка ----------
@router.message(Command("отношения", "статус", "relations", prefix="+/!"))
async def cmd_relations(msg: Message):
    chat_id = msg.chat.id
    uid, name, _ = await resolve_target(msg)
    if not uid:
        uid, name = msg.from_user.id, msg.from_user.first_name
    me = await m(chat_id, uid, name)
    p = await db.crush(chat_id, uid)
    ps = await db.partners(chat_id, uid)
    lines = []
    for partner in ps:
        row = await db.marriage_with(chat_id, uid, partner)
        lines.append(f"{kind_label(row['kind'])} с {await m(chat_id, partner)} — {duration(int(time.time()) - row['since'])}")
    if not ps:
        lines.append("💍 не в браке. +брак (ответом) — если решишься.")
    if p:
        mutual = await db.crush(chat_id, p) == uid
        lines.append(f"💘 влюблён(а) в {await m(chat_id, p)}" + (" — и это взаимно!" if mutual else ""))
    else:
        lines.append("💘 ни в кого не влюблён(а). +влюблен (ответом) — я никому не скажу. скажу.")
    await msg.answer(f"{me}:\n" + "\n".join(lines))


# ---------- действия: +поцеловать, +обнять, +погладить, +кусь, +шлёпнуть, +вирт ----------
ACTIONS = {
    "поцеловать": "act_kiss", "поцелуй": "act_kiss", "чмок": "act_kiss", "kiss": "act_kiss",
    "обнять": "act_hug", "обнимашки": "act_hug", "hug": "act_hug",
    "погладить": "act_pat", "pat": "act_pat",
    "кусь": "act_bite", "укусить": "act_bite",
    "шлепнуть": "act_slap", "шлёпнуть": "act_slap", "шлепок": "act_slap", "slap": "act_slap",
    "вирт": "act_virt",
}


@router.message(Command(*ACTIONS.keys(), prefix="+!/"))
async def cmd_action(msg: Message):
    chat_id = msg.chat.id
    cmd = (msg.text or "").split()[0][1:].lower()
    key = ACTIONS.get(cmd)
    if not key:
        return
    await db.touch_user(chat_id, msg.from_user)
    me = await m(chat_id, msg.from_user.id, msg.from_user.first_name)
    if key == "act_virt":
        await db.bump_mood(chat_id, -5)
        return await reply(msg, key, user=me)
    uid, name, _ = await resolve_target(msg)
    if not uid or uid == msg.from_user.id:
        await db.bump_mood(chat_id, 5)
        return await reply(msg, key + "_me", user=me)
    await reply(msg, key, user=me, target=await m(chat_id, uid, name))
