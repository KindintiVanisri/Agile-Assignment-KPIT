import os
os.environ.update(DATABASE_URL="sqlite:///./data/test.db", WORKER_ENABLED="0", JOB_BACKOFF_BASE_SEC="0")
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from app import security, worker
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Job, Notification


@pytest.fixture()
def c():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    security._hits.clear()
    return TestClient(app)


def reg(c, name, email):
    r = c.post("/api/auth/register", json={"name": name, "email": email, "password": "password123"})
    assert r.status_code == 201
    return {"Authorization": "Bearer " + r.json()["access_token"]}, r.json()["user"]["id"]


def drain():
    while worker.run_once():
        pass


def test_hierarchy_rollup_and_notification(c):
    h, _ = reg(c, "Asha", "a@x.com")
    _, bob = reg(c, "Bob", "b@x.com")
    pid = c.post("/api/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = c.post(f"/api/projects/{pid}/stories", json={"title": "S"}, headers=h).json()["id"]
    tid = c.post(f"/api/stories/{sid}/tasks", json={"title": "T", "assignee_id": bob}, headers=h).json()["id"]
    c.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers=h)
    story = c.get(f"/api/projects/{pid}", headers=h).json()["stories"][0]
    assert story["status"] == "done" and story["progress"] == {"done": 1, "total": 1}
    drain()
    with SessionLocal() as db:
        assert db.query(Notification).filter_by(user_id=bob).count() == 1


def test_auth_required_and_validation(c):
    assert c.get("/api/projects").status_code == 401
    h, _ = reg(c, "A", "a@x.com")
    assert c.post("/api/projects", json={"name": ""}, headers=h).status_code == 422
    assert c.patch("/api/tasks/999", json={"status": "done"}, headers=h).status_code == 404


def test_only_owner_can_delete_and_cascade(c):
    h, _ = reg(c, "A", "a@x.com")
    h2, _ = reg(c, "B", "b@x.com")
    pid = c.post("/api/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = c.post(f"/api/projects/{pid}/stories", json={"title": "S"}, headers=h).json()["id"]
    c.post(f"/api/stories/{sid}/tasks", json={"title": "T"}, headers=h)
    assert c.delete(f"/api/projects/{pid}", headers=h2).status_code == 403
    assert c.delete(f"/api/projects/{pid}", headers=h).status_code == 204
    assert c.get(f"/api/projects/{pid}", headers=h).status_code == 404


def test_retry_then_success_and_dead_letter(c):
    _, uid = reg(c, "A", "a@x.com")
    with SessionLocal() as db:
        worker.enqueue(db, "notify", {"user_id": uid, "message": "hi", "dedupe_key": "k", "fail_times": 2})
        worker.enqueue(db, "notify", {"user_id": uid, "message": "x", "fail_times": 99}, max_attempts=2)
        db.commit()
    for _ in range(20):
        worker.run_once()
    with SessionLocal() as db:
        a, b = db.query(Job).order_by(Job.id).all()
        assert (a.status, a.attempts) == ("done", 3)     # failed twice, then succeeded
        assert (b.status, b.attempts) == ("dead", 2)     # exhausted retries -> dead letter
        assert db.query(Notification).count() == 1


def test_overdue_scan_is_deduplicated(c):
    h, uid = reg(c, "A", "a@x.com")
    pid = c.post("/api/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = c.post(f"/api/projects/{pid}/stories", json={"title": "S"}, headers=h).json()["id"]
    c.post(f"/api/stories/{sid}/tasks", json={"title": "Late", "assignee_id": uid,
           "due_date": str(date.today() - timedelta(days=2))}, headers=h)
    for _ in range(2):
        c.post("/api/jobs/overdue-scan", headers=h)
        drain()
    with SessionLocal() as db:
        assert db.query(Notification).filter_by(user_id=uid).count() == 1


def test_due_date_set_and_clear(c):
    h, _ = reg(c, "A", "a@x.com")
    pid = c.post("/api/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = c.post(f"/api/projects/{pid}/stories", json={"title": "S"}, headers=h).json()["id"]
    t = c.post(f"/api/stories/{sid}/tasks", json={"title": "T", "due_date": "2026-10-05"}, headers=h).json()
    assert t["due_date"] == "2026-10-05"
    assert c.patch(f"/api/tasks/{t['id']}", json={"due_date": "2026-11-01"}, headers=h).json()["due_date"] == "2026-11-01"
    assert c.patch(f"/api/tasks/{t['id']}", json={"due_date": None}, headers=h).json()["due_date"] is None
