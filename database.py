import sqlite3
from datetime import datetime

DB_NAME = "reminders.db"


def get_conn():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# =========================================================
# INIT
# =========================================================

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # Dùng để giữ hội thoại đang chờ bổ sung thông tin.
        # VD:
        # user: "mai nhắc t đi học"
        # bot:  "Mấy giờ?"
        # pending_state lưu intent cho đến khi user trả lời "7h"
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_states (
                user_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                data TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_user
            ON messages(user_id, id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user
            ON memories(user_id, id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reminders_user
            ON reminders(user_id, status, remind_at)
        """)

        conn.commit()


# =========================================================
# REMINDER
# =========================================================

def add_reminder(user_id, remind_at, content):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO reminders
            (user_id, remind_at, content, status)
            VALUES (?, ?, ?, 'PENDING')
        """, (
            str(user_id),
            remind_at,
            content
        ))

        conn.commit()
        return cur.lastrowid


def get_reminder_by_id(reminder_id, user_id=None):
    with get_conn() as conn:
        if user_id is None:
            row = conn.execute("""
                SELECT id, user_id, remind_at, content, status
                FROM reminders
                WHERE id = ?
                LIMIT 1
            """, (reminder_id,)).fetchone()

        else:
            row = conn.execute("""
                SELECT id, user_id, remind_at, content, status
                FROM reminders
                WHERE id = ?
                  AND user_id = ?
                LIMIT 1
            """, (
                reminder_id,
                str(user_id)
            )).fetchone()

    if not row:
        return None

    return dict(row)


def get_last_reminder(user_id):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT id, remind_at, content
            FROM reminders
            WHERE user_id = ?
              AND status = 'PENDING'
            ORDER BY id DESC
            LIMIT 1
        """, (str(user_id),)).fetchone()

    if not row:
        return None

    return (
        row["id"],
        row["remind_at"],
        row["content"]
    )


def get_user_reminders(user_id):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, remind_at, content
            FROM reminders
            WHERE user_id = ?
              AND status = 'PENDING'
            ORDER BY remind_at ASC
        """, (str(user_id),)).fetchall()

    return [
        (
            row["id"],
            row["remind_at"],
            row["content"]
        )
        for row in rows
    ]


def get_pending_reminders():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, user_id, remind_at, content
            FROM reminders
            WHERE status = 'PENDING'
            ORDER BY remind_at ASC
        """).fetchall()

    return [
        (
            row["id"],
            row["user_id"],
            row["remind_at"],
            row["content"]
        )
        for row in rows
    ]


def update_reminder(reminder_id, remind_at, content=None):
    with get_conn() as conn:
        if content is None:
            conn.execute("""
                UPDATE reminders
                SET remind_at = ?
                WHERE id = ?
            """, (
                remind_at,
                reminder_id
            ))
        else:
            conn.execute("""
                UPDATE reminders
                SET remind_at = ?, content = ?
                WHERE id = ?
            """, (
                remind_at,
                content,
                reminder_id
            ))

        conn.commit()


def cancel_reminder(reminder_id):
    with get_conn() as conn:
        conn.execute("""
            UPDATE reminders
            SET status = 'CANCELLED'
            WHERE id = ?
        """, (reminder_id,))

        conn.commit()


def mark_done(reminder_id):
    with get_conn() as conn:
        conn.execute("""
            UPDATE reminders
            SET status = 'DONE'
            WHERE id = ?
        """, (reminder_id,))

        conn.commit()


# =========================================================
# CHAT HISTORY
# =========================================================

def save_message(user_id, role, content):
    if not content:
        return

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO messages
            (user_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            str(user_id),
            role,
            str(content),
            datetime.now().isoformat()
        ))

        conn.commit()


def get_recent_messages(user_id, limit=24):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT role, content
            FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (
            str(user_id),
            int(limit)
        )).fetchall()

    rows = list(reversed(rows))

    return [
        {
            "role": row["role"],
            "content": row["content"]
        }
        for row in rows
    ]


def clear_chat_history(user_id):
    with get_conn() as conn:
        conn.execute("""
            DELETE FROM messages
            WHERE user_id = ?
        """, (str(user_id),))

        conn.commit()


# =========================================================
# LONG-TERM MEMORY
# =========================================================

def add_memory(user_id, content):
    content = str(content).strip()

    if not content:
        return False

    with get_conn() as conn:
        exists = conn.execute("""
            SELECT id
            FROM memories
            WHERE user_id = ?
              AND LOWER(content) = LOWER(?)
            LIMIT 1
        """, (
            str(user_id),
            content
        )).fetchone()

        if exists:
            return False

        conn.execute("""
            INSERT INTO memories
            (user_id, content, created_at)
            VALUES (?, ?, ?)
        """, (
            str(user_id),
            content,
            datetime.now().isoformat()
        ))

        conn.commit()

    return True


def get_memories(user_id, limit=30):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT content
            FROM memories
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (
            str(user_id),
            int(limit)
        )).fetchall()

    return [
        row["content"]
        for row in reversed(rows)
    ]


def clear_memories(user_id):
    with get_conn() as conn:
        conn.execute("""
            DELETE FROM memories
            WHERE user_id = ?
        """, (str(user_id),))

        conn.commit()


# =========================================================
# PENDING CONVERSATION STATE
# =========================================================

def set_pending_state(user_id, action, data=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO pending_states
            (user_id, action, data, created_at)
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id)
            DO UPDATE SET
                action = excluded.action,
                data = excluded.data,
                created_at = excluded.created_at
        """, (
            str(user_id),
            action,
            data or "",
            datetime.now().isoformat()
        ))

        conn.commit()


def get_pending_state(user_id):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT action, data, created_at
            FROM pending_states
            WHERE user_id = ?
            LIMIT 1
        """, (str(user_id),)).fetchone()

    if not row:
        return None

    return {
        "action": row["action"],
        "data": row["data"],
        "created_at": row["created_at"]
    }


def clear_pending_state(user_id):
    with get_conn() as conn:
        conn.execute("""
            DELETE FROM pending_states
            WHERE user_id = ?
        """, (str(user_id),))

        conn.commit()