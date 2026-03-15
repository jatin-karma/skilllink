# SkillLink — Peer-to-Peer Skill Exchange

A Flask web application where students teach what they know and learn what they need through peer-to-peer sessions.

---

## Overview

SkillLink connects students as mentors and learners. Users add the skills they can teach or want to learn, get auto-matched with peers, schedule live sessions, and build a verified community profile through reviews and ratings.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13+, Flask 3.1 |
| ORM / DB | Flask-SQLAlchemy 3.1, SQLite |
| Templates | Jinja2 |
| Frontend | Vanilla JS, custom CSS (no frameworks) |
| Auth | Session-based with CSRF protection |

---

## Project Structure

```
app.py               # Flask app, all routes, auth, sanitization
database.py          # SQLite helpers, schema migrations, seed data
models.py            # SQLAlchemy table models and indexes
extensions.py        # SQLAlchemy extension instance
query_services.py    # Extracted query logic (skills / matches / profile)
requirements.txt     # Pinned Python dependencies
templates/           # Jinja2 HTML templates
  base.html            # Shared layout, navbar, footer, scripts
  index.html           # Homepage
  become_mentor.html   # Become a Mentor page
  learn_skill.html     # Learn New Skill / browse & book page
  profile.html         # User profile view
  edit_profile.html    # Profile edit page
  skills.html          # Full skill search (legacy /skills route)
  matches.html         # Recommended mentor matches
  discuss.html         # Discussion board list
  discuss_new.html     # New discussion post
  discuss_post.html    # Discussion thread view
  session_detail.html  # Session detail + messaging
  admin_reports.html   # Admin moderation queue
  login.html           # Sign-in page
  register.html        # Registration page
static/
  css/style.css        # Global stylesheet
  media/images/        # Logos, banners, skill thumbnails
  media/videos/        # Hero background videos
uploads/
  profile_pics/        # User-uploaded profile pictures (git-ignored)
```

---

## Features

### Navigation
- **Become a Mentor** page — how-to steps, benefits, active mentor showcase
- **Learn New Skill** page — searchable skill cards with book-session forms
- **Discuss** — community discussion board
- Auto-hiding sticky navbar with profile avatar dropdown

### Authentication & Profile
- Register / login / logout with hashed passwords
- Dedicated **Edit Profile** page (`/profile/edit`)
  - Name, bio, profile picture upload/remove (JPG/PNG/GIF/WEBP, max 4 MB)
  - Kicker label, headline, about, banner image
  - Social links: LinkedIn, GitHub, Portfolio
  - Education details persisted server-side
- Profile dashboard state (education, certificates, projects, posts, XP) saved in SQLite via `/profile/dashboard/state`
- Banner image compressed client-side before upload to stay within payload limits
- Profile image served via login-protected route `/media/profile/<filename>`

### Skills & Matching
- Add teach / learn skills (name, category, level)
- Search and filter by text, category, level, and minimum rating
- Auto-matching: learner's "learn" skills matched against mentor "teach" skills

### Sessions
- Book sessions directly from skill cards (date/time, video platform, meeting link)
- Session status lifecycle: scheduled → completed / cancelled
- In-session messaging with unread indicator
- Post-session reviews and star ratings

### Discussion & Moderation
- Create discussion posts with topic tags
- Threaded replies
- Report posts/replies (spam, abuse, harassment, etc.)
- Admin-only moderation queue at `/admin/reports`

### Security
- CSRF token on every state-changing form and fetch request
- Secure cookie flags (`HttpOnly`, `SameSite=Lax`, `Secure` in production)
- File upload validation: extension allowlist + binary magic-byte check
- `SECRET_KEY` read from environment variable; app refuses to start in production with the default value
- No-cache headers on all HTML responses during development
- XSS-safe: all user content rendered via Jinja2 auto-escaping; data URLs allowlisted before storage

---

## Local Setup (Windows PowerShell)

```powershell
# 1. Clone / open the project folder
cd d:\Peertopeer

# 2. Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the development server
python app.py

# 5. Open in browser
#    http://127.0.0.1:5000/
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `FLASK_SECRET_KEY` | **Yes (production)** | Secret key for sessions. App refuses to start in prod with the default. |
| `FLASK_ENV` | No | Set to `production` to enable secure cookie flags. |
| `FLASK_DEBUG` | No | Set to `1` to enable debug mode locally. |

---

## Seeded Demo Users

On first run with an empty database, demo data is inserted automatically.  
All demo users use password: **`password123`**

| Email | Role |
|---|---|
| `jatin@example.com` | Admin |
| `riya@example.com` | User |
| `arjun@example.com` | User |

---

## Route Reference

| Route | Description |
|---|---|
| `GET /` | Homepage |
| `GET /become-mentor` | Become a Mentor landing page |
| `GET /learn` | Learn New Skill (browse & book) |
| `GET /skills` | Legacy full skill search |
| `GET /matches` | Recommended mentor matches (login required) |
| `GET/POST /register` | Registration |
| `GET/POST /login` | Login |
| `GET /logout` | Logout |
| `GET /profile/<id>` | View user profile |
| `GET /profile/edit` | Edit own profile (login required) |
| `POST /profile/basic` | Update name/bio |
| `POST /profile/dashboard/state` | Save dashboard JSON state |
| `POST /profile/picture` | Upload profile picture |
| `POST /profile/picture/remove` | Remove profile picture |
| `GET /media/profile/<filename>` | Serve profile picture (login required) |
| `POST /skills/add` | Add a skill |
| `POST /skills/<id>/delete` | Delete a skill |
| `POST /sessions/schedule` | Book a session |
| `GET /sessions/<id>` | Session detail + messages |
| `POST /sessions/<id>/status` | Update session status |
| `POST /sessions/<id>/review` | Submit a review |
| `POST /sessions/<id>/messages` | Send a session message |
| `GET /discuss` | Discussion board |
| `GET/POST /discuss/new` | Create a post |
| `GET /discuss/<id>` | View a post + replies |
| `POST /discuss/<id>/reply` | Add a reply |
| `POST /discuss/report` | Report a post/reply |
| `GET /admin/reports` | Admin moderation queue |
| `POST /admin/reports/<id>/action` | Moderate a report |


## Notes
- Database tables are created automatically at startup.
- Lightweight schema updates run automatically for existing DB files.
- Keep project-owned media files in `static/media/images` or `static/media/videos`, not in the project root.
- Uploaded profile images are stored in `uploads/profile_pics/` and served through `/media/profile/<filename>` for authenticated users.
