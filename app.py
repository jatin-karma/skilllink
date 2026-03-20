import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import urlparse
from uuid import uuid4

from flask import Flask, abort, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from database import close_db, get_db, initialize_database
from extensions import db
from query_services import (
    fetch_first_user_id,
    fetch_matches_data,
    fetch_profile_page_data,
    fetch_skills_page_data,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    root_path=PROJECT_ROOT,
    template_folder=os.path.join(PROJECT_ROOT, "templates"),
    static_folder=os.path.join(PROJECT_ROOT, "static"),
)
IS_PRODUCTION = os.environ.get("FLASK_ENV", "").lower() == "production"
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["DATABASE"] = os.path.join(PROJECT_ROOT, "skill_exchange.db")
app.config["PROFILE_UPLOAD_FOLDER"] = os.path.join(
    app.root_path, "uploads", "profile_pics"
)
app.config["PROFILE_UPLOAD_LEGACY_FOLDER"] = os.path.join(
    app.root_path, "static", "uploads", "profile_pics"
)
app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"sqlite:///{app.config['DATABASE'].replace(os.sep, '/')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["PROFILE_UPLOAD_MAX_BYTES"] = 4 * 1024 * 1024
app.config["PROFILE_DASHBOARD_MAX_BYTES"] = 10 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION

if not IS_PRODUCTION:
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

if IS_PRODUCTION and app.config["SECRET_KEY"] == "dev-secret-change-me":
    raise RuntimeError("Set FLASK_SECRET_KEY in production before starting the app.")

SKILL_LEVELS = ["Beginner", "Intermediate", "Advanced"]
SKILL_TYPES = ["teach", "learn"]
VIDEO_PLATFORMS = ["Google Meet", "Zoom", "Microsoft Teams", "Jitsi", "Other"]
PROFILE_IMAGE_EXTENSIONS = {"png", "jpg", "gif", "webp"}
PROFILE_DASHBOARD_MEDIA_MAX_CHARS = 5_800_000
PROFILE_DASHBOARD_MAX_POST_LIKES = 500
PRIVATE_DISCUSSION_INVITE_CODE_LENGTH = 8
PRIVATE_DISCUSSION_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DISCUSSION_TOPICS = [
    "General",
    "Programming",
    "Design",
    "Communication",
    "Media",
    "Career",
    "Projects",
]
REPORT_REASONS = ["Spam", "Abusive", "Harassment", "Misinformation", "Other"]
CSRF_TOKEN_KEY = "csrf_token"
PROFILE_DASHBOARD_MAX_ITEMS = {
    "certificates": 20,
    "projects": 20,
    "posts": 40,
}
DATA_URL_PREFIXES = {
    "resume": ("data:application/pdf;base64,",),
    "certificate": (
        "data:image/jpeg;base64,",
        "data:image/jpg;base64,",
        "data:image/png;base64,",
        "data:image/gif;base64,",
        "data:image/webp;base64,",
        "data:application/pdf;base64,",
    ),
    "project_image": (
        "data:image/jpeg;base64,",
        "data:image/jpg;base64,",
        "data:image/png;base64,",
        "data:image/gif;base64,",
        "data:image/webp;base64,",
    ),
    "profile_banner": (
        "data:image/jpeg;base64,",
        "data:image/jpg;base64,",
        "data:image/png;base64,",
        "data:image/gif;base64,",
        "data:image/webp;base64,",
    ),
    "post_attachment": (
        "data:image/jpeg;base64,",
        "data:image/jpg;base64,",
        "data:image/png;base64,",
        "data:image/gif;base64,",
        "data:image/webp;base64,",
        "data:video/mp4;base64,",
        "data:video/webm;base64,",
        "data:video/quicktime;base64,",
        "data:application/pdf;base64,",
    ),
}

os.makedirs(app.config["PROFILE_UPLOAD_FOLDER"], exist_ok=True)
db.init_app(app)
app.teardown_appcontext(close_db)


def normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def normalize_tags(value: str) -> str:
    cleaned: list[str] = []
    for raw_tag in value.split(","):
        tag = normalize_text(raw_tag.replace("#", ""))
        if not tag:
            continue
        if len(tag) > 24:
            tag = tag[:24].strip()
        if tag and tag.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(tag)
        if len(cleaned) >= 8:
            break
    return ", ".join(cleaned)


def split_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return [tag.strip() for tag in value.split(",") if tag.strip()]


def normalize_free_text(value: object, max_length: int, default: str = "") -> str:
    if not isinstance(value, str):
        return default
    cleaned = value.strip()
    if not cleaned:
        return default
    return cleaned[:max_length]


def sanitize_external_url(value: object) -> str:
    if not isinstance(value, str):
        return ""

    cleaned = value.strip()
    if not cleaned:
        return ""

    candidate = cleaned if cleaned.lower().startswith(("http://", "https://")) else f"https://{cleaned}"

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""

    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc:
        return ""

    return parsed.geturl()[:300]


def sanitize_data_url(value: object, allowed_prefixes: tuple[str, ...], max_chars: int) -> str:
    if not isinstance(value, str):
        return ""

    raw_value = value.strip()
    if not raw_value or len(raw_value) > max_chars:
        return ""

    lower_value = raw_value.lower()
    if not any(lower_value.startswith(prefix) for prefix in allowed_prefixes):
        return ""

    if "," not in raw_value:
        return ""

    return raw_value


def normalize_post_like_user_ids(raw_likes: object) -> list[int]:
    likes_user_ids: list[int] = []
    if not isinstance(raw_likes, list):
        return likes_user_ids

    for raw_like_user_id in raw_likes[:PROFILE_DASHBOARD_MAX_POST_LIKES]:
        if isinstance(raw_like_user_id, bool):
            continue
        try:
            like_user_id = int(raw_like_user_id)
        except (TypeError, ValueError):
            continue
        if like_user_id <= 0 or like_user_id in likes_user_ids:
            continue
        likes_user_ids.append(like_user_id)

    return likes_user_ids


def parse_dashboard_datetime(raw_value: object) -> datetime | None:
    if not isinstance(raw_value, str):
        return None

    candidate = raw_value.strip()
    if not candidate:
        return None

    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"

    try:
        parsed_value = datetime.fromisoformat(candidate)
    except ValueError:
        return None

    if parsed_value.tzinfo is not None:
        return parsed_value.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed_value


def generate_private_discussion_invite_code(db_conn: sqlite3.Connection) -> str:
    for _ in range(20):
        invite_code = "".join(
            secrets.choice(PRIVATE_DISCUSSION_INVITE_ALPHABET)
            for _ in range(PRIVATE_DISCUSSION_INVITE_CODE_LENGTH)
        )
        existing = db_conn.execute(
            "SELECT 1 FROM private_discussions WHERE invite_code = ?",
            (invite_code,),
        ).fetchone()
        if existing is None:
            return invite_code

    return uuid4().hex[:12].upper()


def user_is_private_discussion_member(
    db_conn: sqlite3.Connection,
    discussion_id: int,
    user_id: int,
) -> bool:
    membership = db_conn.execute(
        """
        SELECT 1
        FROM private_discussion_members
        WHERE discussion_id = ? AND user_id = ?
        """,
        (discussion_id, user_id),
    ).fetchone()
    return membership is not None


def add_private_discussion_member(
    db_conn: sqlite3.Connection,
    discussion_id: int,
    user_id: int,
) -> None:
    db_conn.execute(
        """
        INSERT OR IGNORE INTO private_discussion_members (discussion_id, user_id)
        VALUES (?, ?)
        """,
        (discussion_id, user_id),
    )


def default_profile_dashboard_state() -> dict:
    return {
        "kicker": "Student Profile",
        "headline": "",
        "about": "",
        "bannerImageDataUrl": "",
        "social": {
            "linkedin": "",
            "github": "",
            "portfolio": "",
        },
        "education": {
            "college": "N/A",
            "department": "N/A",
            "graduationYear": "----",
            "cgpa": "--",
            "enrollment": "---",
        },
        "resume": {
            "name": "",
            "dataUrl": "",
        },
        "certificates": [],
        "projects": [],
        "posts": [],
    }


