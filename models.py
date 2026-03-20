from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text

from extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    email = db.Column(db.Text, nullable=False, unique=True)
    password_hash = db.Column(db.Text, nullable=False)
    role = db.Column(db.Text, nullable=False, default="user", server_default="user")
    bio = db.Column(db.Text, default="", server_default="")
    profile_image = db.Column(db.Text, default="", server_default="")
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )


class Skill(db.Model):
    __tablename__ = "skills"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    category = db.Column(db.Text, nullable=False)
    level = db.Column(db.Text, nullable=False)
    skill_type = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("skill_type IN ('teach', 'learn')", name="ck_skills_skill_type"),
        Index("idx_skills_type_name", "skill_type", "name"),
        Index("idx_skills_user_type", "user_id", "skill_type"),
    )


class LearningSession(db.Model):
    __tablename__ = "sessions"

    id = db.Column(db.Integer, primary_key=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    mentor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    skill_name = db.Column(db.Text, nullable=False)
    scheduled_for = db.Column(db.Text, nullable=False)
    video_platform = db.Column(db.Text, nullable=False, default="Google Meet", server_default="Google Meet")
    meeting_link = db.Column(db.Text, default="", server_default="")
    status = db.Column(db.Text, nullable=False, default="scheduled", server_default="scheduled")
    notes = db.Column(db.Text, default="", server_default="")
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'completed', 'cancelled')", name="ck_sessions_status"),
        Index("idx_sessions_participants", "learner_id", "mentor_id"),
        Index(
            "idx_sessions_mentor_skill_unique",
            "mentor_id",
            "skill_name",
            unique=True,
            sqlite_where=text("status = 'scheduled'"),
        ),
    )


class SessionMessage(db.Model):
    __tablename__ = "session_messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_session_messages_session", "session_id", "created_at"),
    )


class SessionRead(db.Model):
    __tablename__ = "session_reads"

    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    updated_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_session_reads_user", "user_id", "session_id"),
    )


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reviewee_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, default="", server_default="")
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("session_id", "reviewer_id", name="uq_reviews_session_reviewer"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating"),
    )


class DiscussionPost(db.Model):
    __tablename__ = "discussion_posts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.Text, nullable=False)
    body = db.Column(db.Text, nullable=False)
    topic = db.Column(db.Text, nullable=False, default="General", server_default="General")
    tags = db.Column(db.Text, default="", server_default="")
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_discussion_posts_created", "created_at"),
        Index("idx_discussion_posts_topic", "topic", "created_at"),
    )


class DiscussionReply(db.Model):
    __tablename__ = "discussion_replies"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("discussion_posts.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_discussion_replies_post", "post_id", "created_at"),
    )


class PrivateDiscussion(db.Model):
    __tablename__ = "private_discussions"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.Text, nullable=False)
    invite_code = db.Column(db.Text, nullable=False, unique=True)
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_private_discussions_owner", "owner_id", "created_at"),
    )


class PrivateDiscussionMember(db.Model):
    __tablename__ = "private_discussion_members"

    discussion_id = db.Column(
        db.Integer,
        db.ForeignKey("private_discussions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    joined_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_private_discussion_members_user", "user_id", "discussion_id"),
    )


class PrivateDiscussionMessage(db.Model):
    __tablename__ = "private_discussion_messages"

    id = db.Column(db.Integer, primary_key=True)
    discussion_id = db.Column(
        db.Integer,
        db.ForeignKey("private_discussions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_private_discussion_messages_discussion", "discussion_id", "created_at"),
    )


class DiscussionReport(db.Model):
    __tablename__ = "discussion_reports"

    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("discussion_posts.id", ondelete="CASCADE"), nullable=True)
    reply_id = db.Column(db.Integer, db.ForeignKey("discussion_replies.id", ondelete="CASCADE"), nullable=True)
    reason = db.Column(db.Text, nullable=False)
    details = db.Column(db.Text, default="", server_default="")
    status = db.Column(db.Text, nullable=False, default="open", server_default="open")
    created_at = db.Column(db.Text, server_default=text("CURRENT_TIMESTAMP"))
    resolved_at = db.Column(db.Text)
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('open', 'resolved')", name="ck_discussion_reports_status"),
        CheckConstraint(
            "(post_id IS NOT NULL AND reply_id IS NULL) OR (post_id IS NULL AND reply_id IS NOT NULL)",
            name="ck_discussion_reports_target",
        ),
        Index("idx_discussion_reports_status", "status", "created_at"),
        Index(
            "idx_report_unique_post",
            "reporter_id",
            "post_id",
            unique=True,
            sqlite_where=text("reply_id IS NULL"),
        ),
        Index(
            "idx_report_unique_reply",
            "reporter_id",
            "reply_id",
            unique=True,
            sqlite_where=text("post_id IS NULL"),
        ),
    )
