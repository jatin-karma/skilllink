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


def ensure_schema_updates() -> None:
    """Apply lightweight migrations for existing local SQLite databases."""
    db_conn = get_db()

    session_columns = {
        row["name"] for row in db_conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "video_platform" not in session_columns:
        db_conn.execute(
            "ALTER TABLE sessions ADD COLUMN video_platform TEXT NOT NULL DEFAULT 'Google Meet'"
        )
    if "meeting_link" not in session_columns:
        db_conn.execute("ALTER TABLE sessions ADD COLUMN meeting_link TEXT DEFAULT ''")

    user_columns = {
        row["name"] for row in db_conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "profile_image" not in user_columns:
        db_conn.execute("ALTER TABLE users ADD COLUMN profile_image TEXT DEFAULT ''")
    if "role" not in user_columns:
        db_conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    admin_count = db_conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
    if admin_count == 0:
        first_user = db_conn.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
        if first_user:
            db_conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (first_user["id"],))

    discussion_post_columns = {
        row["name"] for row in db_conn.execute("PRAGMA table_info(discussion_posts)").fetchall()
    }
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