def sanitize_profile_dashboard_state(raw_state: object) -> dict:
    sanitized = default_profile_dashboard_state()
    if not isinstance(raw_state, dict):
        return sanitized

    sanitized["kicker"] = normalize_free_text(raw_state.get("kicker"), 40, "Student Profile")
    sanitized["headline"] = normalize_free_text(raw_state.get("headline"), 120)
    sanitized["about"] = normalize_free_text(raw_state.get("about"), 1600)
    sanitized["bannerImageDataUrl"] = sanitize_data_url(
        raw_state.get("bannerImageDataUrl"),
        DATA_URL_PREFIXES["profile_banner"],
        max_chars=PROFILE_DASHBOARD_MEDIA_MAX_CHARS,
    )

    social = raw_state.get("social")
    if isinstance(social, dict):
        sanitized["social"] = {
            "linkedin": sanitize_external_url(social.get("linkedin")),
            "github": sanitize_external_url(social.get("github")),
            "portfolio": sanitize_external_url(social.get("portfolio")),
        }

    education = raw_state.get("education")
    if isinstance(education, dict):
        sanitized["education"] = {
            "college": normalize_free_text(education.get("college"), 140, "N/A"),
            "department": normalize_free_text(education.get("department"), 140, "N/A"),
            "graduationYear": normalize_free_text(education.get("graduationYear"), 10, "----"),
            "cgpa": normalize_free_text(education.get("cgpa"), 10, "--"),
            "enrollment": normalize_free_text(education.get("enrollment"), 32, "---"),
        }

    resume = raw_state.get("resume")
    if isinstance(resume, dict):
        resume_data = sanitize_data_url(
            resume.get("dataUrl"),
            DATA_URL_PREFIXES["resume"],
            max_chars=PROFILE_DASHBOARD_MEDIA_MAX_CHARS,
        )
        sanitized["resume"] = {
            "name": normalize_free_text(resume.get("name"), 180),
            "dataUrl": resume_data,
        }

    certificates = raw_state.get("certificates")
    if isinstance(certificates, list):
        for item in certificates[: PROFILE_DASHBOARD_MAX_ITEMS["certificates"]]:
            if not isinstance(item, dict):
                continue

            data_url = sanitize_data_url(
                item.get("dataUrl"),
                DATA_URL_PREFIXES["certificate"],
                max_chars=PROFILE_DASHBOARD_MEDIA_MAX_CHARS,
            )
            if not data_url:
                continue

            sanitized["certificates"].append(
                {
                    "id": normalize_free_text(item.get("id"), 80, uuid4().hex),
                    "name": normalize_free_text(item.get("name"), 180, "Certificate"),
                    "type": normalize_free_text(item.get("type"), 60, "file"),
                    "dataUrl": data_url,
                    "createdAt": normalize_free_text(item.get("createdAt"), 40, datetime.utcnow().isoformat()),
                }
            )

    projects = raw_state.get("projects")
    if isinstance(projects, list):
        for item in projects[: PROFILE_DASHBOARD_MAX_ITEMS["projects"]]:
            if not isinstance(item, dict):
                continue

            title = normalize_free_text(item.get("title"), 120)
            description = normalize_free_text(item.get("description"), 1000)
            if not title or not description:
                continue

            image_data = sanitize_data_url(
                item.get("imageDataUrl"),
                DATA_URL_PREFIXES["project_image"],
                max_chars=PROFILE_DASHBOARD_MEDIA_MAX_CHARS,
            )

            sanitized["projects"].append(
                {
                    "id": normalize_free_text(item.get("id"), 80, uuid4().hex),
                    "title": title,
                    "description": description,
                    "link": sanitize_external_url(item.get("link")),
                    "imageDataUrl": image_data,
                    "createdAt": normalize_free_text(item.get("createdAt"), 40, datetime.utcnow().isoformat()),
                }
            )

    posts = raw_state.get("posts")
    if isinstance(posts, list):
        for item in posts[: PROFILE_DASHBOARD_MAX_ITEMS["posts"]]:
            if not isinstance(item, dict):
                continue

            content = normalize_free_text(item.get("content"), 2000)
            attachment_entry: dict | None = None

            attachment = item.get("attachment")
            if isinstance(attachment, dict):
                attachment_data = sanitize_data_url(
                    attachment.get("dataUrl"),
                    DATA_URL_PREFIXES["post_attachment"],
                    max_chars=PROFILE_DASHBOARD_MEDIA_MAX_CHARS,
                )
                if attachment_data:
                    attachment_entry = {
                        "name": normalize_free_text(attachment.get("name"), 180, "Attachment"),
                        "type": normalize_free_text(attachment.get("type"), 80, "file"),
                        "dataUrl": attachment_data,
                    }

            if not content and not attachment_entry:
                continue

            likes_user_ids = normalize_post_like_user_ids(item.get("likesUserIds"))

            post_entry = {
                "id": normalize_free_text(item.get("id"), 80, uuid4().hex),
                "content": content,
                "createdAt": normalize_free_text(item.get("createdAt"), 40, datetime.utcnow().isoformat()),
                "likesUserIds": likes_user_ids,
            }
            if attachment_entry:
                post_entry["attachment"] = attachment_entry

            sanitized["posts"].append(post_entry)

    return sanitized


