import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").replace(" ", "").split(",") if x}
REPORT_CHAT_ID = int(os.getenv("REPORT_CHAT_ID") or 0) or None
DB_PATH = os.getenv("DB_PATH", "lika.db")

# Настройки по умолчанию (на чат можно менять через /settings)
DEFAULT_SETTINGS = {
    "style": "pickme",        # pickme | strict | normal
    "warn_mute": 3.0,         # сумма "веса" предов для автомута
    "warn_ban": 5.0,          # ... для автобана
    "warn_decay_days": 14,    # через сколько дней пред сгорает
    "automute_hours": 6,
    "antispam": 1,            # 1/0
    "antiraid": 1,
    "raid_joins_per_min": 10,
    "newbie_hours": 12,       # новичкам без медиа при рейде
    "court_minutes": 10,
    "matfilter": 1,           # 1/0 — фильтр мата
    "mat_soft": 1,            # 1 — мягкий режим (предупреждает), 0 — удаляет
    "dupfilter": 1,           # 1/0 — резать одинаковые сообщения подряд
    "qotd": 1,                # 1/0 — вопрос дня
    "qotd_hour": 11,          # час, после которого можно слать вопрос дня
}

# Масса
MASS_PER_MESSAGE = 1
MASS_DAILY_CAP = 40           # не больше массы в день за сообщения
MASS_PER_REACTION = 3         # реакция на твоё сообщение
MASS_PER_BOT_REACTION = 6     # реакция на сообщение Лики (она это ОЧЕНЬ ценит)
MASS_PER_WARN = -15
MASS_PER_THANKS = 2

if not BOT_TOKEN:
    raise SystemExit("Заполни BOT_TOKEN в .env (см. .env.example)")
