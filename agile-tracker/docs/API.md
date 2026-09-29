# API Reference

Base URL `http://localhost:8000/api`. JSON in/out. Everything except `/auth/*` needs `Authorization: Bearer <token>`.
Errors: `{"detail": "..."}` with `401` (auth), `403` (forbidden), `404` (missing), `409` (conflict), `422` (validation), `429` (rate limit).
Live, try-it-out docs: `/docs` (Swagger) and `/redoc`.

## Auth
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/auth/register` | `{name, email, password(≥8)}` | 201 → `{access_token, token_type, user}`; 409 if email exists |
| POST | `/auth/login` | `{email, password}` | 200 → same shape; 401 on bad credentials |
| GET | `/me` | – | Current user |
| GET | `/users` | – | All users (for assignee pickers) |

## Projects
| Method | Path | Notes |
|---|---|---|
| GET | `/projects` | List |
| POST | `/projects` | `{name, description?}` → 201 |
| GET | `/projects/{id}` | **Full tree**: project → stories → tasks, with story `progress {done,total}` |
| PATCH | `/projects/{id}` | Partial update |
| DELETE | `/projects/{id}` | 204; owner only (403 otherwise); cascades to stories and tasks |

## Stories
| Method | Path | Body |
|---|---|---|
| POST | `/projects/{pid}/stories` | `{title, description?, status?(backlog\|todo\|in_progress\|done), priority?(low\|medium\|high), points?(0–100), assignee_id?}` → 201 |
| PATCH | `/stories/{id}` | any of the above plus `position` |
| DELETE | `/stories/{id}` | 204; cascades to tasks |

## Tasks
| Method | Path | Body |
|---|---|---|
| POST | `/stories/{sid}/tasks` | `{title, status?(todo\|in_progress\|done), estimate_hours?, due_date?(YYYY-MM-DD), assignee_id?}` → 201 |
| PATCH | `/tasks/{id}` | any of the above; updates roll up into story status |
| DELETE | `/tasks/{id}` | 204 |

Assigning to someone else (create or change) enqueues a notification job.

## Notifications and jobs
| Method | Path | Notes |
|---|---|---|
| GET | `/notifications` | Latest 50 for the current user |
| POST | `/notifications/read` | Mark all read (204) |
| GET | `/jobs?status=pending\|running\|done\|dead` | Queue inspection: attempts, `last_error`, `run_at` |
| POST | `/jobs/overdue-scan` | 202; queue an overdue-task scan now |

## Example
```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/register -H 'Content-Type: application/json' \
  -d '{"name":"Asha","email":"asha@example.com","password":"password123"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -X POST localhost:8000/api/projects -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"Website revamp"}'
```
