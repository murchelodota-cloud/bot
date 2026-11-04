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
    "max_spouses": 4,         # сколько браков может быть у одного человека
    "talaq_men_only": 1,      # 1 — тройной «развод» работает только если пол не «ж»
    "report_chat": 0,         # id чата для жалоб /report (0 — брать REPORT_CHAT_ID из .env)
    "matfilter": 1,           # 1/0 — фильтр мата
    "mat_soft": 1,            # 1 — мягкий режим (предупреждает), 0 — удаляет
    "mat_admins": 1,          # 1 — админам тоже делать замечание за мат (без удаления)
    "dupfilter": 1,           # 1/0 — резать одинаковые сообщения подряд
    "qotd": 1,                # 1/0 — вопрос дня
    "welcome": 1,             # 1/0 — приветствовать новичков
    "qotd_hour": 11,
    "chatter": 1,             # 1/0 — Саррочка иногда встревает в разговор сама
    "chatter_every": 30,
    "play_natural": 1,
    "coins_per_500": 100,     # коинов за каждые 500 сообщений
    "month_bonus_mass": 100,  # бонус массы победителю месяца (2-е место — половина, 3-е — четверть)
    "month_prize": "",        # приз победителю месяца (+приз текст): nft, gram, премиум...        # 1/0 — реагировать на «включи …» без команды      # в среднем раз в N сообщений          # час, после которого можно слать вопрос дня
}

# Масса
MASS_PER_MESSAGE = 1
MASS_DAILY_CAP = 40           # не больше массы в день за сообщения
MASS_PER_REACTION = 3         # реакция на твоё сообщение
MASS_PER_BOT_REACTION = 6     # реакция на сообщение Саррочки (она это ОЧЕНЬ ценит)
MASS_PER_WARN = -15
MASS_PER_THANKS = 2

if not BOT_TOKEN:
    raise SystemExit("Заполни BOT_TOKEN в .env (см. .env.example)")

# Языковая модель для связных сводок (любой OpenAI-совместимый API). Без ключа — сводка по ключевым словам.
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Музыка
MUSIC_MAX_MINUTES = 12            # треки длиннее не качаем
MUSIC_TIMEOUT = 120               # секунд на поиск+скачивание, потом сдаёмся
MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", "music_tmp")