def get_profile_dashboard_state(user_id: int) -> dict:
    row = get_db().execute(
        "SELECT state_json FROM profile_dashboards WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["state_json"]:
        return default_profile_dashboard_state()

    try:
        parsed_state = json.loads(row["state_json"])
    except json.JSONDecodeError:
        return default_profile_dashboard_state()

    return sanitize_profile_dashboard_state(parsed_state)


def save_profile_dashboard_state_for_user(user_id: int, state: dict) -> None:
    serialized_state = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    get_db().execute(
        """
        INSERT INTO profile_dashboards (user_id, state_json, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id)
        DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, serialized_state),
    )
    get_db().commit()
    get_db().commit()


def get_csrf_token() -> str:
    token = session.get(CSRF_TOKEN_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_TOKEN_KEY] = token
    return token


def user_is_admin(user: dict | None) -> bool:
    return bool(user and user.get("role") == "admin")


def normalize_profile_image_extension(extension: str) -> str:
    normalized = extension.lower().lstrip(".")
    if normalized == "jpeg":
        return "jpg"
    return normalized


def is_allowed_profile_image(filename: str) -> bool:
    safe_name = secure_filename(filename)
    if not safe_name or "." not in safe_name:
        return False
    extension = normalize_profile_image_extension(safe_name.rsplit(".", 1)[1])
    return extension in PROFILE_IMAGE_EXTENSIONS


def detect_profile_image_extension(file_stream) -> str | None:
    file_header = file_stream.read(16)
    file_stream.seek(0)

    if file_header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if file_header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if file_header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(file_header) >= 12 and file_header[:4] == b"RIFF" and file_header[8:12] == b"WEBP":
        return "webp"
    return None


def get_uploaded_file_size(uploaded_file) -> int:
    current_position = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0, os.SEEK_END)
    size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(current_position)
    return size


def profile_image_url(filename: str | None) -> str | None:
    if not filename:
        return None
    safe_name = secure_filename(filename)
    if not safe_name:
        return None
    return url_for("profile_image_file", filename=safe_name)


def delete_profile_image_file(filename: str | None) -> None:
    if not filename:
        return

    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        return

    upload_folders = [
        app.config["PROFILE_UPLOAD_FOLDER"],
        app.config["PROFILE_UPLOAD_LEGACY_FOLDER"],
    ]

    for folder in upload_folders:
        file_path = os.path.join(folder, safe_name)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            continue


def migrate_legacy_profile_images() -> None:
    source_folder = app.config["PROFILE_UPLOAD_LEGACY_FOLDER"]
    target_folder = app.config["PROFILE_UPLOAD_FOLDER"]

    if not os.path.isdir(source_folder):
        return

    for item_name in os.listdir(source_folder):
        safe_name = secure_filename(item_name)
        if not safe_name or safe_name != item_name:
            continue
        if not is_allowed_profile_image(safe_name):
            continue

        source_path = os.path.join(source_folder, safe_name)
        target_path = os.path.join(target_folder, safe_name)
        if not os.path.isfile(source_path) or os.path.exists(target_path):
            continue

        try:
            os.replace(source_path, target_path)
        except OSError:
            continue


def get_current_user() -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    row = get_db().execute(
        "SELECT id, name, email, role, bio, profile_image FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


@app.before_request
def load_logged_in_user() -> None:
    g.current_user = get_current_user()


@app.before_request
def enforce_csrf() -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return

    expected_token = session.get(CSRF_TOKEN_KEY, "")
    received_token = request.form.get("csrf_token", "") or request.headers.get(
        "X-CSRF-Token", ""
    )

    if not expected_token or not received_token:
        flash("Security check failed. Please refresh and try again.", "danger")
        return redirect(request.referrer or url_for("home"))

    if not secrets.compare_digest(expected_token, received_token):
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(request.referrer or url_for("home"))


@app.context_processor
def inject_template_data() -> dict:
    return {
        "current_user": g.get("current_user"),
        "is_admin": user_is_admin(g.get("current_user")),
        "skill_levels": SKILL_LEVELS,
        "discussion_topics": DISCUSSION_TOPICS,
        "report_reasons": REPORT_REASONS,
        "video_platforms": VIDEO_PLATFORMS,
        "csrf_token": get_csrf_token(),
        "split_tags": split_tags,
        "get_profile_image_url": profile_image_url,
        "static_file_version": static_file_version,
    }


@app.after_request
def disable_html_cache(response):
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large_file(_error):
    flash("Upload is too large. Reduce file size and try again.", "danger")
    return redirect(request.referrer or url_for("profile"))


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if g.current_user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if g.current_user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        if not user_is_admin(g.current_user):
            flash("Admin access required.", "danger")
            return redirect(url_for("home"))
        return view_func(*args, **kwargs)

    return wrapped


def static_file_version(filename: str) -> int:
    static_root = app.static_folder or os.path.join(app.root_path, "static")
    file_path = os.path.join(static_root, *filename.split("/"))
    try:
        return int(os.path.getmtime(file_path))
    except OSError:
        return 1


def skill_thumbnail_file(skill_name: str, category: str) -> str:
    name_lower = skill_name.lower()
    category_lower = category.lower()
    combined = f"{skill_name} {category}".lower()

    if (
        "public speaking" in name_lower
        or "communication" in name_lower
        or "communication" in category_lower
    ):
        return "media/images/commu.png"

    if "graphic design" in name_lower or "design" in category_lower:
        return "media/images/GD.jpg"

    if any(
        keyword in combined
        for keyword in (
            "python",
            "java",
            "code",
            "coding",
            "program",
            "software",
            "web",
            "data",
            "ai",
            "machine learning",
            "devops",
        )
    ):
        return "media/images/skill-programming.jpg"

    if any(
        keyword in combined
        for keyword in (
            "design",
            "ux",
            "ui",
            "graphic",
            "creative",
            "video",
            "photo",
            "editing",
            "media",
            "music",
            "art",
        )
    ):
        return "media/images/GD.jpg"

    if any(
        keyword in combined
        for keyword in (
            "communication",
            "language",
            "public speaking",
            "speaking",
            "presentation",
            "writing",
            "english",
            "interview",
        )
    ):
        return "media/images/commu.png"

    if any(
        keyword in combined
        for keyword in (
            "business",
            "finance",
            "economics",
            "management",
            "math",
            "mathematics",
            "accounting",
        )
    ):
        return "media/images/skill-thumb-4.png"

    return "media/images/skill-programming.jpg"


@app.route("/")
def home():
    db = get_db()
    stats = db.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM users) AS total_students,
            (SELECT COUNT(*) FROM skills WHERE skill_type = 'teach') AS total_teaching_offers,
            (SELECT COUNT(DISTINCT name) FROM skills WHERE skill_type = 'teach') AS total_skill_topics,
            (SELECT ROUND(COALESCE(AVG(rating), 0), 1) FROM reviews) AS average_rating
        """
    ).fetchone()

    popular_skill_rows = db.execute(
        """
        SELECT name, category, COUNT(*) AS mentor_count
        FROM skills
        WHERE skill_type = 'teach'
        GROUP BY name, category
        ORDER BY mentor_count DESC, name ASC
        LIMIT 6
        """
    ).fetchall()

    popular_skills = []
    for row in popular_skill_rows:
        skill_item = dict(row)
        skill_item["thumb_file"] = skill_thumbnail_file(
            skill_name=skill_item["name"],
            category=skill_item["category"],
        )
        skill_item["thumb_version"] = static_file_version(skill_item["thumb_file"])
        popular_skills.append(skill_item)

    testimonials = db.execute(
        """
        SELECT rv.comment, rv.rating, reviewer.name AS reviewer_name, reviewee.name AS reviewee_name
        FROM reviews rv
        JOIN users reviewer ON reviewer.id = rv.reviewer_id
        JOIN users reviewee ON reviewee.id = rv.reviewee_id
        WHERE TRIM(rv.comment) != ''
        ORDER BY rv.created_at DESC
        LIMIT 3
        """
    ).fetchall()

    return render_template(
        "index.html",
        stats=stats,
        popular_skills=popular_skills,
        testimonials=testimonials,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    preview_mode = request.args.get("preview") == "1"
    if g.current_user and not preview_mode:
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    if request.method == "POST":
        name = normalize_text(request.form.get("name", ""))
        email = normalize_text(request.form.get("email", "")).lower()
        password = request.form.get("password", "")
        bio = normalize_free_text(request.form.get("bio"), 1200)
        college_name = normalize_free_text(request.form.get("college_name"), 140, "N/A")
        department = normalize_free_text(request.form.get("department"), 140, "N/A")
        enrollment_number = normalize_free_text(request.form.get("enrollment_number"), 32, "---")
        graduation_year = normalize_free_text(request.form.get("graduation_year"), 10, "----")
        address = normalize_free_text(request.form.get("address"), 300)

        if not name or not email or not password:
            flash("Name, email, and password are required.", "danger")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")

        db = get_db()
        try:
            cursor = db.execute(
                """
                INSERT INTO users (name, email, password_hash, bio)
                VALUES (?, ?, ?, ?)
                """,
                (name, email, generate_password_hash(password), bio),
            )
            db.commit()

            profile_state = sanitize_profile_dashboard_state(
                {
                    "about": address,
                    "education": {
                        "college": college_name,
                        "department": department,
                        "graduationYear": graduation_year,
                        "cgpa": "--",
                        "enrollment": enrollment_number,
                    },
                }
            )
            save_profile_dashboard_state_for_user(cursor.lastrowid, profile_state)
        except sqlite3.IntegrityError:
            flash("An account with this email already exists.", "danger")
            return render_template("register.html")

        session.clear()
        session["user_id"] = cursor.lastrowid
        flash("Account created successfully.", "success")
        return redirect(url_for("profile", user_id=cursor.lastrowid))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    preview_mode = request.args.get("preview") == "1"
    if g.current_user and not preview_mode:
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    if request.method == "POST":
        email = normalize_text(request.form.get("email", "")).lower()
        password = request.form.get("password", "")

        user = get_db().execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]
        flash("Welcome back.", "success")

        next_url = request.args.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("profile", user_id=user["id"]))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


@app.route("/skills")
def skills():
    query_text = normalize_text(request.args.get("q", ""))
    category = normalize_text(request.args.get("category", ""))
    level = normalize_text(request.args.get("level", ""))
    min_rating_raw = normalize_text(request.args.get("min_rating", ""))
    min_rating: float | None = None

    if min_rating_raw:
        try:
            min_rating = float(min_rating_raw)
        except ValueError:
            flash("Minimum rating filter was ignored due to invalid value.", "warning")

    skill_rows, categories = fetch_skills_page_data(
        query_text=query_text,
        category=category,
        level=level,
        min_rating=min_rating,
    )

    filters = {
        "q": query_text,
        "category": category,
        "level": level,
        "min_rating": min_rating_raw,
    }

    return render_template(
        "skills.html",
        skills=skill_rows,
        categories=categories,
        filters=filters,
    )


def _build_skill_filters(request):
    query_text = normalize_text(request.args.get("q", ""))
    category = normalize_text(request.args.get("category", ""))
    level = normalize_text(request.args.get("level", ""))
    min_rating_raw = normalize_text(request.args.get("min_rating", ""))
    min_rating: float | None = None
    if min_rating_raw:
        try:
            min_rating = float(min_rating_raw)
        except ValueError:
            flash("Minimum rating filter was ignored due to invalid value.", "warning")
    return query_text, category, level, min_rating_raw, min_rating


