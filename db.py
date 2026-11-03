import time
import json
import aiosqlite
from config import DB_PATH, DEFAULT_SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER, user_id INTEGER,
    username TEXT, first_name TEXT,
    mass REAL DEFAULT 0, msgs INTEGER DEFAULT 0,
    joined_at INTEGER, last_seen INTEGER,
    day_key TEXT, day_mass REAL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS warns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, user_id INTEGER, by_user INTEGER,
    reason TEXT, weight REAL, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER PRIMARY KEY, data TEXT
);
CREATE TABLE IF NOT EXISTS mood (
    chat_id INTEGER PRIMARY KEY, value REAL DEFAULT 50, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS bot_messages (
    chat_id INTEGER, message_id INTEGER, created_at INTEGER,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS achievements (
    chat_id INTEGER, user_id INTEGER, key TEXT, got_at INTEGER,
    PRIMARY KEY (chat_id, user_id, key)
);
CREATE TABLE IF NOT EXISTS courts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, message_id INTEGER, target_id INTEGER, target_name TEXT,
    kind TEXT, created_at INTEGER, closed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS court_votes (
    court_id INTEGER, user_id INTEGER, choice TEXT, weight REAL,
    PRIMARY KEY (court_id, user_id)
);
CREATE TABLE IF NOT EXISTS digest (
    chat_id INTEGER PRIMARY KEY, last_sent INTEGER
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, user_id INTEGER, text TEXT, fire_at INTEGER
);
CREATE TABLE IF NOT EXISTS tags (
    chat_id INTEGER, name TEXT, file_id TEXT,
    PRIMARY KEY (chat_id, name)
);
CREATE TABLE IF NOT EXISTS custom_phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, key TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS qotd (
    chat_id INTEGER PRIMARY KEY, last_day TEXT
);
"""


class DB:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def init(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    # ---------- settings ----------
    async def settings(self, chat_id: int) -> dict:
        cur = await self.conn.execute("SELECT data FROM settings WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
        data = dict(DEFAULT_SETTINGS)
        if row:
            data.update(json.loads(row["data"]))
        return data

    async def set_setting(self, chat_id: int, key: str, value):
        data = await self.settings(chat_id)
        data[key] = value
        await self.conn.execute(
            "INSERT INTO settings(chat_id,data) VALUES(?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET data=excluded.data",
            (chat_id, json.dumps(data)),
        )
        await self.conn.commit()

    # ---------- users / mass ----------
    async def touch_user(self, chat_id: int, user) -> aiosqlite.Row:
        now = int(time.time())
        await self.conn.execute(
            "INSERT INTO users(chat_id,user_id,username,first_name,joined_at,last_seen) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET "
            "username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen",
            (chat_id, user.id, user.username, user.first_name, now, now),
        )
        await self.conn.commit()
        return await self.user(chat_id, user.id)

    async def user(self, chat_id: int, user_id: int):
        cur = await self.conn.execute(
            "SELECT * FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        return await cur.fetchone()

    async def find_user_by_username(self, chat_id: int, username: str):
        cur = await self.conn.execute(
            "SELECT * FROM users WHERE chat_id=? AND lower(username)=lower(?)",
            (chat_id, username.lstrip("@")),
        )
        return await cur.fetchone()

    async def add_mass(self, chat_id: int, user_id: int, delta: float, daily_cap: float | None = None):
        if daily_cap is not None:
            day = time.strftime("%Y-%m-%d")
            cur = await self.conn.execute(
                "SELECT day_key, day_mass FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)
            )
            row = await cur.fetchone()
            day_mass = row["day_mass"] if row and row["day_key"] == day else 0
            if day_mass >= daily_cap:
                return
            delta = min(delta, daily_cap - day_mass)
            await self.conn.execute(
                "UPDATE users SET day_key=?, day_mass=? WHERE chat_id=? AND user_id=?",
                (day, day_mass + delta, chat_id, user_id),
            )
        await self.conn.execute(
            "UPDATE users SET mass=MAX(0, mass+?) WHERE chat_id=? AND user_id=?",
            (delta, chat_id, user_id),
        )
        await self.conn.commit()

    async def inc_msgs(self, chat_id: int, user_id: int) -> int:
        await self.conn.execute(
            "UPDATE users SET msgs=msgs+1 WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        await self.conn.commit()
        row = await self.user(chat_id, user_id)
        return row["msgs"]

    async def top_users(self, chat_id: int, by: str = "mass", limit: int = 5):
        assert by in ("mass", "msgs")
        cur = await self.conn.execute(
            f"SELECT * FROM users WHERE chat_id=? ORDER BY {by} DESC LIMIT ?", (chat_id, limit)
        )
        return await cur.fetchall()

    async def rank(self, chat_id: int, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*)+1 AS r FROM users WHERE chat_id=? AND mass > "
            "(SELECT mass FROM users WHERE chat_id=? AND user_id=?)",
            (chat_id, chat_id, user_id),
        )
        return (await cur.fetchone())["r"]

    # ---------- warns ----------
    async def add_warn(self, chat_id, user_id, by_user, reason, weight) -> float:
        await self.conn.execute(
            "INSERT INTO warns(chat_id,user_id,by_user,reason,weight,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, user_id, by_user, reason, weight, int(time.time())),
        )
        await self.conn.commit()
        return await self.active_warns(chat_id, user_id)

    async def active_warns(self, chat_id, user_id) -> float:
        s = await self.settings(chat_id)
        since = int(time.time()) - int(s["warn_decay_days"]) * 86400
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(weight),0) AS w FROM warns WHERE chat_id=? AND user_id=? AND created_at>?",
            (chat_id, user_id, since),
        )
        return round((await cur.fetchone())["w"], 2)

    async def list_warns(self, chat_id, user_id):
        s = await self.settings(chat_id)
        since = int(time.time()) - int(s["warn_decay_days"]) * 86400
        cur = await self.conn.execute(
            "SELECT * FROM warns WHERE chat_id=? AND user_id=? AND created_at>? ORDER BY created_at",
            (chat_id, user_id, since),
        )
        return await cur.fetchall()

    async def clear_warns(self, chat_id, user_id):
        await self.conn.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        await self.conn.commit()

    async def most_warned(self, chat_id, days=7):
        since = int(time.time()) - days * 86400
        cur = await self.conn.execute(
            "SELECT u.first_name, u.username, COUNT(*) AS n FROM warns w "
            "JOIN users u ON u.chat_id=w.chat_id AND u.user_id=w.user_id "
            "WHERE w.chat_id=? AND w.created_at>? GROUP BY w.user_id ORDER BY n DESC LIMIT 1",
            (chat_id, since),
        )
        return await cur.fetchone()

    # ---------- mood ----------
    async def mood(self, chat_id: int) -> float:
        now = int(time.time())
        cur = await self.conn.execute("SELECT * FROM mood WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
        if not row:
            await self.conn.execute("INSERT INTO mood VALUES(?,?,?)", (chat_id, 50, now))
            await self.conn.commit()
            return 50
        # настроение падает на 1 за каждый час без внимания
        hours = (now - row["updated_at"]) / 3600
        value = max(0.0, row["value"] - hours)
        return value

    async def bump_mood(self, chat_id: int, delta: float):
        value = await self.mood(chat_id)
        value = max(0.0, min(100.0, value + delta))
        await self.conn.execute(
            "INSERT INTO mood VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET value=?, updated_at=?",
            (chat_id, value, int(time.time()), value, int(time.time())),
        )
        await self.conn.commit()
        return value

    # ---------- bot messages (для реакций) ----------
    async def remember_bot_message(self, chat_id: int, message_id: int):
        now = int(time.time())
        await self.conn.execute(
            "INSERT OR IGNORE INTO bot_messages VALUES(?,?,?)", (chat_id, message_id, now)
        )
        await self.conn.execute("DELETE FROM bot_messages WHERE created_at<?", (now - 7 * 86400,))
        await self.conn.commit()

    async def is_bot_message(self, chat_id: int, message_id: int) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM bot_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)
        )
        return bool(await cur.fetchone())

    # ---------- achievements ----------
    async def give_achievement(self, chat_id, user_id, key) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO achievements VALUES(?,?,?,?)",
            (chat_id, user_id, key, int(time.time())),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def achievements(self, chat_id, user_id):
        cur = await self.conn.execute(
            "SELECT key FROM achievements WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        return [r["key"] for r in await cur.fetchall()]

    # ---------- court ----------
    async def create_court(self, chat_id, message_id, target_id, target_name, kind) -> int:
        cur = await self.conn.execute(
            "INSERT INTO courts(chat_id,message_id,target_id,target_name,kind,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, message_id, target_id, target_name, kind, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def court(self, court_id):
        cur = await self.conn.execute("SELECT * FROM courts WHERE id=?", (court_id,))
        return await cur.fetchone()

    async def vote(self, court_id, user_id, choice, weight):
        await self.conn.execute(
            "INSERT INTO court_votes VALUES(?,?,?,?) ON CONFLICT(court_id,user_id) "
            "DO UPDATE SET choice=excluded.choice, weight=excluded.weight",
            (court_id, user_id, choice, weight),
        )
        await self.conn.commit()

    async def tally(self, court_id) -> dict:
        cur = await self.conn.execute(
            "SELECT choice, SUM(weight) AS w, COUNT(*) AS n FROM court_votes WHERE court_id=? GROUP BY choice",
            (court_id,),
        )
        res = {"yes": (0, 0), "no": (0, 0)}
        for r in await cur.fetchall():
            res[r["choice"]] = (round(r["w"], 1), r["n"])
        return res

    async def court_voters(self, court_id, choice):
        cur = await self.conn.execute(
            "SELECT user_id FROM court_votes WHERE court_id=? AND choice=?", (court_id, choice)
        )
        return [r["user_id"] for r in await cur.fetchall()]

    async def close_court(self, court_id):
        await self.conn.execute("UPDATE courts SET closed=1 WHERE id=?", (court_id,))
        await self.conn.commit()

    # ---------- digest ----------
    async def digest_due(self, chat_id: int, period: int = 7 * 86400) -> bool:
        cur = await self.conn.execute("SELECT last_sent FROM digest WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
        return not row or int(time.time()) - row["last_sent"] >= period

    async def mark_digest(self, chat_id: int):
        await self.conn.execute(
            "INSERT INTO digest VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET last_sent=excluded.last_sent",
            (chat_id, int(time.time())),
        )
        await self.conn.commit()

    async def all_chats(self):
        cur = await self.conn.execute("SELECT DISTINCT chat_id FROM users")
        return [r["chat_id"] for r in await cur.fetchall()]

    # ---------- reminders ----------
    async def add_reminder(self, chat_id, user_id, text, fire_at) -> int:
        cur = await self.conn.execute(
            "INSERT INTO reminders(chat_id,user_id,text,fire_at) VALUES(?,?,?,?)",
            (chat_id, user_id, text, fire_at),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def due_reminders(self, now: int):
        cur = await self.conn.execute(
            "SELECT * FROM reminders WHERE fire_at<=? ORDER BY fire_at", (now,)
        )
        return await cur.fetchall()

    async def delete_reminder(self, rem_id: int):
        await self.conn.execute("DELETE FROM reminders WHERE id=?", (rem_id,))
        await self.conn.commit()

    # ---------- tags (стикеры чата) ----------
    async def set_tag(self, chat_id, name, file_id):
        await self.conn.execute(
            "INSERT INTO tags VALUES(?,?,?) ON CONFLICT(chat_id,name) DO UPDATE SET file_id=excluded.file_id",
            (chat_id, name, file_id),
        )
        await self.conn.commit()

    async def get_tag(self, chat_id, name):
        cur = await self.conn.execute(
            "SELECT file_id FROM tags WHERE chat_id=? AND name=?", (chat_id, name)
        )
        row = await cur.fetchone()
        return row["file_id"] if row else None

    async def del_tag(self, chat_id, name) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM tags WHERE chat_id=? AND name=?", (chat_id, name)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def list_tags(self, chat_id):
        cur = await self.conn.execute(
            "SELECT name FROM tags WHERE chat_id=? ORDER BY name", (chat_id,)
        )
        return [r["name"] for r in await cur.fetchall()]

    # ---------- вопрос дня ----------
    async def qotd_due(self, chat_id: int, day: str) -> bool:
        cur = await self.conn.execute("SELECT last_day FROM qotd WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
        return not row or row["last_day"] < day

    async def mark_qotd(self, chat_id: int, day: str):
        await self.conn.execute(
            "INSERT INTO qotd VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET last_day=excluded.last_day",
            (chat_id, day),
        )
        await self.conn.commit()


    # ---------- пользовательские реплики ----------
    async def all_custom_phrases(self) -> dict:
        cur = await self.conn.execute("SELECT chat_id, key, text FROM custom_phrases ORDER BY id")
        res: dict = {}
        for r in await cur.fetchall():
            res.setdefault(r["chat_id"], {}).setdefault(r["key"], []).append(r["text"])
        return res

    async def add_custom_phrase(self, chat_id, key, text):
        await self.conn.execute("INSERT INTO custom_phrases(chat_id,key,text) VALUES(?,?,?)", (chat_id, key, text))
        await self.conn.commit()

    async def custom_phrases(self, chat_id, key) -> list[str]:
        cur = await self.conn.execute(
            "SELECT text FROM custom_phrases WHERE chat_id=? AND key=? ORDER BY id", (chat_id, key))
        return [r["text"] for r in await cur.fetchall()]

    async def del_custom_phrase(self, chat_id, key, index: int) -> bool:
        cur = await self.conn.execute(
            "SELECT id FROM custom_phrases WHERE chat_id=? AND key=? ORDER BY id", (chat_id, key))
        ids = [r["id"] for r in await cur.fetchall()]
        if not 0 <= index < len(ids):
            return False
        await self.conn.execute("DELETE FROM custom_phrases WHERE id=?", (ids[index],))
        await self.conn.commit()
        return True

    async def reset_custom_phrases(self, chat_id, key):
        await self.conn.execute("DELETE FROM custom_phrases WHERE chat_id=? AND key=?", (chat_id, key))
        await self.conn.commit()


db = DB()
