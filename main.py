import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config import BOT_TOKEN
from db import db
from handlers import routers
from handlers.profile import MassMiddleware
from handlers.common import send_digest
import phrases
from handlers.extras import fire_due_reminders, send_qotd, qotd_due

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def digest_loop(bot: Bot):
    """Раз в час проверяет, не пора ли слать недельный дайджест и вопрос дня."""
    while True:
        await asyncio.sleep(3600)
        for chat_id in await db.all_chats():
            try:
                if await db.digest_due(chat_id):
                    await send_digest(bot, chat_id)
                if await qotd_due(bot, chat_id):
                    await send_qotd(bot, chat_id)
            except Exception as e:  # чат мог удалить бота
                logging.warning("digest/qotd %s: %s", chat_id, e)


async def reminder_loop(bot: Bot):
    """Каждые 30 секунд шлёт созревшие напоминания."""
    while True:
        await asyncio.sleep(30)
        try:
            await fire_due_reminders(bot)
        except Exception as e:
            logging.warning("reminders: %s", e)


async def main():
    await db.init()
    phrases.CUSTOM.update(await db.all_custom_phrases())
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.outer_middleware(MassMiddleware())
    dp.include_routers(*routers)
    asyncio.create_task(digest_loop(bot))
    asyncio.create_task(reminder_loop(bot))
    # message_reaction и chat_member не приходят по умолчанию — просим явно
    allowed = dp.resolve_used_update_types() + ["message_reaction", "chat_member"]
    logging.info("Саррочка запускается. никто не заметит, но запускается.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=list(set(allowed)))


if __name__ == "__main__":
    asyncio.run(main())