@app.route("/become-mentor")
def become_mentor():
    query_text, category, level, min_rating_raw, min_rating = _build_skill_filters(request)
    # Show teach-type skills (mentors available) so visitors can see what topics are taught
    skill_rows, categories = fetch_skills_page_data(
        query_text=query_text,
        category=category,
        level=level,
        min_rating=min_rating,
    )
    filters = {"q": query_text, "category": category, "level": level, "min_rating": min_rating_raw}
    return render_template("become_mentor.html", skills=skill_rows, categories=categories, filters=filters)


@app.route("/learn")
def learn_skill():
    query_text, category, level, min_rating_raw, min_rating = _build_skill_filters(request)
    skill_rows, categories = fetch_skills_page_data(
        query_text=query_text,
        category=category,
        level=level,
        min_rating=min_rating,
    )
    filters = {"q": query_text, "category": category, "level": level, "min_rating": min_rating_raw}
    return render_template("learn_skill.html", skills=skill_rows, categories=categories, filters=filters)


@app.route("/community")
def community():
    current_user_id = g.current_user["id"] if g.current_user else None
    dashboard_rows = get_db().execute(
        """
        SELECT
            pd.user_id,
            pd.state_json,
            pd.updated_at,
            u.name AS user_name,
            u.profile_image AS user_profile_image
        FROM profile_dashboards pd
        JOIN users u ON u.id = pd.user_id
        WHERE TRIM(COALESCE(pd.state_json, '')) != ''
        ORDER BY datetime(pd.updated_at) DESC, pd.user_id ASC
        """
    ).fetchall()

    community_posts: list[dict] = []
    for row in dashboard_rows:
        if current_user_id is not None and row["user_id"] == current_user_id:
            continue

        try:
            raw_state = json.loads(row["state_json"])
            sanitized_state = sanitize_profile_dashboard_state(raw_state)

            for post in sanitized_state.get("posts", []):
                if not isinstance(post, dict):
                    continue

                post_id = normalize_free_text(post.get("id"), 80)
                if not post_id:
                    continue

                likes_user_ids = normalize_post_like_user_ids(post.get("likesUserIds"))
                attachment = post.get("attachment") if isinstance(post.get("attachment"), dict) else None
                created_at = normalize_free_text(post.get("createdAt"), 40, row["updated_at"] or "")

                community_posts.append(
                    {
                        "owner_id": row["user_id"],
                        "post_id": post_id,
                        "author_name": row["user_name"],
                        "author_profile_image": row["user_profile_image"] or "",
                        "content": normalize_free_text(post.get("content"), 2000),
                        "attachment": attachment,
                        "created_at": created_at,
                        "likes_count": len(likes_user_ids),
                        "liked_by_current_user": bool(
                            current_user_id is not None and current_user_id in likes_user_ids
                        ),
                    }
                )
        except (TypeError, ValueError, KeyError):
            continue

    community_posts.sort(
        key=lambda item: parse_dashboard_datetime(item["created_at"]) or datetime.min,
        reverse=True,
    )

    return render_template("community.html", community_posts=community_posts)


@app.route("/matches")
@login_required
def matches():
    learner_id = g.current_user["id"]
    match_rows, learn_rows = fetch_matches_data(learner_id)

    return render_template("matches.html", matches=match_rows, my_learn_skills=learn_rows)


@app.route("/skills/add", methods=["POST"])
@login_required
def add_skill():
    name = normalize_text(request.form.get("name", ""))
    category = normalize_text(request.form.get("category", ""))
    level = normalize_text(request.form.get("level", ""))
    skill_type = normalize_text(request.form.get("skill_type", "")).lower()

    if not name or not category or not level or not skill_type:
        flash("All skill fields are required.", "danger")
        return redirect(request.referrer or url_for("profile", user_id=g.current_user["id"]))

    if level not in SKILL_LEVELS:
        flash("Invalid skill level selected.", "danger")
        return redirect(request.referrer or url_for("profile", user_id=g.current_user["id"]))
    if skill_type not in SKILL_TYPES:
        flash("Invalid skill type selected.", "danger")
        return redirect(request.referrer or url_for("profile", user_id=g.current_user["id"]))

    existing_skill = get_db().execute(
        "SELECT id FROM skills WHERE user_id = ? AND LOWER(name) = ? AND skill_type = ?",
        (g.current_user["id"], name.lower(), skill_type),
    ).fetchone()
    if existing_skill:
        flash(
            f"You already have '{name}' in your {skill_type} skills. You cannot add the same skill twice.",
            "warning",
        )
        return redirect(request.referrer or url_for("profile", user_id=g.current_user["id"]))

    get_db().execute(
        """
        INSERT INTO skills (user_id, name, category, level, skill_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        (g.current_user["id"], name, category, level, skill_type),
    )
    get_db().commit()
    flash("Skill added successfully.", "success")
    return redirect(request.referrer or url_for("profile", user_id=g.current_user["id"]))


@app.route("/sessions/schedule", methods=["POST"])
@login_required
def schedule_session():
    db = get_db()
    learner_id = g.current_user["id"]

    skill_name = normalize_text(request.form.get("skill_name", ""))
    notes = normalize_text(request.form.get("notes", ""))
    video_platform = normalize_text(request.form.get("video_platform", ""))
    meeting_link = normalize_text(request.form.get("meeting_link", ""))
    mentor_id_raw = request.form.get("mentor_id", "")
    scheduled_raw = request.form.get("scheduled_for", "")

    if video_platform not in VIDEO_PLATFORMS:
        flash("Please select a valid video platform.", "danger")
        return redirect(request.referrer or url_for("matches"))

    if not meeting_link:
        flash("Meeting link is required for the appointment.", "danger")
        return redirect(request.referrer or url_for("matches"))

    try:
        mentor_id = int(mentor_id_raw)
    except (TypeError, ValueError):
        flash("Invalid mentor selected.", "danger")
        return redirect(request.referrer or url_for("matches"))

    if mentor_id == learner_id:
        flash("You cannot schedule a session with yourself.", "danger")
        return redirect(request.referrer or url_for("matches"))

    mentor = db.execute("SELECT id FROM users WHERE id = ?", (mentor_id,)).fetchone()
    if mentor is None:
        flash("Selected mentor does not exist.", "danger")
        return redirect(request.referrer or url_for("matches"))

    try:
        scheduled_dt = datetime.fromisoformat(scheduled_raw)
    except ValueError:
        flash("Please provide a valid date and time.", "danger")
        return redirect(request.referrer or url_for("matches"))

    existing_session = db.execute(
        "SELECT id FROM sessions WHERE mentor_id = ? AND skill_name = ? AND status = ?",
        (mentor_id, skill_name, "scheduled"),
    ).fetchone()
    if existing_session:
        flash(
            f"This mentor already has a scheduled session for '{skill_name}'. Please choose a different skill or wait for the current session to complete/be cancelled.",
            "danger",
        )
        return redirect(request.referrer or url_for("matches"))

    learner_duplicate = db.execute(
        "SELECT id FROM sessions WHERE learner_id = ? AND mentor_id = ? AND skill_name = ? AND status IN (?, ?)",
        (learner_id, mentor_id, skill_name, "scheduled", "completed"),
    ).fetchone()
    if learner_duplicate:
        flash(
            f"You already have an active or completed session with this mentor for '{skill_name}'. Please wait until it ends or is cancelled before booking again.",
            "danger",
        )
        return redirect(request.referrer or url_for("matches"))

    overlapping_session = db.execute(
        """
        SELECT id FROM sessions
        WHERE learner_id = ?
          AND status IN (?, ?)
          AND datetime(scheduled_for) < datetime(?)
          AND datetime(datetime(scheduled_for, '+1 hour')) > datetime(?)
        """,
        (learner_id, "scheduled", "completed", (scheduled_dt + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:00"), scheduled_dt.strftime("%Y-%m-%d %H:%M:00")),
    ).fetchone()
    if overlapping_session:
        flash(
            "You already have another session scheduled during this time (sessions are assumed to be 1 hour). Please choose a different time.",
            "danger",
        )
        return redirect(request.referrer or url_for("matches"))

    db.execute(
        """
        INSERT INTO sessions (learner_id, mentor_id, skill_name, scheduled_for, video_platform, meeting_link, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            learner_id,
            mentor_id,
            skill_name,
            scheduled_dt.strftime("%Y-%m-%d %H:%M"),
            video_platform,
            meeting_link,
            notes,
        ),
    )
    db.commit()
    flash("Session scheduled successfully.", "success")
    return redirect(request.referrer or url_for("profile", user_id=learner_id))


