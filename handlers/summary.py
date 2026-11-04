"""Сводки: !сводка1 (час), !сводка24 (сутки), !сводка7 (неделя) — что происходило в чате."""
import html
import time
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from db import db
from utils import reply, mention, split_args

router = Router()


async def nm(chat_id: int, uid: int) -> str:
    row = await db.user(chat_id, uid)
    return mention(uid, ((row["nick"] or row["first_name"]) if row else None))


async def build_summary(chat_id: int, hours: int) -> dict:
    since = int(time.time()) - hours * 3600
    st = await db.summary_stats(chat_id, since)
    top = await db.summary_top(chat_id, since, 3)
    top_lines = [f"{['🥇', '🥈', '🥉'][i]} {await nm(chat_id, r['user_id'])} — {r['n']}" for i, r in enumerate(top)]
    return {**st, "top": "\n".join(top_lines) or "—"}


@router.message(Command("сводка", "сводка1", "сводка24", "сводка7", "summary", prefix="!+/"))
async def cmd_summary(msg: Message):
    cmd = (msg.text or "").split()[0][1:].lower()
    args = split_args(msg)
    num = cmd.replace("сводка", "").replace("summary", "") or (args[0] if args else "24")
    hours = {"1": 1, "24": 24, "7": 168, "неделя": 168, "день": 24, "час": 1, "сутки": 24}.get(num, 24)
    label = {1: "за час", 24: "за сутки", 168: "за неделю"}[hours]
    d = await build_summary(msg.chat.id, hours)
    await reply(msg, "summary", period=label, **d)
