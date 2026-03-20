import sqlite3

from flask import current_app, g
from werkzeug.security import generate_password_hash

from extensions import db
import models  # noqa: F401


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    db_conn = g.pop("db", None)
    if db_conn is not None:
        db_conn.close()


def normalize_skill_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


def get_table_columns(db_conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row["name"] for row in db_conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def normalize_and_dedupe_skills(db_conn: sqlite3.Connection) -> None:
    skill_rows = db_conn.execute(
        "SELECT id, user_id, name, skill_type FROM skills ORDER BY id ASC"
    ).fetchall()
    seen_skill_keys: set[tuple[int, str, str]] = set()
    duplicate_skill_ids: list[int] = []

    for row in skill_rows:
        normalized_name = normalize_skill_name(row["name"])
        if normalized_name != row["name"]:
            db_conn.execute(
                "UPDATE skills SET name = ? WHERE id = ?",
                (normalized_name, row["id"]),
            )

        dedupe_key = (row["user_id"], row["skill_type"], normalized_name.lower())
        if dedupe_key in seen_skill_keys:
            duplicate_skill_ids.append(row["id"])
            continue
        seen_skill_keys.add(dedupe_key)

    if duplicate_skill_ids:
        placeholders = ",".join("?" for _ in duplicate_skill_ids)
        db_conn.execute(f"DELETE FROM skills WHERE id IN ({placeholders})", duplicate_skill_ids)


def ensure_schema_updates() -> None:
    """Apply lightweight migrations for existing local SQLite databases."""
    db_conn = get_db()

    session_columns = get_table_columns(db_conn, "sessions")
    if "video_platform" not in session_columns:
        db_conn.execute(
            "ALTER TABLE sessions ADD COLUMN video_platform TEXT NOT NULL DEFAULT 'Google Meet'"
        )
    if "meeting_link" not in session_columns:
        db_conn.execute("ALTER TABLE sessions ADD COLUMN meeting_link TEXT DEFAULT ''")

    user_columns = get_table_columns(db_conn, "users")
    if "profile_image" not in user_columns:
        db_conn.execute("ALTER TABLE users ADD COLUMN profile_image TEXT DEFAULT ''")
    if "role" not in user_columns:
        db_conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    admin_count = db_conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
    if admin_count == 0:
        first_user = db_conn.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
        if first_user:
            db_conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (first_user["id"],))

    discussion_post_columns = get_table_columns(db_conn, "discussion_posts")
    if "topic" not in discussion_post_columns:
        db_conn.execute(
            "ALTER TABLE discussion_posts ADD COLUMN topic TEXT NOT NULL DEFAULT 'General'"
        )
    if "tags" not in discussion_post_columns:
        db_conn.execute("ALTER TABLE discussion_posts ADD COLUMN tags TEXT DEFAULT ''")

    db_conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_posts_topic ON discussion_posts(topic, created_at DESC)"
    )
    db_conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_reports_status ON discussion_reports(status, created_at DESC)"
    )
    db_conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_report_unique_post
        ON discussion_reports(reporter_id, post_id)
        WHERE reply_id IS NULL
        """
    )
    db_conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_report_unique_reply
        ON discussion_reports(reporter_id, reply_id)
        WHERE post_id IS NULL
        """
    )

    db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_dashboards (
            user_id INTEGER PRIMARY KEY,
            state_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_dashboards_updated
        ON profile_dashboards(updated_at DESC)
        """
    )

    db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS private_discussions (
            id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            invite_code TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS private_discussion_members (
            discussion_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(discussion_id, user_id),
            FOREIGN KEY(discussion_id) REFERENCES private_discussions(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS private_discussion_messages (
            id INTEGER PRIMARY KEY,
            discussion_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(discussion_id) REFERENCES private_discussions(id) ON DELETE CASCADE,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_private_discussions_owner
        ON private_discussions(owner_id, created_at DESC)
        """
    )
    db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_private_discussion_members_user
        ON private_discussion_members(user_id, discussion_id)
        """
    )
    db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_private_discussion_messages_discussion
        ON private_discussion_messages(discussion_id, created_at ASC)
        """
    )
    db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            link_path TEXT DEFAULT '',
            is_read INTEGER NOT NULL DEFAULT 0,
            event_type TEXT DEFAULT '',
            session_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            read_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE SET NULL
        )
        """
    )

    notification_columns = get_table_columns(db_conn, "notifications")
    if "link_path" not in notification_columns:
        db_conn.execute("ALTER TABLE notifications ADD COLUMN link_path TEXT DEFAULT ''")
    if "is_read" not in notification_columns:
        db_conn.execute("ALTER TABLE notifications ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0")
    if "event_type" not in notification_columns:
        db_conn.execute("ALTER TABLE notifications ADD COLUMN event_type TEXT DEFAULT ''")
    if "session_id" not in notification_columns:
        db_conn.execute("ALTER TABLE notifications ADD COLUMN session_id INTEGER")
    if "read_at" not in notification_columns:
        db_conn.execute("ALTER TABLE notifications ADD COLUMN read_at TEXT")

    db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notifications_user_status
        ON notifications(user_id, is_read, created_at DESC)
        """
    )
    db_conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_user_event_session
        ON notifications(user_id, event_type, session_id)
        WHERE session_id IS NOT NULL
          AND TRIM(COALESCE(event_type, '')) != ''
        """
    )
    db_conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_mentor_skill_unique
        ON sessions(mentor_id, skill_name)
        WHERE status = 'scheduled'
        """
    )

    normalize_and_dedupe_skills(db_conn)

    db_conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_user_type_name_unique
        ON skills(user_id, skill_type, name COLLATE NOCASE)
        """
    )
    db_conn.commit()


def seed_data() -> None:
    db_conn = get_db()
    existing = db_conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
    if existing:
        return

    sample_users = [
        (
            "Jatin Karma",
            "jatin@example.com",
            "I enjoy helping peers with coding and communication.",
            "admin",
        ),
        (
            "Riya Shah",
            "riya@example.com",
            "Creative learner focused on design and media.",
            "user",
        ),
        (
            "Arjun Mehta",
            "arjun@example.com",
            "Tech enthusiast interested in analytics and mentoring.",
            "user",
        ),
    ]

    for name, email, bio, role in sample_users:
        db_conn.execute(
            "INSERT INTO users (name, email, password_hash, bio, role) VALUES (?, ?, ?, ?, ?)",
            (name, email, generate_password_hash("password123"), bio, role),
        )

    users = {
        row["email"]: row["id"]
        for row in db_conn.execute("SELECT id, email FROM users").fetchall()
    }

    sample_skills = [
        (users["jatin@example.com"], "Python", "Programming", "Advanced", "teach"),
        (users["jatin@example.com"], "Public Speaking", "Communication", "Intermediate", "teach"),
        (users["jatin@example.com"], "Graphic Design", "Design", "Beginner", "learn"),
        (users["riya@example.com"], "Graphic Design", "Design", "Advanced", "teach"),
        (users["riya@example.com"], "Video Editing", "Media", "Intermediate", "teach"),
        (users["riya@example.com"], "Python", "Programming", "Beginner", "learn"),
        (users["arjun@example.com"], "Data Analysis", "Programming", "Advanced", "teach"),
        (
            users["arjun@example.com"],
            "Communication Skills",
            "Communication",
            "Intermediate",
            "teach",
        ),
        (users["arjun@example.com"], "Video Editing", "Media", "Beginner", "learn"),
    ]

    db_conn.executemany(
        """
        INSERT INTO skills (user_id, name, category, level, skill_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        sample_skills,
    )

    db_conn.execute(
        """
        INSERT INTO sessions (learner_id, mentor_id, skill_name, scheduled_for, status, notes)
        VALUES (?, ?, ?, ?, 'completed', ?)
        """,
        (
            users["riya@example.com"],
            users["jatin@example.com"],
            "Python",
            "2026-03-01 16:00",
            "Intro session on Python basics and problem solving.",
        ),
    )

    session_id = db_conn.execute("SELECT id FROM sessions LIMIT 1").fetchone()["id"]
    db_conn.execute(
        """
        INSERT INTO reviews (session_id, reviewer_id, reviewee_id, rating, comment)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session_id,
            users["riya@example.com"],
            users["jatin@example.com"],
            5,
            "Great mentor. The session was practical and easy to follow.",
        ),
    )

    db_conn.commit()


def initialize_database() -> None:
    db.create_all()
    ensure_schema_updates()
    seed_data()