@app.route("/sessions/<int:session_id>/status", methods=["POST"])
@login_required
def update_session_status(session_id: int):
    db = get_db()
    status = normalize_text(request.form.get("status", "")).lower()
    if status not in {"completed", "cancelled"}:
        flash("Invalid status.", "danger")
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    session_row = db.execute(
        "SELECT id, learner_id, mentor_id, status FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session_row:
        flash("Session not found.", "danger")
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    user_id = g.current_user["id"]
    is_mentor = user_id == session_row["mentor_id"]
    is_learner = user_id == session_row["learner_id"]

    if not (is_mentor or is_learner):
        flash("You are not authorized to update this session.", "danger")
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    if status == "completed" and not is_mentor:
        flash("Only mentor can mark session as completed.", "warning")
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    db.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
    db.commit()
    flash("Session status updated.", "success")
    return redirect(url_for("profile", user_id=g.current_user["id"]))


@app.route("/sessions/<int:session_id>/review", methods=["POST"])
@login_required
def submit_review(session_id: int):
    db = get_db()
    row = db.execute(
        """
        SELECT id, learner_id, mentor_id, status
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if not row:
        flash("Session not found.", "danger")
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    user_id = g.current_user["id"]
    if user_id not in (row["learner_id"], row["mentor_id"]):
        flash("You cannot review this session.", "danger")
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    if row["status"] != "completed":
        flash("Only completed sessions can be reviewed.", "warning")
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    rating_raw = request.form.get("rating", "")
    comment = normalize_text(request.form.get("comment", ""))
    try:
        rating = int(rating_raw)
    except ValueError:
        flash("Invalid rating value.", "danger")
        return redirect(url_for("profile", user_id=g.current_user["id"]))
    if rating < 1 or rating > 5:
        flash("Rating must be between 1 and 5.", "danger")
        return redirect(url_for("profile", user_id=g.current_user["id"]))

    reviewee_id = row["mentor_id"] if user_id == row["learner_id"] else row["learner_id"]

    db.execute(
        """
        INSERT INTO reviews (session_id, reviewer_id, reviewee_id, rating, comment)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id, reviewer_id)
        DO UPDATE SET
            rating = excluded.rating,
            comment = excluded.comment,
            created_at = CURRENT_TIMESTAMP
        """,
        (session_id, user_id, reviewee_id, rating, comment),
    )
    db.commit()
    flash("Review submitted successfully.", "success")
    return redirect(url_for("profile", user_id=g.current_user["id"]))


@app.route("/profile")
def profile():
    if g.current_user:
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    first_user_id = fetch_first_user_id()
    if first_user_id is None:
        flash("No users available yet. Please register first.", "info")
        return redirect(url_for("register"))
    return redirect(url_for("profile_by_id", user_id=first_user_id))


@app.route("/profile/<int:user_id>")
def profile_by_id(user_id: int):
    current_user_id = g.current_user["id"] if g.current_user else None
    profile_data = fetch_profile_page_data(user_id=user_id, current_user_id=current_user_id)

    if not profile_data["user"]:
        flash("Profile not found.", "danger")
        return redirect(url_for("home"))

    is_owner = bool(g.current_user and g.current_user["id"] == user_id)

    return render_template(
        "profile.html",
        user=profile_data["user"],
        teach_skills=profile_data["teach_skills"],
        learn_skills=profile_data["learn_skills"],
        rating_summary=profile_data["rating_summary"],
        profile_reviews=profile_data["profile_reviews"],
        is_owner=is_owner,
        my_sessions=profile_data["my_sessions"],
        dashboard_state=get_profile_dashboard_state(user_id),
    )


@app.route("/edit_profile")
@app.route("/profile/edit")
@app.route("/profile/<int:user_id>/edit")
@login_required
def edit_profile(user_id: int | None = None):
    current_user_id = g.current_user["id"]
    if user_id is not None and user_id != current_user_id:
        flash("You can only edit your own profile.", "warning")
        return redirect(url_for("profile_by_id", user_id=current_user_id))

    user_id = current_user_id
    profile_data = fetch_profile_page_data(user_id=user_id, current_user_id=user_id)

    if not profile_data["user"]:
        flash("Profile not found.", "danger")
        return redirect(url_for("home"))

    return render_template(
        "edit_profile.html",
        user=profile_data["user"],
        dashboard_state=get_profile_dashboard_state(user_id),
    )


@app.route("/profile/basic", methods=["POST"])
@login_required
def update_profile_basic():
    display_name = normalize_free_text(request.form.get("name"), 80)
    bio = normalize_free_text(request.form.get("bio"), 1200)

    if not display_name:
        flash("Name cannot be empty.", "danger")
        return redirect(url_for("edit_profile"))

    db = get_db()
    db.execute(
        "UPDATE users SET name = ?, bio = ? WHERE id = ?",
        (display_name, bio, g.current_user["id"]),
    )
    db.commit()

    flash("Basic profile details updated.", "success")
    return redirect(url_for("edit_profile", user_id=g.current_user["id"]))


@app.route("/profile/dashboard/state", methods=["POST"])
@login_required
def save_profile_dashboard_state():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"ok": False, "message": "Invalid request payload."}, 400

    state = sanitize_profile_dashboard_state(payload.get("state"))
    
    db_state = dict(state)
    db_state["resume"] = {"name": state["resume"].get("name", ""), "dataUrl": ""}
    db_state["certificates"] = [
        {"id": cert.get("id"), "name": cert.get("name"), "type": cert.get("type"), "createdAt": cert.get("createdAt"), "dataUrl": ""}
        for cert in state.get("certificates", [])
    ]
    db_state["projects"] = [
        {**proj, "imageDataUrl": ""} for proj in state.get("projects", [])
    ]
    
    serialized_state = json.dumps(db_state, ensure_ascii=False, separators=(",", ":"))
    if len(serialized_state.encode("utf-8")) > app.config["PROFILE_DASHBOARD_MAX_BYTES"]:
        return {"ok": False, "message": "Profile data is too large. Reduce media size or items."}, 413

    save_profile_dashboard_state_for_user(g.current_user["id"], db_state)
    return {"ok": True, "state": state}


@app.route("/profile/<int:user_id>/posts/<path:post_id>/like", methods=["POST"])
@login_required
def toggle_profile_post_like(user_id: int, post_id: str):
    dashboard_state = get_profile_dashboard_state(user_id)
    posts = dashboard_state.get("posts", [])

    target_post: dict | None = None
    for post_item in posts:
        if not isinstance(post_item, dict):
            continue
        if str(post_item.get("id", "")) == post_id:
            target_post = post_item
            break

    if target_post is None:
        return {"ok": False, "message": "Post not found."}, 404

    viewer_id = g.current_user["id"]
    likes_user_ids = normalize_post_like_user_ids(target_post.get("likesUserIds"))

    if viewer_id in likes_user_ids:
        likes_user_ids.remove(viewer_id)
        liked = False
    else:
        likes_user_ids.append(viewer_id)
        liked = True

    target_post["likesUserIds"] = likes_user_ids

    sanitized_state = sanitize_profile_dashboard_state(dashboard_state)
    serialized_state = json.dumps(sanitized_state, ensure_ascii=False, separators=(",", ":"))
    if len(serialized_state.encode("utf-8")) > app.config["PROFILE_DASHBOARD_MAX_BYTES"]:
        return {"ok": False, "message": "Profile data is too large. Reduce media size or items."}, 413

    save_profile_dashboard_state_for_user(user_id, sanitized_state)
    return {"ok": True, "liked": liked, "likesCount": len(likes_user_ids)}


@app.route("/media/profile/<path:filename>")
@login_required
def profile_image_file(filename: str):
    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename or not is_allowed_profile_image(safe_name):
        abort(404)

    primary_folder = app.config["PROFILE_UPLOAD_FOLDER"]
    file_path = os.path.join(primary_folder, safe_name)
    if os.path.exists(file_path):
        return send_from_directory(primary_folder, safe_name)

    legacy_folder = app.config["PROFILE_UPLOAD_LEGACY_FOLDER"]
    legacy_path = os.path.join(legacy_folder, safe_name)
    if os.path.exists(legacy_path):
        return send_from_directory(legacy_folder, safe_name)

    abort(404)


@app.route("/profile/picture", methods=["POST"])
@login_required
def update_profile_picture():
    uploaded_file = request.files.get("profile_picture")
    if uploaded_file is None or not uploaded_file.filename:
        flash("Please select an image file.", "danger")
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    safe_name = secure_filename(uploaded_file.filename)
    if not safe_name:
        flash("Invalid filename. Please rename the file and try again.", "danger")
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    if get_uploaded_file_size(uploaded_file) > app.config["PROFILE_UPLOAD_MAX_BYTES"]:
        flash("Profile picture is too large. Maximum allowed size is 4MB.", "danger")
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    if not is_allowed_profile_image(safe_name):
        flash("Invalid file type. Use JPG, PNG, GIF, or WEBP.", "danger")
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    requested_extension = normalize_profile_image_extension(os.path.splitext(safe_name)[1])
    detected_extension = detect_profile_image_extension(uploaded_file.stream)
    if detected_extension is None:
        flash("Invalid image content. Use a valid JPG, PNG, GIF, or WEBP file.", "danger")
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    if requested_extension != detected_extension:
        flash("File extension does not match file content.", "danger")
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    uploaded_file.stream.seek(0)
    extension = f".{detected_extension}"
    new_filename = f"user_{g.current_user['id']}_{uuid4().hex}{extension}"
    save_path = os.path.join(app.config["PROFILE_UPLOAD_FOLDER"], new_filename)

    try:
        uploaded_file.save(save_path)
    except OSError:
        flash("Could not save the image. Please try again.", "danger")
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    db = get_db()
    old_image_row = db.execute(
        "SELECT profile_image FROM users WHERE id = ?", (g.current_user["id"],)
    ).fetchone()
    old_image = old_image_row["profile_image"] if old_image_row else ""

    db.execute(
        "UPDATE users SET profile_image = ? WHERE id = ?",
        (new_filename, g.current_user["id"]),
    )
    db.commit()

    if old_image and old_image != new_filename:
        delete_profile_image_file(old_image)

    flash("Profile picture updated.", "success")
    return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))


@app.route("/profile/picture/remove", methods=["POST"])
@login_required
def remove_profile_picture():
    db = get_db()
    row = db.execute(
        "SELECT profile_image FROM users WHERE id = ?", (g.current_user["id"],)
    ).fetchone()
    current_image = row["profile_image"] if row else ""

    if not current_image:
        flash("No profile picture to remove.", "info")
        return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))

    db.execute(
        "UPDATE users SET profile_image = '' WHERE id = ?", (g.current_user["id"],)
    )
    db.commit()
    delete_profile_image_file(current_image)

    flash("Profile picture removed.", "success")
    return redirect(url_for("profile_by_id", user_id=g.current_user["id"]))


@app.route("/sessions/<int:session_id>")
@login_required
def session_detail(session_id: int):
    db = get_db()
    user_id = g.current_user["id"]
    session_row = db.execute(
        """
        SELECT
            se.id,
            se.skill_name,
            se.scheduled_for,
            se.video_platform,
            se.meeting_link,
            se.status,
            se.notes,
            se.learner_id,
            se.mentor_id,
            learner.name AS learner_name,
            mentor.name AS mentor_name
        FROM sessions se
        JOIN users learner ON learner.id = se.learner_id
        JOIN users mentor ON mentor.id = se.mentor_id
        WHERE se.id = ?
        """,
        (session_id,),
    ).fetchone()

    if not session_row:
        flash("Session not found.", "danger")
        return redirect(url_for("profile"))

    if user_id not in (session_row["learner_id"], session_row["mentor_id"]):
        flash("You are not allowed to view this talking section.", "danger")
        return redirect(url_for("profile"))

    messages = db.execute(
        """
        SELECT sm.id, sm.message, sm.created_at, sm.sender_id, u.name AS sender_name
        FROM session_messages sm
        JOIN users u ON u.id = sm.sender_id
        WHERE sm.session_id = ?
        ORDER BY datetime(sm.created_at) ASC, sm.id ASC
        """,
        (session_id,),
    ).fetchall()

    latest_message_id = messages[-1]["id"] if messages else 0
    db.execute(
        """
        INSERT INTO session_reads (session_id, user_id, last_read_message_id, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, user_id)
        DO UPDATE SET
            last_read_message_id = CASE
                WHEN excluded.last_read_message_id > session_reads.last_read_message_id
                    THEN excluded.last_read_message_id
                ELSE session_reads.last_read_message_id
            END,
            updated_at = CURRENT_TIMESTAMP
        """,
        (session_id, user_id, latest_message_id),
    )
    db.commit()

    return render_template(
        "session_detail.html",
        session_item=session_row,
        messages=messages,
    )


@app.route("/sessions/<int:session_id>/messages", methods=["POST"])
@login_required
def add_session_message(session_id: int):
    db = get_db()
    user_id = g.current_user["id"]
    session_row = db.execute(
        "SELECT id, learner_id, mentor_id, status FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()

    if not session_row:
        flash("Session not found.", "danger")
        return redirect(url_for("profile"))

    if user_id not in (session_row["learner_id"], session_row["mentor_id"]):
        flash("You are not allowed to post in this talking section.", "danger")
        return redirect(url_for("profile"))

    if session_row["status"] == "cancelled":
        flash("Cannot post messages in a cancelled appointment.", "warning")
        return redirect(url_for("session_detail", session_id=session_id))

    message = normalize_text(request.form.get("message", ""))
    if not message:
        flash("Message cannot be empty.", "danger")
        return redirect(url_for("session_detail", session_id=session_id))

    if len(message) > 1000:
        flash("Message is too long. Keep it under 1000 characters.", "warning")
        return redirect(url_for("session_detail", session_id=session_id))

    cursor = db.execute(
        "INSERT INTO session_messages (session_id, sender_id, message) VALUES (?, ?, ?)",
        (session_id, user_id, message),
    )
    db.execute(
        """
        INSERT INTO session_reads (session_id, user_id, last_read_message_id, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, user_id)
        DO UPDATE SET
            last_read_message_id = CASE
                WHEN excluded.last_read_message_id > session_reads.last_read_message_id
                    THEN excluded.last_read_message_id
                ELSE session_reads.last_read_message_id
            END,
            updated_at = CURRENT_TIMESTAMP
        """,
        (session_id, user_id, cursor.lastrowid),
    )
    db.commit()
    flash("Message sent.", "success")
    return redirect(url_for("session_detail", session_id=session_id))


# ── Discussion Board ──────────────────────────────────────────────────────────

@app.route("/discuss/private", methods=["GET", "POST"])
@login_required
def private_discussions():
    db = get_db()
    user_id = g.current_user["id"]

    if request.method == "POST":
        title = normalize_text(request.form.get("title", ""))
        if not title:
            flash("Discussion title is required.", "danger")
            return redirect(url_for("private_discussions"))
        if len(title) > 120:
            flash("Discussion title must be 120 characters or less.", "warning")
            return redirect(url_for("private_discussions"))

        invite_code = generate_private_discussion_invite_code(db)
        cursor = db.execute(
            """
            INSERT INTO private_discussions (owner_id, title, invite_code)
            VALUES (?, ?, ?)
            """,
            (user_id, title, invite_code),
        )
        discussion_id = cursor.lastrowid
        add_private_discussion_member(db, discussion_id, user_id)
        db.commit()

        flash("Private discussion created. Share the invite code or link.", "success")
        return redirect(url_for("private_discussion_room", discussion_id=discussion_id))

    private_rooms = db.execute(
        """
        SELECT
            pd.id,
            pd.title,
            pd.invite_code,
            pd.created_at,
            pd.owner_id,
            owner.name AS owner_name,
            (
                SELECT COUNT(*)
                FROM private_discussion_members pm
                WHERE pm.discussion_id = pd.id
            ) AS member_count,
            (
                SELECT COUNT(*)
                FROM private_discussion_messages msg
                WHERE msg.discussion_id = pd.id
            ) AS message_count
        FROM private_discussions pd
        JOIN private_discussion_members me
            ON me.discussion_id = pd.id
           AND me.user_id = ?
        JOIN users owner ON owner.id = pd.owner_id
        ORDER BY datetime(pd.created_at) DESC, pd.id DESC
        """,
        (user_id,),
    ).fetchall()

    return render_template("private_discussions.html", private_rooms=private_rooms)


@app.route("/discuss/private/join", methods=["POST"])
@login_required
def join_private_discussion_by_code():
    invite_code = normalize_text(request.form.get("invite_code", "")).upper()
    if not invite_code:
        flash("Invite code is required.", "danger")
        return redirect(url_for("private_discussions"))

    db = get_db()
    discussion = db.execute(
        """
        SELECT id, title
        FROM private_discussions
        WHERE invite_code = ?
        """,
        (invite_code,),
    ).fetchone()
    if discussion is None:
        flash("Invite code is invalid.", "danger")
        return redirect(url_for("private_discussions"))

    user_id = g.current_user["id"]
    already_member = user_is_private_discussion_member(db, discussion["id"], user_id)
    add_private_discussion_member(db, discussion["id"], user_id)
    db.commit()

    if already_member:
        flash("You are already a member of this private discussion.", "info")
    else:
        flash(f"Joined private discussion: {discussion['title']}", "success")

    return redirect(url_for("private_discussion_room", discussion_id=discussion["id"]))


@app.route("/discuss/private/join/<string:invite_code>")
@login_required
def join_private_discussion_by_link(invite_code: str):
    normalized_code = normalize_text(invite_code).upper()
    if not normalized_code:
        flash("Invalid invite link.", "danger")
        return redirect(url_for("private_discussions"))

    db = get_db()
    discussion = db.execute(
        """
        SELECT id, title
        FROM private_discussions
        WHERE invite_code = ?
        """,
        (normalized_code,),
    ).fetchone()
    if discussion is None:
        flash("Invite link is invalid or expired.", "danger")
        return redirect(url_for("private_discussions"))

    user_id = g.current_user["id"]
    already_member = user_is_private_discussion_member(db, discussion["id"], user_id)
    add_private_discussion_member(db, discussion["id"], user_id)
    db.commit()

    if already_member:
        flash("You are already in this private discussion.", "info")
    else:
        flash(f"Joined private discussion: {discussion['title']}", "success")

    return redirect(url_for("private_discussion_room", discussion_id=discussion["id"]))


@app.route("/discuss/private/<int:discussion_id>")
@login_required
def private_discussion_room(discussion_id: int):
    db = get_db()
    user_id = g.current_user["id"]

    discussion = db.execute(
        """
        SELECT
            pd.id,
            pd.title,
            pd.invite_code,
            pd.created_at,
            pd.owner_id,
            owner.name AS owner_name
        FROM private_discussions pd
        JOIN users owner ON owner.id = pd.owner_id
        WHERE pd.id = ?
        """,
        (discussion_id,),
    ).fetchone()

    if discussion is None:
        flash("Private discussion not found.", "danger")
        return redirect(url_for("private_discussions"))

    if not user_is_private_discussion_member(db, discussion_id, user_id):
        flash("You are not a member of this private discussion.", "danger")
        return redirect(url_for("private_discussions"))

    members = db.execute(
        """
        SELECT u.id, u.name
        FROM private_discussion_members pm
        JOIN users u ON u.id = pm.user_id
        WHERE pm.discussion_id = ?
        ORDER BY u.name ASC
        """,
        (discussion_id,),
    ).fetchall()

    messages = db.execute(
        """
        SELECT
            msg.id,
            msg.message,
            msg.created_at,
            msg.sender_id,
            u.name AS sender_name
        FROM private_discussion_messages msg
        JOIN users u ON u.id = msg.sender_id
        WHERE msg.discussion_id = ?
        ORDER BY datetime(msg.created_at) ASC, msg.id ASC
        """,
        (discussion_id,),
    ).fetchall()

    invite_link = url_for(
        "join_private_discussion_by_link",
        invite_code=discussion["invite_code"],
        _external=True,
    )

    return render_template(
        "private_discussion_room.html",
        discussion=discussion,
        members=members,
        messages=messages,
        invite_link=invite_link,
    )


@app.route("/discuss/private/<int:discussion_id>/messages", methods=["POST"])
@login_required
def private_discussion_message(discussion_id: int):
    db = get_db()
    user_id = g.current_user["id"]

    if not user_is_private_discussion_member(db, discussion_id, user_id):
        flash("You are not allowed to post in this private discussion.", "danger")
        return redirect(url_for("private_discussions"))

    message = normalize_text(request.form.get("message", ""))
    if not message:
        flash("Message cannot be empty.", "danger")
        return redirect(url_for("private_discussion_room", discussion_id=discussion_id))
    if len(message) > 2000:
        flash("Message must be 2000 characters or less.", "warning")
        return redirect(url_for("private_discussion_room", discussion_id=discussion_id))

    db.execute(
        """
        INSERT INTO private_discussion_messages (discussion_id, sender_id, message)
        VALUES (?, ?, ?)
        """,
        (discussion_id, user_id, message),
    )
    db.commit()

    return redirect(url_for("private_discussion_room", discussion_id=discussion_id))


@app.route("/discuss")
def discuss():
    db = get_db()
    page_raw = request.args.get("page", "1")
    try:
        page = int(page_raw)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)
    per_page = 15
    offset = (page - 1) * per_page
    q = normalize_text(request.args.get("q", ""))
    topic = normalize_text(request.args.get("topic", ""))

    where_clauses: list[str] = []
    params: list[str] = []
    if q:
        where_clauses.append("(dp.title LIKE ? OR dp.body LIKE ? OR dp.tags LIKE ?)")
        like_q = f"%{q}%"
        params.extend([like_q, like_q, like_q])
    if topic and topic in DISCUSSION_TOPICS:
        where_clauses.append("dp.topic = ?")
        params.append(topic)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    posts = db.execute(
        f"""
        SELECT
            dp.id,
            dp.title,
            dp.body,
            dp.topic,
            dp.tags,
            dp.created_at,
            u.name AS author_name,
            u.id AS author_id,
            COUNT(dr.id) AS reply_count
        FROM discussion_posts dp
        JOIN users u ON u.id = dp.user_id
        LEFT JOIN discussion_replies dr ON dr.post_id = dp.id
        {where_sql}
        GROUP BY dp.id
        ORDER BY datetime(dp.created_at) DESC, dp.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()

    total = db.execute(
        f"SELECT COUNT(*) FROM discussion_posts dp {where_sql}",
        params,
    ).fetchone()[0]

    total_pages = max(1, -(-total // per_page))
    return render_template(
        "discuss.html",
        posts=posts,
        page=page,
        total_pages=total_pages,
        q=q,
        topic=topic,
    )


@app.route("/discuss/new", methods=["GET", "POST"])
@login_required
def discuss_new():
    if request.method == "POST":
        db = get_db()
        title = normalize_text(request.form.get("title", ""))
        body = normalize_text(request.form.get("body", ""))
        topic = normalize_text(request.form.get("topic", "General"))
        tags = normalize_tags(request.form.get("tags", ""))
        if not title or not body:
            flash("Title and body are required.", "danger")
            return redirect(url_for("discuss_new"))
        if topic not in DISCUSSION_TOPICS:
            flash("Please choose a valid topic.", "danger")
            return redirect(url_for("discuss_new"))
        if len(title) > 200:
            flash("Title must be 200 characters or less.", "warning")
            return redirect(url_for("discuss_new"))
        if len(body) > 5000:
            flash("Body must be 5000 characters or less.", "warning")
            return redirect(url_for("discuss_new"))
        post = db.execute(
            "INSERT INTO discussion_posts (user_id, title, body, topic, tags) VALUES (?, ?, ?, ?, ?)",
            (g.current_user["id"], title, body, topic, tags),
        )
        db.commit()
        flash("Discussion posted!", "success")
        return redirect(url_for("discuss_post", post_id=post.lastrowid))
    return render_template("discuss_new.html")


@app.route("/discuss/<int:post_id>")
def discuss_post(post_id: int):
    db = get_db()
    post = db.execute(
        """
        SELECT dp.id, dp.title, dp.body, dp.topic, dp.tags, dp.created_at, dp.user_id,
               u.name AS author_name, u.id AS author_id, u.profile_image AS author_image
        FROM discussion_posts dp
        JOIN users u ON u.id = dp.user_id
        WHERE dp.id = ?
        """,
        (post_id,),
    ).fetchone()
    if not post:
        flash("Discussion not found.", "danger")
        return redirect(url_for("discuss"))
    replies = db.execute(
        """
        SELECT dr.id, dr.body, dr.created_at, dr.user_id,
               u.name AS author_name, u.profile_image AS author_image
        FROM discussion_replies dr
        JOIN users u ON u.id = dr.user_id
        WHERE dr.post_id = ?
        ORDER BY dr.created_at ASC
        """,
        (post_id,),
    ).fetchall()
    return render_template("discuss_post.html", post=post, replies=replies)


@app.route("/discuss/<int:post_id>/reply", methods=["POST"])
@login_required
def discuss_reply(post_id: int):
    db = get_db()
    post = db.execute("SELECT id FROM discussion_posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        flash("Discussion not found.", "danger")
        return redirect(url_for("discuss"))
    body = normalize_text(request.form.get("body", ""))
    if not body:
        flash("Reply cannot be empty.", "danger")
        return redirect(url_for("discuss_post", post_id=post_id))
    if len(body) > 2000:
        flash("Reply must be 2000 characters or less.", "warning")
        return redirect(url_for("discuss_post", post_id=post_id))
    db.execute(
        "INSERT INTO discussion_replies (post_id, user_id, body) VALUES (?, ?, ?)",
        (post_id, g.current_user["id"], body),
    )
    db.commit()
    flash("Reply posted!", "success")
    return redirect(url_for("discuss_post", post_id=post_id))


@app.route("/discuss/<int:post_id>/report", methods=["POST"])
@login_required
def discuss_report_post(post_id: int):
    db = get_db()
    post = db.execute("SELECT id, user_id FROM discussion_posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        flash("Discussion not found.", "danger")
        return redirect(url_for("discuss"))
    if post["user_id"] == g.current_user["id"]:
        flash("You cannot report your own discussion.", "info")
        return redirect(url_for("discuss_post", post_id=post_id))

    reason = normalize_text(request.form.get("reason", ""))
    details = normalize_text(request.form.get("details", ""))
    if reason not in REPORT_REASONS:
        flash("Please select a valid report reason.", "danger")
        return redirect(url_for("discuss_post", post_id=post_id))

    cursor = db.execute(
        """
        INSERT OR IGNORE INTO discussion_reports (reporter_id, post_id, reason, details)
        VALUES (?, ?, ?, ?)
        """,
        (g.current_user["id"], post_id, reason, details),
    )
    db.commit()
    if cursor.rowcount == 0:
        flash("You already reported this discussion.", "info")
    else:
        flash("Thanks for reporting. The moderation team has been notified.", "success")
    return redirect(url_for("discuss_post", post_id=post_id))


@app.route("/discuss/<int:post_id>/replies/<int:reply_id>/report", methods=["POST"])
@login_required
def discuss_report_reply(post_id: int, reply_id: int):
    db = get_db()
    reply = db.execute(
        """
        SELECT dr.id, dr.user_id
        FROM discussion_replies dr
        WHERE dr.id = ? AND dr.post_id = ?
        """,
        (reply_id, post_id),
    ).fetchone()
    if not reply:
        flash("Reply not found.", "danger")
        return redirect(url_for("discuss_post", post_id=post_id))
    if reply["user_id"] == g.current_user["id"]:
        flash("You cannot report your own reply.", "info")
        return redirect(url_for("discuss_post", post_id=post_id))

    reason = normalize_text(request.form.get("reason", ""))
    details = normalize_text(request.form.get("details", ""))
    if reason not in REPORT_REASONS:
        flash("Please select a valid report reason.", "danger")
        return redirect(url_for("discuss_post", post_id=post_id))

    cursor = db.execute(
        """
        INSERT OR IGNORE INTO discussion_reports (reporter_id, reply_id, reason, details)
        VALUES (?, ?, ?, ?)
        """,
        (g.current_user["id"], reply_id, reason, details),
    )
    db.commit()
    if cursor.rowcount == 0:
        flash("You already reported this reply.", "info")
    else:
        flash("Reply reported. Thanks for helping keep discussions safe.", "success")
    return redirect(url_for("discuss_post", post_id=post_id))


@app.route("/discuss/<int:post_id>/delete", methods=["POST"])
@login_required
def discuss_delete(post_id: int):
    db = get_db()
    post = db.execute("SELECT id, user_id FROM discussion_posts WHERE id = ?", (post_id,)).fetchone()
    if not post or post["user_id"] != g.current_user["id"]:
        flash("Not allowed.", "danger")
        return redirect(url_for("discuss"))
    db.execute("DELETE FROM discussion_posts WHERE id = ?", (post_id,))
    db.commit()
    flash("Discussion deleted.", "info")
    return redirect(url_for("discuss"))


@app.route("/discuss/<int:post_id>/replies/<int:reply_id>/delete", methods=["POST"])
@login_required
def discuss_reply_delete(post_id: int, reply_id: int):
    db = get_db()
    reply = db.execute(
        "SELECT id, user_id FROM discussion_replies WHERE id = ? AND post_id = ?",
        (reply_id, post_id),
    ).fetchone()
    if not reply or reply["user_id"] != g.current_user["id"]:
        flash("Not allowed.", "danger")
        return redirect(url_for("discuss_post", post_id=post_id))
    db.execute("DELETE FROM discussion_replies WHERE id = ?", (reply_id,))
    db.commit()
    flash("Reply deleted.", "info")
    return redirect(url_for("discuss_post", post_id=post_id))


@app.route("/admin/reports")
@admin_required
def admin_reports():
    db = get_db()
    report_rows = db.execute(
        """
        SELECT
            rp.id,
            rp.reason,
            rp.details,
            rp.created_at,
            rp.post_id,
            rp.reply_id,
            reporter.name AS reporter_name,
            dp.title AS post_title,
            dp.body AS post_body,
            dp.id AS discussion_id,
            post_author.name AS post_author_name,
            dr.body AS reply_body,
            dr.post_id AS reply_post_id,
            reply_author.name AS reply_author_name
        FROM discussion_reports rp
        JOIN users reporter ON reporter.id = rp.reporter_id
        LEFT JOIN discussion_posts dp ON dp.id = rp.post_id
        LEFT JOIN users post_author ON post_author.id = dp.user_id
        LEFT JOIN discussion_replies dr ON dr.id = rp.reply_id
        LEFT JOIN users reply_author ON reply_author.id = dr.user_id
        WHERE rp.status = 'open'
        ORDER BY datetime(rp.created_at) DESC, rp.id DESC
        """
    ).fetchall()
    return render_template("admin_reports.html", reports=report_rows)


@app.route("/admin/reports/<int:report_id>/action", methods=["POST"])
@admin_required
def admin_report_action(report_id: int):
    db = get_db()
    report = db.execute(
        "SELECT id, post_id, reply_id FROM discussion_reports WHERE id = ? AND status = 'open'",
        (report_id,),
    ).fetchone()
    if not report:
        flash("Report not found or already resolved.", "warning")
        return redirect(url_for("admin_reports"))

    action = normalize_text(request.form.get("action", "")).lower()
    if action == "resolve":
        db.execute(
            """
            UPDATE discussion_reports
            SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, resolved_by = ?
            WHERE id = ?
            """,
            (g.current_user["id"], report_id),
        )
        db.commit()
        flash("Report marked as resolved.", "success")
        return redirect(url_for("admin_reports"))

    if action == "remove_post" and report["post_id"]:
        db.execute("DELETE FROM discussion_posts WHERE id = ?", (report["post_id"],))
        db.commit()
        flash("Discussion removed.", "success")
        return redirect(url_for("admin_reports"))

    if action == "remove_reply" and report["reply_id"]:
        db.execute("DELETE FROM discussion_replies WHERE id = ?", (report["reply_id"],))
        db.commit()
        flash("Reply removed.", "success")
        return redirect(url_for("admin_reports"))

    flash("Invalid moderation action.", "danger")
    return redirect(url_for("admin_reports"))


with app.app_context():
    migrate_legacy_profile_images()
    initialize_database()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")