# SkillLink

A peer-to-peer learning platform where students teach what they know and learn what they need through scheduled sessions.

## What this project does

SkillLink helps students:
- Add **teach** and **learn** skills
- Find matching mentors
- Schedule video sessions
- Chat inside each session
- Give post-session reviews (learner → mentor)
- Join discussion forums and community activity feeds

It also includes moderation tools and notification workflows for session activity.

---

## Tech stack

- **Backend:** Python, Flask
- **Database:** SQLite + Flask-SQLAlchemy
- **Frontend:** Jinja templates, Vanilla JS, custom CSS
- **Auth:** Session-based login with CSRF protection

---

## Key features

### 1) Authentication & profile
- Register / login / logout
- Profile edit and dashboard state save
- Profile image upload and protected media serving

### 2) Skills & matching
- Add/delete teach or learn skills
- Browse skills with filters
- Auto match learner interests to mentor skills
- Self-listings are excluded from learn/mentor listing views

### 3) Session scheduling lifecycle
- Book a session with date/time, platform, and meeting link
- Session status progression:
  - `scheduled`
  - `ongoing` (once scheduled time starts)
  - `completed` (manual by mentor or auto after configured duration)
  - `cancelled`
- Session edit allowed only while still effectively scheduled

### 4) Session chat
- Per-session conversation thread
- Unread message counts in session actions

### 5) Reviews
- Reviews are allowed only for completed sessions
- **Learner-only review submission**
- 5-star interactive rating UI in profile session table
- Review updates are upserted per session/reviewer

### 6) Notifications
- Navbar bell icon with unread count
- Notification dropdown + mark-all-read action
- API endpoints for polling unread notifications
- Chrome desktop notifications (after permission)
- Meeting-start browser alarm tone for `meeting_start` notification events
- Session scheduling creates mentor notification

### 7) Discussion & moderation
- Public discussion board with topics/tags
- Replies on posts
- Report posts/replies
- Admin moderation queue and moderation actions
- Private discussion rooms with invite code/link

---

## Project structure

```text
app.py
models.py
database.py
query_services.py
extensions.py
requirements.txt
README.md

templates/
  base.html
  index.html
  become_mentor.html
  learn_skill.html
  skills.html
  matches.html
  profile.html
  edit_profile.html
  session_detail.html
  discuss.html
  discuss_new.html
  discuss_post.html
  community.html
  private_discussions.html
  private_discussion_room.html
  admin_reports.html
  login.html
  register.html

static/
  css/style.css
  media/images/
  media/videos/

uploads/
  profile_pics/
```

---

## Local setup (Windows PowerShell)

```powershell
cd d:\Peertopeer

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

python app.py
```

Open:
- `http://127.0.0.1:5000/`

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `FLASK_SECRET_KEY` | Required in production | Session secret key |
| `FLASK_ENV` | Optional | Set `production` for production behavior |
| `FLASK_DEBUG` | Optional | Set `1` for debug mode locally |
| `SESSION_AUTO_COMPLETE_HOURS` | Optional | Auto-complete timeout for sessions (default: `2`, min: `1`) |

---

## Notification behavior notes

- Desktop/browser notifications require user permission in Chrome.
- Current implementation uses polling from the open web app (every ~30 seconds).
- If the app tab is closed, browser popups do not fire in the current non-service-worker setup.

---

## Seed users (first run)

On first run with an empty DB, sample users are created.

Password for all seeded users:
- `password123`

Users:
- `jatin@example.com` (admin)
- `riya@example.com`
- `arjun@example.com`

---

## Core routes (quick reference)

### Auth / profile
- `GET,POST /register`
- `GET,POST /login`
- `GET /logout`
- `GET /profile/<user_id>`
- `GET /profile/edit`
- `POST /profile/basic`
- `POST /profile/dashboard/state`
- `POST /profile/picture`
- `POST /profile/picture/remove`
- `GET /media/profile/<filename>`

### Skills / sessions
- `GET /skills`
- `GET /become-mentor`
- `GET /learn`
- `GET /matches`
- `POST /skills/add`
- `POST /skills/<skill_id>/delete`
- `POST /sessions/schedule`
- `GET /sessions/<session_id>`
- `POST /sessions/<session_id>/edit`
- `POST /sessions/<session_id>/status`
- `POST /sessions/<session_id>/messages`
- `POST /sessions/<session_id>/review`

### Notifications
- `GET /notifications/unread`
- `POST /notifications/mark-read`

### Community / discussion
- `GET /community`
- `GET /discuss`
- `GET,POST /discuss/new`
- `GET /discuss/<post_id>`
- `POST /discuss/<post_id>/reply`
- `POST /discuss/<post_id>/report`
- `POST /discuss/<post_id>/replies/<reply_id>/report`
- `POST /discuss/<post_id>/delete`
- `POST /discuss/<post_id>/replies/<reply_id>/delete`

### Private discussions
- `GET,POST /discuss/private`
- `POST /discuss/private/join`
- `GET /discuss/private/join/<invite_code>`
- `GET /discuss/private/<discussion_id>`
- `POST /discuss/private/<discussion_id>/messages`

### Admin
- `GET /admin/reports`
- `POST /admin/reports/<report_id>/action`

---

## Development notes

- Schema updates are handled automatically at startup via `ensure_schema_updates()`.
- Keep media assets inside `static/media/...`.
- Uploaded profile images are stored under `uploads/profile_pics/`.
- For production, always set a strong `FLASK_SECRET_KEY`.
