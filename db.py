"""
Слой работы с базой данных (SQLite через aiosqlite).

Для одного чата SQLite более чем достаточен и не требует поднимать
Postgres/Redis, как это предусмотрено в "большом" ТЗ для 10 000+ чатов.
Если в будущем бота нужно будет масштабировать на много чатов —
слой db.py можно заменить на репозитории поверх Postgres, не трогая
остальной код (вся работа с данными идёт только через функции этого модуля).
"""
import time
import json
import aiosqlite
from typing import Optional

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    joined_at INTEGER,
    message_count INTEGER DEFAULT 0,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0,
    warns_count INTEGER DEFAULT 0,
    is_muted INTEGER DEFAULT 0,
    muted_until INTEGER DEFAULT 0,
    is_banned INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    reason TEXT,
    moderator_id INTEGER,
    created_at INTEGER,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    action TEXT,
    user_id INTEGER,
    moderator_id INTEGER,
    details TEXT
);

CREATE TABLE IF NOT EXISTS banned_words (
    word TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS ad_domains (
    domain TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS roles (
    user_id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    granted_by INTEGER,
    granted_at INTEGER
);

CREATE TABLE IF NOT EXISTS reputation (
    user_id INTEGER PRIMARY KEY,
    score INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reputation_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voter_id INTEGER,
    target_id INTEGER,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS pending_captcha (
    user_id INTEGER PRIMARY KEY,
    join_message_id INTEGER,
    correct_answer TEXT,
    deadline INTEGER
);
"""

_conn: Optional[aiosqlite.Connection] = None


async def init_db():
    global _conn
    _conn = await aiosqlite.connect(config.DB_PATH)
    await _conn.executescript(_SCHEMA)
    await _conn.commit()

    # Заполняем настройки по умолчанию, если их ещё нет
    for key, value in config.DEFAULT_SETTINGS.items():
        await _conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )

    for word in config.DEFAULT_BANNED_WORDS:
        await _conn.execute("INSERT OR IGNORE INTO banned_words (word) VALUES (?)", (word.lower(),))

    for domain in config.DEFAULT_AD_DOMAINS:
        await _conn.execute("INSERT OR IGNORE INTO ad_domains (domain) VALUES (?)", (domain.lower(),))

    await _conn.commit()


async def close_db():
    if _conn:
        await _conn.close()


# ---------- SETTINGS ----------

async def get_setting(key: str) -> Optional[str]:
    async with _conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
        return row[0] if row else None


async def set_setting(key: str, value: str):
    await _conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await _conn.commit()


async def get_bool_setting(key: str) -> bool:
    val = await get_setting(key)
    return val == "1"


async def get_all_settings() -> dict:
    async with _conn.execute("SELECT key, value FROM settings") as cur:
        rows = await cur.fetchall()
        return {k: v for k, v in rows}


async def get_punishment_ladder() -> list:
    raw = await get_setting("punishment_ladder")
    return [int(x) for x in raw.split(",") if x.strip()]


# ---------- MEMBERS ----------

async def ensure_member(user_id: int, username: str = None, full_name: str = None):
    async with _conn.execute("SELECT user_id FROM members WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        await _conn.execute(
            "INSERT INTO members (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, int(time.time())),
        )
        await _conn.commit()
    else:
        await _conn.execute(
            "UPDATE members SET username = ?, full_name = ? WHERE user_id = ?",
            (username, full_name, user_id),
        )
        await _conn.commit()


async def get_member(user_id: int) -> Optional[aiosqlite.Row]:
    _conn.row_factory = aiosqlite.Row
    async with _conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)) as cur:
        return await cur.fetchone()


async def bump_message_count(user_id: int, xp_gain: int = 1):
    await _conn.execute(
        "UPDATE members SET message_count = message_count + 1, xp = xp + ? WHERE user_id = ?",
        (xp_gain, user_id),
    )
    await _conn.commit()
    member = await get_member(user_id)
    if member:
        new_level = _level_from_xp(member["xp"])
        if new_level != member["level"]:
            await _conn.execute("UPDATE members SET level = ? WHERE user_id = ?", (new_level, user_id))
            await _conn.commit()
            return new_level  # уровень повысился/понизился -> вернуть новый уровень
    return None


def _level_from_xp(xp: int) -> int:
    # Простая прогрессия: level = floor(sqrt(xp / 10))
    level = 0
    threshold = 10
    remaining = xp
    while remaining >= threshold:
        remaining -= threshold
        level += 1
        threshold += 10
    return level


async def set_muted(user_id: int, until_ts: int):
    await _conn.execute(
        "UPDATE members SET is_muted = 1, muted_until = ? WHERE user_id = ?", (until_ts, user_id)
    )
    await _conn.commit()


async def clear_muted(user_id: int):
    await _conn.execute(
        "UPDATE members SET is_muted = 0, muted_until = 0 WHERE user_id = ?", (user_id,)
    )
    await _conn.commit()


async def set_banned(user_id: int, banned: bool):
    await _conn.execute("UPDATE members SET is_banned = ? WHERE user_id = ?", (1 if banned else 0, user_id))
    await _conn.commit()


async def list_muted(limit: int = 8, offset: int = 0):
    _conn.row_factory = aiosqlite.Row
    async with _conn.execute(
        "SELECT * FROM members WHERE is_muted = 1 ORDER BY muted_until DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ) as cur:
        return await cur.fetchall()


async def count_muted() -> int:
    async with _conn.execute("SELECT COUNT(*) FROM members WHERE is_muted = 1") as cur:
        row = await cur.fetchone()
        return row[0]


async def list_banned(limit: int = 8, offset: int = 0):
    _conn.row_factory = aiosqlite.Row
    async with _conn.execute(
        "SELECT * FROM members WHERE is_banned = 1 ORDER BY user_id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ) as cur:
        return await cur.fetchall()


async def count_banned() -> int:
    async with _conn.execute("SELECT COUNT(*) FROM members WHERE is_banned = 1") as cur:
        row = await cur.fetchone()
        return row[0]


# ---------- WARNINGS ----------

async def add_warning(user_id: int, reason: str, moderator_id: int) -> int:
    await _conn.execute(
        "INSERT INTO warnings (user_id, reason, moderator_id, created_at, active) VALUES (?, ?, ?, ?, 1)",
        (user_id, reason, moderator_id, int(time.time())),
    )
    await _conn.execute(
        "UPDATE members SET warns_count = warns_count + 1 WHERE user_id = ?", (user_id,)
    )
    await _conn.commit()
    async with _conn.execute(
        "SELECT COUNT(*) FROM warnings WHERE user_id = ? AND active = 1", (user_id,)
    ) as cur:
        row = await cur.fetchone()
        return row[0]


async def count_active_warnings(user_id: int) -> int:
    async with _conn.execute(
        "SELECT COUNT(*) FROM warnings WHERE user_id = ? AND active = 1", (user_id,)
    ) as cur:
        row = await cur.fetchone()
        return row[0]


async def clear_warnings(user_id: int):
    await _conn.execute("UPDATE warnings SET active = 0 WHERE user_id = ?", (user_id,))
    await _conn.execute("UPDATE members SET warns_count = 0 WHERE user_id = ?", (user_id,))
    await _conn.commit()


# ---------- LOGS ----------

async def add_log(action: str, user_id: int, moderator_id: Optional[int], details: str = ""):
    await _conn.execute(
        "INSERT INTO logs (ts, action, user_id, moderator_id, details) VALUES (?, ?, ?, ?, ?)",
        (int(time.time()), action, user_id, moderator_id, details),
    )
    await _conn.commit()


async def get_logs(limit: int = 10, offset: int = 0):
    _conn.row_factory = aiosqlite.Row
    async with _conn.execute(
        "SELECT * FROM logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    ) as cur:
        return await cur.fetchall()


async def count_logs() -> int:
    async with _conn.execute("SELECT COUNT(*) FROM logs") as cur:
        row = await cur.fetchone()
        return row[0]


# ---------- BANNED WORDS / AD DOMAINS ----------

async def get_banned_words() -> list:
    async with _conn.execute("SELECT word FROM banned_words") as cur:
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def add_banned_word(word: str):
    await _conn.execute("INSERT OR IGNORE INTO banned_words (word) VALUES (?)", (word.lower(),))
    await _conn.commit()


async def remove_banned_word(word: str):
    await _conn.execute("DELETE FROM banned_words WHERE word = ?", (word.lower(),))
    await _conn.commit()


async def get_ad_domains() -> list:
    async with _conn.execute("SELECT domain FROM ad_domains") as cur:
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def add_ad_domain(domain: str):
    await _conn.execute("INSERT OR IGNORE INTO ad_domains (domain) VALUES (?)", (domain.lower(),))
    await _conn.commit()


async def remove_ad_domain(domain: str):
    await _conn.execute("DELETE FROM ad_domains WHERE domain = ?", (domain.lower(),))
    await _conn.commit()


# ---------- ROLES (кастомная роль "модератор" поверх Telegram-прав) ----------

async def grant_role(user_id: int, role: str, granted_by: int):
    await _conn.execute(
        "INSERT INTO roles (user_id, role, granted_by, granted_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET role = excluded.role, granted_by = excluded.granted_by, granted_at = excluded.granted_at",
        (user_id, role, granted_by, int(time.time())),
    )
    await _conn.commit()


async def revoke_role(user_id: int):
    await _conn.execute("DELETE FROM roles WHERE user_id = ?", (user_id,))
    await _conn.commit()


async def get_role(user_id: int) -> Optional[str]:
    async with _conn.execute("SELECT role FROM roles WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
        return row[0] if row else None


async def list_roles(role: str = None):
    _conn.row_factory = aiosqlite.Row
    if role:
        async with _conn.execute("SELECT * FROM roles WHERE role = ?", (role,)) as cur:
            return await cur.fetchall()
    async with _conn.execute("SELECT * FROM roles") as cur:
        return await cur.fetchall()


# ---------- REPUTATION ----------

async def add_reputation(target_id: int, voter_id: int, cooldown_sec: int) -> tuple[bool, int]:
    """Возвращает (успех, текущий_счёт). Если voter уже голосовал за target в течение cooldown — успех False."""
    now = int(time.time())
    async with _conn.execute(
        "SELECT created_at FROM reputation_votes WHERE voter_id = ? AND target_id = ? ORDER BY created_at DESC LIMIT 1",
        (voter_id, target_id),
    ) as cur:
        row = await cur.fetchone()
    if row and now - row[0] < cooldown_sec:
        score = await get_reputation(target_id)
        return False, score

    await _conn.execute(
        "INSERT INTO reputation_votes (voter_id, target_id, created_at) VALUES (?, ?, ?)",
        (voter_id, target_id, now),
    )
    await _conn.execute(
        "INSERT INTO reputation (user_id, score) VALUES (?, 1) "
        "ON CONFLICT(user_id) DO UPDATE SET score = score + 1",
        (target_id,),
    )
    await _conn.commit()
    return True, await get_reputation(target_id)


async def get_reputation(user_id: int) -> int:
    async with _conn.execute("SELECT score FROM reputation WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
        return row[0] if row else 0


async def top_reputation(limit: int = 10):
    _conn.row_factory = aiosqlite.Row
    async with _conn.execute(
        "SELECT r.user_id, r.score, m.username, m.full_name FROM reputation r "
        "LEFT JOIN members m ON m.user_id = r.user_id ORDER BY r.score DESC LIMIT ?",
        (limit,),
    ) as cur:
        return await cur.fetchall()


async def top_xp(limit: int = 10):
    _conn.row_factory = aiosqlite.Row
    async with _conn.execute(
        "SELECT user_id, username, full_name, xp, level FROM members ORDER BY xp DESC LIMIT ?", (limit,)
    ) as cur:
        return await cur.fetchall()


# ---------- CAPTCHA ----------

async def add_pending_captcha(user_id: int, join_message_id: int, correct_answer: str, deadline: int):
    await _conn.execute(
        "INSERT INTO pending_captcha (user_id, join_message_id, correct_answer, deadline) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET join_message_id = excluded.join_message_id, "
        "correct_answer = excluded.correct_answer, deadline = excluded.deadline",
        (user_id, join_message_id, correct_answer, deadline),
    )
    await _conn.commit()


async def get_pending_captcha(user_id: int):
    _conn.row_factory = aiosqlite.Row
    async with _conn.execute("SELECT * FROM pending_captcha WHERE user_id = ?", (user_id,)) as cur:
        return await cur.fetchone()


async def remove_pending_captcha(user_id: int):
    await _conn.execute("DELETE FROM pending_captcha WHERE user_id = ?", (user_id,))
    await _conn.commit()


# ---------- STATS OVERVIEW ----------

async def chat_overview() -> dict:
    async with _conn.execute("SELECT COUNT(*) FROM members") as cur:
        total_members = (await cur.fetchone())[0]
    async with _conn.execute("SELECT COUNT(*) FROM members WHERE is_muted = 1") as cur:
        muted = (await cur.fetchone())[0]
    async with _conn.execute("SELECT COUNT(*) FROM members WHERE is_banned = 1") as cur:
        banned = (await cur.fetchone())[0]
    async with _conn.execute("SELECT COALESCE(SUM(message_count), 0) FROM members") as cur:
        total_messages = (await cur.fetchone())[0]
    day_ago = int(time.time()) - 86400
    async with _conn.execute("SELECT COUNT(*) FROM logs WHERE action = 'verdict' AND ts > ?", (day_ago,)) as cur:
        violations_24h = (await cur.fetchone())[0]
    return {
        "total_members": total_members,
        "muted": muted,
        "banned": banned,
        "total_messages": total_messages,
        "violations_24h": violations_24h,
    }
