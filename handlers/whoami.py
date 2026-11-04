"""!кто я / !кто ты (ответом) / /me — большая карточка пользователя с графиком активности (как в Ирисе)."""
import asyncio
import html
import io
import os
import time
from datetime import date, timedelta
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from db import db
from phrases import ACHIEVEMENTS
from utils import reply, mention, level, resolve_target, rank_of, rank_name, split_args

router = Router()


def _ago(ts: int | None) -> str:
    if not ts:
        return "—"
    d = int(time.time()) - ts
    if d < 60:
        return "только что"
    if d < 3600:
        return f"{d // 60} мин"
    if d < 86400:
        return f"{d // 3600} ч"
    return f"{d // 86400} дн"


def _since(ts: int | None) -> str:
    if not ts:
        return "—"
    d0 = date.fromtimestamp(ts)
    days = (date.today() - d0).days
    y, rem = divmod(days, 365)
    mo, dd = divmod(rem, 30)
    parts = [f"{y} г" if y else "", f"{mo} мес" if mo else "", f"{dd} дн" if (dd and not y) else ""]
    return f"{d0:%d.%m.%Y} ({' '.join(p for p in parts if p) or 'сегодня'})"


def _fmt(n: int) -> str:
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def _chart_png(series: dict[str, int], first_day: str | None) -> bytes:
    """Столбчатый график сообщений по дням за 365 дней. Рисуется в отдельном потоке."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    today = date.today()
    days = [today - timedelta(days=i) for i in range(364, -1, -1)]
    vals = [series.get(d.isoformat(), 0) for d in days]
    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=110)
    fig.patch.set_facecolor("white")
    colors = []
    for d in days:
        if d >= today - timedelta(days=6):
            colors.append("#4a6b1f")
        elif d >= today - timedelta(days=29):
            colors.append("#7fb52a")
        else:
            colors.append("#c6e05a")
    ax.bar(range(365), vals, width=1.0, color=colors, linewidth=0)
    ax.set_title("Статистика активности за 365 дней", fontsize=10, color="#333")
    ax.yaxis.tick_right()
    ax.set_ylabel("Сообщений", fontsize=8, color="#555")
    ax.yaxis.set_label_position("right")
    ticks = list(range(0, 365, 45))
    ax.set_xticks(ticks)
    ax.set_xticklabels([days[i].strftime("%d.%m") for i in ticks], fontsize=7, color="#555")
    ax.tick_params(axis="y", labelsize=7, colors="#555")
    for s in ("top", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#ddd")
    ax.spines["right"].set_color("#ddd")
    ax.grid(axis="y", color="#eee", linewidth=0.6)
    ax.set_axisbelow(True)
    if first_day:
        try:
            fd = date.fromisoformat(first_day)
            if days[0] <= fd <= today:
                ax.axvline((fd - days[0]).days, color="#999", linestyle="--", linewidth=0.8)
        except ValueError:
            pass
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


async def build_card(bot: Bot, chat_id: int, uid: int, fallback_name: str | None) -> tuple[str, InlineKeyboardMarkup]:
    row = await db.user(chat_id, uid)
    name = (row["nick"] or row["first_name"]) if row else fallback_name
    mass = row["mass"] if row else 0
    msgs = row["msgs"] if row else 0
    rank = await rank_of(bot, chat_id, uid)
    try:
        cm = await bot.get_chat_member(chat_id, uid)
        tg_admin = cm.status in ("administrator", "creator")
        username = cm.user.username
    except TelegramBadRequest:
        tg_admin, username = False, (row["username"] if row else None)
    act = await db.activity(chat_id, uid)
    warns = await db.active_warns(chat_id, uid)
    coins = await db.coins_total(chat_id, uid)
    partner = await db.partner(chat_id, uid)
    crush = await db.crush(chat_id, uid)
    ach = await db.achievements(chat_id, uid)

    lines = [f"👤 Это пользователь {mention(uid, name)}" + (f" (@{username})" if username else "")]
    if tg_admin:
        lines.append("👨‍🔧 Телеграм-админ этого чата")
    lines += [
        "",
        f"⭐ [{rank}] Ранг: {rank_name(rank).capitalize()}",
        f"⚖️ Масса: {int(mass)} (уровень {level(mass)}, #{await db.rank(chat_id, uid)}) · 🪙 Сарра-коины: {coins}",
        f"Первое появление: {_since(row['joined_at'] if row else None)}",
        f"Последний актив: {_ago(row['last_seen'] if row else None)}",
        f"Актив (д|н|м|весь): {act['day']} | {act['week']} | {_fmt(act['month'])} | {_fmt(msgs)}",
    ]
    if warns:
        lines.append(f"⚠️ Активных предов: {warns}")
    rel = []
    if partner:
        p = await db.user(chat_id, partner)
        rel.append(f"💍 в браке с {mention(partner, (p['nick'] or p['first_name']) if p else None)}")
    if crush:
        c = await db.user(chat_id, crush)
        mutual = await db.crush(chat_id, crush) == uid
        rel.append(f"💘 влюблён(а) в {mention(crush, (c['nick'] or c['first_name']) if c else None)}" + (" (взаимно)" if mutual else ""))
    if rel:
        lines += [""] + rel
    lines.append("")
    lines.append("🏆 НАГРАДЫ:")
    lines += [f"🎗 {ACHIEVEMENTS.get(a, a)}" for a in ach] or ["пока нет. но я верю."]
    bio = row["bio"] if row and row["bio"] else None
    if bio:
        lines += ["", f"📝 {html.escape(bio)}"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Награды", callback_data=f"who:ach:{uid}"),
         InlineKeyboardButton(text="💞 Отношения", callback_data=f"who:rel:{uid}")],
        [InlineKeyboardButton(text="⚠️ Преды", callback_data=f"who:warns:{uid}"),
         InlineKeyboardButton(text="🪙 Коины", callback_data=f"who:coins:{uid}")],
    ])
    return "\n".join(lines), kb


async def send_card(msg: Message, bot: Bot, uid: int, name: str | None):
    chat_id = msg.chat.id
    text, kb = await build_card(bot, chat_id, uid, name)
    series = await db.activity_series(chat_id, uid, 365)
    row = await db.user(chat_id, uid)
    first_day = date.fromtimestamp(row["joined_at"]).isoformat() if row and row["joined_at"] else None
    try:
        png = await asyncio.get_running_loop().run_in_executor(None, _chart_png, series, first_day)
        sent = await msg.answer_photo(BufferedInputFile(png, "activity.png"), caption=text[:1024], reply_markup=kb)
    except Exception:
        sent = await msg.answer(text, reply_markup=kb)
    await db.remember_bot_message(chat_id, sent.message_id)


@router.message(Command("кто", "who", "whoami", prefix="!/+"))
async def cmd_who(msg: Message, bot: Bot):
    """!кто я · !кто ты (ответом) · !кто @юзер"""
    args = split_args(msg)
    uid, name, _ = await resolve_target(msg)
    if args and args[0].lower() in ("я", "me", "i") and not msg.reply_to_message:
        uid, name = msg.from_user.id, msg.from_user.first_name
    if not uid:
        uid, name = msg.from_user.id, msg.from_user.first_name
    await db.touch_user(msg.chat.id, msg.from_user)
    await send_card(msg, bot, uid, name)


@router.message(Command("описание", "bio", prefix="+!/"))
async def cmd_bio(msg: Message):
    """+описание текст — своё описание в карточке; +описание снять."""
    await db.touch_user(msg.chat.id, msg.from_user)
    text = " ".join(split_args(msg)).strip()
    if not text:
        return await msg.answer("+описание текст — и оно появится в твоей карточке. +описание снять — убрать.")
    if text.lower() in ("снять", "удалить", "-"):
        await db.set_bio(msg.chat.id, msg.from_user.id, None)
        return await msg.answer("описание убрала. ты снова загадка.")
    await db.set_bio(msg.chat.id, msg.from_user.id, text[:200])
    await msg.answer("записала. теперь все будут знать. ну, кто спросит !кто ты.")


@router.callback_query(F.data.startswith("who:"))
async def on_who_button(cb: CallbackQuery, bot: Bot):
    _, what, uid = cb.data.split(":")
    uid, chat_id = int(uid), cb.message.chat.id
    row = await db.user(chat_id, uid)
    name = mention(uid, (row["nick"] or row["first_name"]) if row else None)
    if what == "ach":
        ach = await db.achievements(chat_id, uid)
        text = f"🏆 награды {name}:\n" + ("\n".join(f"🎗 {ACHIEVEMENTS.get(a, a)}" for a in ach) or "пока нет. но я верю.")
    elif what == "rel":
        partner, crush = await db.partner(chat_id, uid), await db.crush(chat_id, uid)
        parts = []
        if partner:
            p = await db.user(chat_id, partner); parts.append(f"💍 в браке с {mention(partner, (p['nick'] or p['first_name']) if p else None)}")
        if crush:
            c = await db.user(chat_id, crush); parts.append(f"💘 влюблён(а) в {mention(crush, (c['nick'] or c['first_name']) if c else None)}")
        text = f"💞 {name}:\n" + ("\n".join(parts) or "свободен(а). как птица. как я.")
    elif what == "warns":
        rows = await db.list_warns(chat_id, uid)
        text = f"⚠️ преды {name}:\n" + ("\n".join(
            f"• {html.escape(r['reason'])} ({time.strftime('%d.%m', time.localtime(r['created_at']))})" for r in rows) or "чист. как слеза. моя.")
    else:
        from handlers.coins import month_key
        text = (f"🪙 {name}: всего {await db.coins_total(chat_id, uid)}, за месяц {await db.coins_month(chat_id, uid, month_key())}, "
                f"место #{await db.coins_rank(chat_id, uid, month_key())}")
    await cb.answer()
    sent = await bot.send_message(chat_id, text, reply_to_message_id=cb.message.message_id)
    await db.remember_bot_message(chat_id, sent.message_id)
