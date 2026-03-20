from typing import Any

from flask import current_app
from sqlalchemy import func, or_, select, text

from extensions import db
from models import Review, Skill, User


def _mappings_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def get_session_auto_complete_hours() -> int:
    raw_value = current_app.config.get("SESSION_AUTO_COMPLETE_HOURS", 2)
    try:
        parsed_value = int(raw_value)
    except (TypeError, ValueError):
        return 2
    return max(1, parsed_value)


def fetch_skills_page_data(
    query_text: str,
    category: str,
    level: str,
    min_rating: float | None,
    exclude_user_id: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mentor_rating_expr = func.coalesce(func.round(func.avg(Review.rating), 1), 0).label(
        "mentor_rating"
    )
    review_count_expr = func.count(Review.id).label("review_count")

    stmt = (
        select(
            Skill.id.label("id"),
            Skill.user_id.label("user_id"),
            Skill.name.label("name"),
            Skill.category.label("category"),
            Skill.level.label("level"),
            User.name.label("mentor_name"),
            mentor_rating_expr,
            review_count_expr,
        )
        .join(User, User.id == Skill.user_id)
        .outerjoin(Review, Review.reviewee_id == User.id)
        .where(Skill.skill_type == "teach")
    )

    if exclude_user_id is not None:
        stmt = stmt.where(Skill.user_id != exclude_user_id)

    if query_text:
        like_query = f"%{query_text}%"
        stmt = stmt.where(
            or_(
                Skill.name.like(like_query),
                Skill.category.like(like_query),
                User.name.like(like_query),
            )
        )

    if category:
        stmt = stmt.where(Skill.category == category)

    if level:
        stmt = stmt.where(Skill.level == level)

    stmt = stmt.group_by(
        Skill.id,
        Skill.user_id,
        Skill.name,
        Skill.category,
        Skill.level,
        User.name,
    )

    if min_rating is not None:
        stmt = stmt.having(func.coalesce(func.round(func.avg(Review.rating), 1), 0) >= min_rating)

    stmt = stmt.order_by(mentor_rating_expr.desc(), Skill.name.asc())
    skill_rows = _mappings_to_dicts(db.session.execute(stmt).mappings().all())

    category_stmt = (
        select(Skill.category.label("category"))
        .where(Skill.skill_type == "teach")
        .distinct()
        .order_by(Skill.category.asc())
    )
    if exclude_user_id is not None:
        category_stmt = category_stmt.where(Skill.user_id != exclude_user_id)
    categories = _mappings_to_dicts(db.session.execute(category_stmt).mappings().all())

    return skill_rows, categories


def fetch_matches_data(learner_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    learn_stmt = (
        select(
            Skill.name.label("name"),
            Skill.category.label("category"),
            Skill.level.label("level"),
        )
        .where(
            Skill.user_id == learner_id,
            Skill.skill_type == "learn",
        )
        .distinct()
        .order_by(Skill.name.asc())
    )
    learn_rows = _mappings_to_dicts(db.session.execute(learn_stmt).mappings().all())

    learn_names = [row["name"].lower() for row in learn_rows]
    if not learn_names:
        return [], learn_rows

    mentor_rating_expr = func.coalesce(func.round(func.avg(Review.rating), 1), 0).label(
        "mentor_rating"
    )
    review_count_expr = func.count(Review.id).label("review_count")

    match_stmt = (
        select(
            Skill.name.label("name"),
            Skill.category.label("category"),
            Skill.level.label("level"),
            Skill.user_id.label("mentor_id"),
            User.name.label("mentor_name"),
            mentor_rating_expr,
            review_count_expr,
        )
        .join(User, User.id == Skill.user_id)
        .outerjoin(Review, Review.reviewee_id == User.id)
        .where(
            Skill.skill_type == "teach",
            func.lower(Skill.name).in_(learn_names),
            Skill.user_id != learner_id,
        )
        .group_by(
            Skill.id,
            Skill.name,
            Skill.category,
            Skill.level,
            Skill.user_id,
            User.name,
        )
        .order_by(mentor_rating_expr.desc(), Skill.name.asc())
    )

    match_rows = _mappings_to_dicts(db.session.execute(match_stmt).mappings().all())
    return match_rows, learn_rows


def fetch_first_user_id() -> int | None:
    stmt = select(User.id).order_by(User.id.asc()).limit(1)
    return db.session.execute(stmt).scalar_one_or_none()


def fetch_profile_page_data(
    user_id: int,
    current_user_id: int | None,
) -> dict[str, Any]:
    user_stmt = select(
        User.id.label("id"),
        User.name.label("name"),
        User.email.label("email"),
        User.bio.label("bio"),
        User.profile_image.label("profile_image"),
        User.created_at.label("created_at"),
    ).where(User.id == user_id)
    user_row = db.session.execute(user_stmt).mappings().first()

    if not user_row:
        return {
            "user": None,
            "teach_skills": [],
            "learn_skills": [],
            "rating_summary": {"average_rating": 0, "total_reviews": 0},
            "profile_reviews": [],
            "my_sessions": [],
        }

    teach_stmt = (
        select(
            Skill.id.label("id"),
            Skill.name.label("name"),
            Skill.category.label("category"),
            Skill.level.label("level"),
        )
        .where(
            Skill.user_id == user_id,
            Skill.skill_type == "teach",
        )
        .order_by(Skill.name.asc())
    )
    teach_skills = _mappings_to_dicts(db.session.execute(teach_stmt).mappings().all())

    learn_stmt = (
        select(
            Skill.id.label("id"),
            Skill.name.label("name"),
            Skill.category.label("category"),
            Skill.level.label("level"),
        )
        .where(
            Skill.user_id == user_id,
            Skill.skill_type == "learn",
        )
        .order_by(Skill.name.asc())
    )
    learn_skills = _mappings_to_dicts(db.session.execute(learn_stmt).mappings().all())

    rating_stmt = select(
        func.coalesce(func.round(func.avg(Review.rating), 1), 0).label("average_rating"),
        func.count(Review.id).label("total_reviews"),
    ).where(Review.reviewee_id == user_id)
    rating_summary = dict(db.session.execute(rating_stmt).mappings().first())

    profile_reviews_stmt = (
        select(
            Review.rating.label("rating"),
            Review.comment.label("comment"),
            Review.created_at.label("created_at"),
            User.name.label("reviewer_name"),
        )
        .join(User, User.id == Review.reviewer_id)
        .where(Review.reviewee_id == user_id)
        .order_by(Review.created_at.desc())
        .limit(8)
    )
    profile_reviews = _mappings_to_dicts(
        db.session.execute(profile_reviews_stmt).mappings().all()
    )

    my_sessions: list[dict[str, Any]] = []
    if current_user_id and current_user_id == user_id:
        auto_complete_modifier = f"+{get_session_auto_complete_hours()} hours"
        my_sessions_stmt = text(
            """
            SELECT
                se.id,
                se.skill_name,
                se.scheduled_for,
                se.video_platform,
                se.meeting_link,
                se.status AS raw_status,
                CASE
                    WHEN se.status = 'scheduled'
                     AND datetime(datetime(se.scheduled_for, :auto_complete_modifier)) <= datetime('now', 'localtime')
                    THEN 'completed'
                    WHEN se.status = 'scheduled'
                     AND datetime(se.scheduled_for) <= datetime('now', 'localtime')
                    THEN 'ongoing'
                    ELSE se.status
                END AS status,
                se.notes,
                se.learner_id,
                se.mentor_id,
                learner.name AS learner_name,
                mentor.name AS mentor_name,
                (
                    SELECT COUNT(*)
                    FROM session_messages sm
                    WHERE sm.session_id = se.id
                ) AS message_count,
                (
                    SELECT COUNT(*)
                    FROM session_messages sm
                    WHERE sm.session_id = se.id
                      AND sm.sender_id != :viewer_id
                      AND sm.id > COALESCE(
                          (
                              SELECT sr.last_read_message_id
                              FROM session_reads sr
                              WHERE sr.session_id = se.id
                                AND sr.user_id = :viewer_id
                          ),
                          0
                      )
                ) AS unread_message_count,
                CASE WHEN rv.id IS NULL THEN 0 ELSE 1 END AS reviewed_by_me
            FROM sessions se
            JOIN users learner ON learner.id = se.learner_id
            JOIN users mentor ON mentor.id = se.mentor_id
            LEFT JOIN reviews rv
                ON rv.session_id = se.id
               AND rv.reviewer_id = :viewer_id
            WHERE se.learner_id = :owner_id OR se.mentor_id = :owner_id
            ORDER BY datetime(se.scheduled_for) DESC
            """
        )
        my_sessions = _mappings_to_dicts(
            db.session.execute(
                my_sessions_stmt,
                {
                    "viewer_id": current_user_id,
                    "owner_id": user_id,
                    "auto_complete_modifier": auto_complete_modifier,
                },
            ).mappings().all()
        )

    return {
        "user": dict(user_row),
        "teach_skills": teach_skills,
        "learn_skills": learn_skills,
        "rating_summary": rating_summary,
        "profile_reviews": profile_reviews,
        "my_sessions": my_sessions,
    }
