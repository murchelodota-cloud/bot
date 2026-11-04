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
CREATE TABLE IF NOT EXISTS marriages (
    chat_id INTEGER, a INTEGER, b INTEGER, since INTEGER
);
CREATE TABLE IF NOT EXISTS crushes (
    chat_id INTEGER, user_id INTEGER, target_id INTEGER, since INTEGER,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS sarra_love (
    chat_id INTEGER, user_id INTEGER, n INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS coin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, from_user INTEGER, to_user INTEGER, n INTEGER, note TEXT, ts INTEGER, month TEXT
);
CREATE TABLE IF NOT EXISTS badwords (
    chat_id INTEGER, word TEXT,
    PRIMARY KEY (chat_id, word)
);
CREATE TABLE IF NOT EXISTS daily_activity (
    chat_id INTEGER, user_id INTEGER, day TEXT, n INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS music_cache (
    key TEXT PRIMARY KEY, file_id TEXT, title TEXT, artist TEXT, duration INTEGER, hits INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ranks (
    chat_id INTEGER, user_id INTEGER, rank INTEGER, by_user INTEGER, set_at INTEGER,
    PRIMARY KEY (chat_id, user_id)
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
        cur = await self.conn.execute("PRAGMA table_info(users)")
        cols = [r["name"] for r in await cur.fetchall()]
        if "nick" not in cols:
            await self.conn.execute("ALTER TABLE users ADD COLUMN nick TEXT")
        if "bio" not in cols:
            await self.conn.execute("ALTER TABLE users ADD COLUMN bio TEXT")
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


    # ---------- ранги ----------
    async def rank(self, chat_id, user_id) -> int:
        cur = await self.conn.execute("SELECT rank FROM ranks WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        row = await cur.fetchone()
        return row["rank"] if row else 0

    async def set_rank(self, chat_id, user_id, rank: int, by_user: int):
        if rank <= 0:
            await self.conn.execute("DELETE FROM ranks WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        else:
            await self.conn.execute(
                "INSERT INTO ranks VALUES(?,?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET rank=excluded.rank, by_user=excluded.by_user, set_at=excluded.set_at",
                (chat_id, user_id, rank, by_user, int(time.time())))
        await self.conn.commit()

    async def list_ranks(self, chat_id):
        cur = await self.conn.execute(
            "SELECT r.user_id, r.rank, u.first_name FROM ranks r LEFT JOIN users u ON u.chat_id=r.chat_id AND u.user_id=r.user_id "
            "WHERE r.chat_id=? ORDER BY r.rank DESC, u.first_name", (chat_id,))
        return await cur.fetchall()


    # ---------- ники ----------
    async def set_nick(self, chat_id, user_id, nick):
        await self.conn.execute("UPDATE users SET nick=? WHERE chat_id=? AND user_id=?", (nick, chat_id, user_id))
        await self.conn.commit()

    # ---------- браки ----------
    async def partner(self, chat_id, user_id):
        cur = await self.conn.execute(
            "SELECT a, b FROM marriages WHERE chat_id=? AND (a=? OR b=?)", (chat_id, user_id, user_id))
        row = await cur.fetchone()
        if not row:
            return None
        return row["b"] if row["a"] == user_id else row["a"]

    async def marriage(self, chat_id, user_id):
        cur = await self.conn.execute(
            "SELECT * FROM marriages WHERE chat_id=? AND (a=? OR b=?)", (chat_id, user_id, user_id))
        return await cur.fetchone()

    async def marry(self, chat_id, a, b):
        await self.conn.execute("INSERT INTO marriages VALUES(?,?,?,?)", (chat_id, a, b, int(time.time())))
        await self.conn.commit()

    async def divorce(self, chat_id, user_id):
        await self.conn.execute("DELETE FROM marriages WHERE chat_id=? AND (a=? OR b=?)", (chat_id, user_id, user_id))
        await self.conn.commit()

    async def marriages(self, chat_id):
        cur = await self.conn.execute("SELECT * FROM marriages WHERE chat_id=? ORDER BY since", (chat_id,))
        return await cur.fetchall()

    # ---------- влюблённости ----------
    async def crush(self, chat_id, user_id):
        cur = await self.conn.execute("SELECT target_id FROM crushes WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        row = await cur.fetchone()
        return row["target_id"] if row else None

    async def set_crush(self, chat_id, user_id, target_id):
        if target_id is None:
            await self.conn.execute("DELETE FROM crushes WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        else:
            await self.conn.execute(
                "INSERT INTO crushes VALUES(?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET target_id=excluded.target_id, since=excluded.since",
                (chat_id, user_id, target_id, int(time.time())))
        await self.conn.commit()

    async def love_sarra(self, chat_id, user_id) -> int:
        await self.conn.execute(
            "INSERT INTO sarra_love VALUES(?,?,1) ON CONFLICT(chat_id,user_id) DO UPDATE SET n=n+1", (chat_id, user_id))
        await self.conn.commit()
        cur = await self.conn.execute("SELECT n FROM sarra_love WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        return (await cur.fetchone())["n"]


    # ---------- сарра-коины ----------
    async def give_coin(self, chat_id, from_user, to_user, note="", n=1):
        now = int(time.time())
        await self.conn.execute(
            "INSERT INTO coin_log(chat_id,from_user,to_user,n,note,ts,month) VALUES(?,?,?,?,?,?,?)",
            (chat_id, from_user, to_user, n, note, now, time.strftime("%Y-%m", time.localtime(now))))
        await self.conn.commit()

    async def coins_given_today(self, chat_id, from_user) -> int:
        since = int(time.time()) - 86400
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(n),0) AS s FROM coin_log WHERE chat_id=? AND from_user=? AND ts>?", (chat_id, from_user, since))
        return (await cur.fetchone())["s"]

    async def coins_total(self, chat_id, user_id) -> int:
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(n),0) AS s FROM coin_log WHERE chat_id=? AND to_user=?", (chat_id, user_id))
        return (await cur.fetchone())["s"]

    async def coins_month(self, chat_id, user_id, month) -> int:
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(n),0) AS s FROM coin_log WHERE chat_id=? AND to_user=? AND month=?", (chat_id, user_id, month))
        return (await cur.fetchone())["s"]

    async def coins_top(self, chat_id, month, limit=10):
        cur = await self.conn.execute(
            "SELECT to_user AS user_id, SUM(n) AS n FROM coin_log WHERE chat_id=? AND month=? "
            "GROUP BY to_user ORDER BY n DESC LIMIT ?", (chat_id, month, limit))
        return await cur.fetchall()

    async def coins_rank(self, chat_id, user_id, month) -> int:
        rows = await self.coins_top(chat_id, month, 1000)
        for i, r in enumerate(rows):
            if r["user_id"] == user_id:
                return i + 1
        return 0


    # ---------- запретные слова ----------
    async def badwords(self, chat_id) -> list[str]:
        cur = await self.conn.execute("SELECT word FROM badwords WHERE chat_id=? ORDER BY word", (chat_id,))
        return [r["word"] for r in await cur.fetchall()]

    async def add_badword(self, chat_id, word) -> bool:
        cur = await self.conn.execute("INSERT OR IGNORE INTO badwords VALUES(?,?)", (chat_id, word))
        await self.conn.commit()
        return cur.rowcount > 0

    async def del_badword(self, chat_id, word) -> bool:
        cur = await self.conn.execute("DELETE FROM badwords WHERE chat_id=? AND word=?", (chat_id, word))
        await self.conn.commit()
        return cur.rowcount > 0

    async def clear_badwords(self, chat_id):
        await self.conn.execute("DELETE FROM badwords WHERE chat_id=?", (chat_id,))
        await self.conn.commit()


    # ---------- активность по дням / описание ----------
    async def bump_daily(self, chat_id, user_id):
        day = time.strftime("%Y-%m-%d")
        await self.conn.execute(
            "INSERT INTO daily_activity VALUES(?,?,?,1) ON CONFLICT(chat_id,user_id,day) DO UPDATE SET n=n+1",
            (chat_id, user_id, day))
        await self.conn.commit()

    async def activity(self, chat_id, user_id) -> dict:
        now = time.time()
        res = {}
        for key, days in (("day", 1), ("week", 7), ("month", 30)):
            since = time.strftime("%Y-%m-%d", time.localtime(now - (days - 1) * 86400))
            cur = await self.conn.execute(
                "SELECT COALESCE(SUM(n),0) AS s FROM daily_activity WHERE chat_id=? AND user_id=? AND day>=?",
                (chat_id, user_id, since))
            res[key] = (await cur.fetchone())["s"]
        return res

    async def activity_series(self, chat_id, user_id, days=365) -> dict:
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
        cur = await self.conn.execute(
            "SELECT day, n FROM daily_activity WHERE chat_id=? AND user_id=? AND day>=?", (chat_id, user_id, since))
        return {r["day"]: r["n"] for r in await cur.fetchall()}

    async def set_bio(self, chat_id, user_id, bio):
        await self.conn.execute("UPDATE users SET bio=? WHERE chat_id=? AND user_id=?", (bio, chat_id, user_id))
        await self.conn.commit()


    # ---------- кэш музыки ----------
    async def music_get(self, key: str):
        cur = await self.conn.execute("SELECT * FROM music_cache WHERE key=?", (key,))
        row = await cur.fetchone()
        if row:
            await self.conn.execute("UPDATE music_cache SET hits=hits+1 WHERE key=?", (key,))
            await self.conn.commit()
        return row

    async def music_put(self, key: str, file_id: str, title: str, artist: str, duration: int):
        await self.conn.execute(
            "INSERT INTO music_cache(key,file_id,title,artist,duration) VALUES(?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET file_id=excluded.file_id",
            (key, file_id, title, artist, duration))
        await self.conn.commit()


db = DB()
