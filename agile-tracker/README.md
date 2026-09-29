# Agile Tracker

A small full-stack tool for a team of 3–10 people to plan work as **Project → User Story → Task**, with a Kanban-style board, assignments, due dates and a background notification worker.

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2 · SQLite (WAL) · vanilla JS frontend (no build step) · pytest

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- App: http://localhost:8000
- Interactive API docs (Swagger): http://localhost:8000/docs
- Tests: `pytest -q`
- Config: copy `.env.example`; every variable has a sensible default.

Open the app, create two accounts (e.g. in two browsers), create a project, add a story, add tasks, and assign a task to the other user. Their bell shows a notification a moment later, delivered by the background worker.

## Features

- Create / view / update / delete projects, stories and tasks; the full tree comes back from one call (`GET /api/projects/{id}`).
- Board grouped by story status (Backlog, To do, In progress, Done), with priority, story points, assignee, task due dates, and overdue highlighting.
- **Roll-up rule:** when every task in a story is done, the story becomes done; starting a task moves the story to In progress; adding a task to a done story re-opens it.
- Auth (register/login), notifications, and a job-status endpoint.

## Architecture

```
Browser (static/ HTML+JS) ──HTTP/JSON──► FastAPI (app/main.py)
                                            │  validation: app/schemas.py (Pydantic)
                                            │  auth:       app/security.py
                                            ▼
                                        SQLite  ◄────── Worker thread (app/worker.py)
                                   (users, projects, stories,     polls `jobs` table
                                    tasks, notifications, jobs)
```

| File | Responsibility |
|---|---|
| `app/main.py` | Routes, business rules (roll-up, assignment notifications), app lifecycle |
| `app/models.py` | ORM models, constraints and indexes |
| `app/schemas.py` | Request/response contracts and input validation |
| `app/security.py` | Password hashing, tokens, auth dependency, login throttle |
| `app/worker.py` | Job queue, handlers, retry/backoff logic |
| `static/` | Single-page UI |

## Database schema

```
users(id PK, name, email UNIQUE, password_hash, created_at)
auth_tokens(token_hash PK, user_id FK→users CASCADE, expires_at)
projects(id PK, name, description, owner_id FK→users, created_at)
stories(id PK, project_id FK→projects CASCADE, title, description,
        status CHECK in (backlog,todo,in_progress,done), priority CHECK in (low,medium,high),
        points, position, assignee_id FK→users SET NULL, created_at)
        INDEX(project_id, status)
tasks(id PK, story_id FK→stories CASCADE, title, status CHECK in (todo,in_progress,done),
      estimate_hours, due_date, assignee_id FK→users SET NULL, created_at)
      INDEX(story_id), INDEX(due_date, status)
notifications(id PK, user_id FK→users CASCADE, message, dedupe_key UNIQUE NULL, read, created_at)
jobs(id PK, type, payload JSON, status CHECK-by-code in (pending,running,done,dead),
     attempts, max_attempts, run_at, last_error, created_at, finished_at)
     INDEX(status, run_at)
```

`Project 1─* Story 1─* Task`; deleting a parent removes its children (FK `ON DELETE CASCADE` plus ORM cascade; `PRAGMA foreign_keys=ON` is set on every connection because SQLite ignores FKs by default). Deleting a user only un-assigns their work (`SET NULL`).

## API

Full reference in [`docs/API.md`](docs/API.md); Swagger is generated live at `/docs`.

## Async / background workflow

**What it does**
1. **Assignment notifications:** assigning a story/task enqueues a `notify` job. The worker writes an in-app notification for the assignee (the delivery function is where email/Slack would plug in).
2. **Overdue reminders:** a recurring `overdue_scan` job (hourly, or on demand via `POST /api/jobs/overdue-scan`) finds open, assigned tasks past their due date and enqueues one `notify` per task per day.

