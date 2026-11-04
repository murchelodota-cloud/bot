"""/phrase — редактирование реплик Саррочки прямо из Telegram (для админов).

/phrase                      — справка и список ключей
/phrase list warn            — текущие реплики для ключа
/phrase add warn текст       — добавить свою (свои реплики ЗАМЕНЯЮТ стандартные для этого ключа)
/phrase del warn 2           — удалить свою №2
/phrase reset warn           — вернуть стандартные
"""
import html
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from db import db
import phrases
from phrases import PHRASES, KEY_HELP
from utils import reply, deny, style

router = Router()
MAX_LEN = 400


def _usage() -> str:
    keys = "\n".join(f"• <code>{k}</code> — {html.escape(v)}" for k, v in KEY_HELP.items())
    return (
        "мои реплики можно переписать. в скобках — что подставляется (пиши в фигурных скобках, как есть).\n\n"
        "<code>/phrase list warn</code> — показать текущие\n"
        "<code>/phrase add warn {user}, ну ты чего ({n}/{limit})</code> — добавить свою\n"
        "<code>/phrase del warn 2</code> — удалить свою №2\n"
        "<code>/phrase reset warn</code> — вернуть мои родные\n\n"
        "как только добавишь хоть одну свою — для этого ключа звучат только твои. "
        "reset — и я снова как прежде. я не обижусь. почти.\n\n"
        f"ключи:\n{keys}"
    )


@router.message(Command("phrase", "phrases", "реплика", "реплики", prefix="/!"))
async def cmd_phrase(msg: Message, bot: Bot):
    if await deny(msg, bot, "phrase"):
        return
    parts = (msg.text or "").split(maxsplit=3)
    if len(parts) < 3:
        return await msg.answer(_usage(), disable_web_page_preview=True)
    action, key = parts[1].lower(), parts[2].lower()
    if key not in PHRASES["pickme"]:
        return await msg.answer(f"не знаю ключ <code>{html.escape(key)}</code>. /phrase — список.")
    chat_id = msg.chat.id

    if action == "list":
        custom = await db.custom_phrases(chat_id, key)
        if custom:
            lines = [f"{i + 1}. {html.escape(t)}" for i, t in enumerate(custom)]
            head = f"твои реплики для <code>{key}</code>:"
        else:
            st = await style(chat_id)
            bank = PHRASES.get(st, PHRASES["pickme"]).get(key) or PHRASES["pickme"][key]
            lines = [f"{i + 1}. {html.escape(t)}" for i, t in enumerate(bank)]
            head = f"мои родные реплики для <code>{key}</code> (своих нет):"
        text = head + "\n" + "\n".join(lines)
        return await msg.answer(text[:4000], disable_web_page_preview=True)

    if action == "add":
        text = parts[3].strip() if len(parts) > 3 else ""
        if not text:
            return await msg.answer("а текст? /phrase add ключ текст")
        if len(text) > MAX_LEN:
            return await msg.answer(f"длинно. до {MAX_LEN} символов, я не оратор.")
        # проверяем, что подстановки корректные
        try:
            text.format(**{k: "x" for k in _placeholders(key)})
        except (KeyError, IndexError, ValueError) as e:
            return await msg.answer(
                f"что-то не так с фигурными скобками ({html.escape(str(e))}). "
                f"для <code>{key}</code> можно: {html.escape(KEY_HELP.get(key, ''))}")
        await db.add_custom_phrase(chat_id, key, text)
        phrases.CUSTOM.setdefault(chat_id, {})[key] = await db.custom_phrases(chat_id, key)
        n = len(phrases.CUSTOM[chat_id][key])
        return await msg.answer(f"записала. теперь у <code>{key}</code> {n} твоих реплик(и). мои родные молчат, пока не сделаешь reset.")

    if action == "del":
        try:
            idx = int(parts[3]) - 1
        except (IndexError, ValueError):
            return await msg.answer("номер? /phrase del ключ 2")
        if not await db.del_custom_phrase(chat_id, key, idx):
            return await msg.answer("нет такой. /phrase list ключ — посмотри номера.")
        rest = await db.custom_phrases(chat_id, key)
        if rest:
            phrases.CUSTOM.setdefault(chat_id, {})[key] = rest
        else:
            phrases.CUSTOM.get(chat_id, {}).pop(key, None)
        return await msg.answer(f"удалила. осталось {len(rest)}." + ("" if rest else " твоих больше нет — снова говорю своими."))

    if action == "reset":
        await db.reset_custom_phrases(chat_id, key)
        phrases.CUSTOM.get(chat_id, {}).pop(key, None)
        return await msg.answer(f"<code>{key}</code> — вернула свои родные реплики. спасибо, что вернул меня. никто не возвращает.")

    await msg.answer(_usage(), disable_web_page_preview=True)


def _placeholders(key: str) -> set[str]:
    """Все плейсхолдеры, которые встречаются в стандартных репликах ключа (во всех стилях)."""
    import re
    names: set[str] = set()
    for bank in PHRASES.values():
        for t in bank.get(key, []):
            names.update(re.findall(r"{(\w+)}", t))
    return names
