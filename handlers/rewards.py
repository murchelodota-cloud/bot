"""!награды — что дают за сарра-коины; !обменять <название> — заявка на обмен; админ: +награда <цена> <что>, -награда <цена>."""
import html
import json
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from db import db
from config import REPORT_CHAT_ID
from utils import reply, deny, mention, split_args, rank_of

router = Router()
DEFAULT_REWARDS = [
    {"cost": 5000, "name": "мишку", "emoji": "🧸"},
    {"cost": 10000, "name": "100 звёзд", "emoji": "⭐"},
    {"cost": 100000, "name": "1000 звёзд", "emoji": "🌟"},
]


async def get_rewards(chat_id: int) -> list[dict]:
    s = await db.settings(chat_id)
    raw = s.get("rewards")
    if not raw:
        return list(DEFAULT_REWARDS)
    try:
        return sorted(json.loads(raw), key=lambda r: r["cost"])
    except (ValueError, TypeError, KeyError):
        return list(DEFAULT_REWARDS)


async def save_rewards(chat_id: int, rewards: list[dict]):
    await db.set_setting(chat_id, "rewards", json.dumps(sorted(rewards, key=lambda r: r["cost"]), ensure_ascii=False))


async def nm(chat_id: int, uid: int, fallback: str | None = None) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None) or fallback)


@router.message(Command("награды", "магазин", "rewards", "shop", prefix="!+/"))
async def cmd_rewards(msg: Message):
    chat_id = msg.chat.id
    rewards = await get_rewards(chat_id)
    bal = await db.coins_total(chat_id, msg.from_user.id)
    lines = []
    for r in rewards:
        ok = "✅" if bal >= r["cost"] else "🔒"
        lines.append(f"{ok} {r.get('emoji', '🎁')} <b>{r['cost']:,}</b> 🪙 → {html.escape(r['name'])}".replace(",", " "))
    nxt = next((r for r in rewards if r["cost"] > bal), None)
    left = f"до «{html.escape(nxt['name'])}» не хватает {nxt['cost'] - bal:,} 🪙".replace(",", " ") if nxt else "ты можешь забрать всё. всё!"
    await reply(msg, "rewards_list", list="\n".join(lines), balance=bal, left=left)


@router.message(Command("обменять", "обмен", "забрать", "redeem", prefix="!+/"))
async def cmd_redeem(msg: Message, bot: Bot):
    chat_id, uid = msg.chat.id, msg.from_user.id
    want = " ".join(split_args(msg)).strip().lower()
    rewards = await get_rewards(chat_id)
    if not want:
        return await reply(msg, "redeem_usage")
    r = next((x for x in rewards if x["name"].lower() == want or want in x["name"].lower() or want == str(x["cost"])), None)
    if not r:
        return await reply(msg, "redeem_unknown", q=html.escape(want))
    bal = await db.coins_total(chat_id, uid)
    if bal < r["cost"]:
        return await reply(msg, "redeem_poor", name=html.escape(r["name"]), cost=r["cost"], balance=bal)
    await db.give_coin(chat_id, 0, uid, f"обмен: {r['name']}", -r["cost"])
    me = await nm(chat_id, uid, msg.from_user.first_name)
    await reply(msg, "redeem_ok", user=me, name=html.escape(r["name"]), cost=r["cost"], left=bal - r["cost"])
    # уведомить владельца: в чат жалоб, а если его нет — упомянуть создателя
    s = await db.settings(chat_id)
    report_chat = int(s.get("report_chat") or 0) or REPORT_CHAT_ID
    note = (f"🎁 заявка на награду в «{html.escape(msg.chat.title or '')}»\n"
            f"кто: {me} (id {uid})\nчто: {r.get('emoji', '🎁')} {html.escape(r['name'])} за {r['cost']} 🪙\n"
            f"коины уже списаны. выдать вручную.")
    try:
        if report_chat:
            await bot.send_message(report_chat, note)
        else:
            admins = await bot.get_chat_administrators(chat_id)
            owner = next((a.user for a in admins if a.status == "creator"), None)
            if owner:
                await msg.answer(f"{mention(owner.id, owner.first_name)}, " + note)
    except Exception:
        pass


@router.message(Command("награда", "reward", prefix="+"))
async def cmd_reward_add(msg: Message, bot: Bot):
    """+награда 5000 мишку [эмодзи]"""
    if await deny(msg, bot, "settings"):
        return
    args = split_args(msg)
    if len(args) < 2 or not args[0].isdigit():
        return await msg.answer("+награда 5000 мишку 🧸 — цена, что дают, (необязательно) эмодзи")
    cost = int(args[0])
    emoji = args[-1] if len(args) > 2 and len(args[-1]) <= 2 and not args[-1].isalnum() else "🎁"
    name = " ".join(args[1:-1] if emoji != "🎁" else args[1:]).strip()
    rewards = [r for r in await get_rewards(msg.chat.id) if r["cost"] != cost]
    rewards.append({"cost": cost, "name": name[:60], "emoji": emoji})
    await save_rewards(msg.chat.id, rewards)
    await reply(msg, "reward_added", cost=cost, name=html.escape(name[:60]))


@router.message(Command("награда", "reward", prefix="-"))
async def cmd_reward_del(msg: Message, bot: Bot):
    """-награда 5000"""
    if await deny(msg, bot, "settings"):
        return
    args = split_args(msg)
    if not args or not args[0].isdigit():
        return await msg.answer("-награда 5000 — убрать награду с этой ценой")
    cost = int(args[0])
    rewards = await get_rewards(msg.chat.id)
    new = [r for r in rewards if r["cost"] != cost]
    if len(new) == len(rewards):
        return await msg.answer("награды с такой ценой нет. !награды — список.")
    await save_rewards(msg.chat.id, new)
    await reply(msg, "reward_removed", cost=cost)