**Design:** a `jobs` table acts as a queue and a worker thread starts with the app. Jobs are inserted in the *same transaction* as the change that triggered them, so a crash cannot produce "task assigned but no notification" (transactional outbox pattern). A job is claimed with an atomic `UPDATE … WHERE status='pending'`, so two workers can never run the same job.

**Failures and retries**
- A failing handler is caught, `last_error` is stored, and the job returns to `pending` with **exponential backoff** (`5s · 2^(attempt-1)` → 5, 10, 20, 40 s).
- After `max_attempts` (default 5) the job is marked **`dead`** (dead-letter) and logged; inspect with `GET /api/jobs?status=dead`.
- Handlers are **idempotent**: notifications carry a unique `dedupe_key`, so a retry after a partial failure, or running the overdue scan twice in a day, never creates duplicates.
- If the process crashes mid-job, `recover_stuck()` re-queues `running` jobs on startup (at-least-once delivery, which idempotency makes safe).
- Covered by `tests/test_api.py` (retry-then-success, dead-letter, dedupe).

## Design decisions and tradeoffs

| Decision | Why | Tradeoff |
|---|---|---|
| FastAPI + Pydantic | Typed validation and auto-generated OpenAPI docs | Python-only ecosystem |
| SQLite (WAL) | Zero setup, right size for 3–10 users | Single-writer; move to Postgres for multi-node |
| DB-backed job queue | No extra infra; transactional enqueue | Polling (1s latency); Celery/RQ + Redis is better at scale |
| Vanilla JS frontend | No build tooling, easy for reviewers to run | Manual DOM rendering; React/Vue would scale better as UI grows |
| Status roll-up computed on task change | Board stays truthful automatically | Manual story status can be overridden by the next task update |
| Flat team model (all users see all projects) | Matches "small team" scope | No per-project membership or roles yet |
| `create_all` instead of migrations | Simple for the assignment | Schema changes need Alembic in production |

## Security considerations

- **Passwords:** PBKDF2-HMAC-SHA256, 200k iterations, per-user salt, constant-time comparison.
- **Sessions:** random 256-bit bearer tokens; only the SHA-256 hash is stored, tokens expire (12 h default).
- **Input validation:** strict Pydantic schemas (lengths, enums, ranges); DB `CHECK` constraints as a second line of defence.
- **SQL injection:** all queries go through the SQLAlchemy ORM (parameterised).
- **XSS:** all server-provided text is HTML-escaped before rendering; a CSP header restricts sources.
- **Other headers:** `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`.
- **Abuse:** in-memory rate limit on login/register (10/min/IP); generic "invalid email or password" message.
- **Authorization:** every endpoint except register/login requires a valid token; only a project's owner can delete it; assignee IDs are validated.
- **Known gaps:** token is kept in `localStorage` (XSS-exposed; an `HttpOnly` cookie + CSRF token is better), no HTTPS termination (use a reverse proxy), no per-project roles, no audit log, in-memory rate limiter is per-process.

## AI usage

> **Edit this section so it accurately reflects how you worked before you submit.**

I used an AI assistant (Claude) to scaffold the project and draft code, tests and documentation. I reviewed the code, ran the test suite and the app locally, and I understand the data model, the retry logic and the tradeoffs described above. Examples of decisions I made or verified myself: <add 2–3 concrete ones, e.g. why a DB-backed queue instead of Celery, the roll-up rule, the idempotency approach>.

## What I would build next

1. Per-project membership and roles (owner / member / viewer) with authorization checks on every route.
2. Alembic migrations and Postgres support; run the worker as a separate process.
3. Real delivery channels (email/Slack) behind the `notify` handler, plus per-user notification preferences.
4. Drag-and-drop board with persisted ordering (`position` is already in the schema), sprints and burndown.
5. Comments and activity history on stories/tasks; search and filters; pagination on list endpoints.
6. `HttpOnly` cookie sessions with CSRF protection, persistent rate limiting, and a Dockerfile + CI (lint, tests).
7. Frontend tests and a component framework once the UI grows.
