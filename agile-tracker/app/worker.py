"""Database-backed background job queue with retries, exponential backoff and a dead-letter state.

Why a DB queue: SQLite is the stated store, the team is small, and jobs are enqueued in the same
transaction as the change that caused them (no lost notifications if the process crashes).
"""
import json, logging, os, threading, time
from datetime import date, timedelta
from sqlalchemy import select, update
from .db import SessionLocal
from .models import Job, Notification, Task, utcnow

log = logging.getLogger("worker")
BACKOFF = int(os.getenv("JOB_BACKOFF_BASE_SEC", "5"))
MAX_ATTEMPTS = int(os.getenv("JOB_MAX_ATTEMPTS", "5"))
SCAN_EVERY = int(os.getenv("OVERDUE_SCAN_INTERVAL_SEC", "3600"))


def enqueue(db, type_: str, payload: dict, delay: int = 0, max_attempts: int = MAX_ATTEMPTS) -> Job:
    """Adds to the caller's session; the caller commits so the job shares the business transaction."""
    job = Job(type=type_, payload=json.dumps(payload), max_attempts=max_attempts,
              run_at=utcnow() + timedelta(seconds=delay))
    db.add(job)
    return job


# ---- handlers (must be idempotent: a job may run more than once) ----
def h_notify(db, job, p):
    if job.attempts <= p.get("fail_times", 0):          # test/demo hook to exercise retries
        raise RuntimeError("simulated delivery failure")
    key = p.get("dedupe_key")
    if key and db.scalar(select(Notification.id).where(Notification.dedupe_key == key)):
        return                                           # already delivered on an earlier attempt
    db.add(Notification(user_id=p["user_id"], message=p["message"], dedupe_key=key))
    log.info("notify user=%s msg=%s", p["user_id"], p["message"])  # swap for SMTP/Slack in prod


def h_overdue_scan(db, job, p):
    today = date.today()
    rows = db.scalars(select(Task).where(Task.due_date < today, Task.status != "done",
                                         Task.assignee_id.is_not(None))).all()
    for t in rows:
        enqueue(db, "notify", {"user_id": t.assignee_id, "dedupe_key": f"overdue:{t.id}:{today}",
                               "message": f"Task '{t.title}' was due {t.due_date} and is still open"})


HANDLERS = {"notify": h_notify, "overdue_scan": h_overdue_scan}


def _claim(db):
    job = db.scalars(select(Job).where(Job.status == "pending", Job.run_at <= utcnow())
                     .order_by(Job.run_at, Job.id).limit(1)).first()
    if not job:
        return None
    # Atomic compare-and-set so two workers can never run the same job.
    res = db.execute(update(Job).where(Job.id == job.id, Job.status == "pending")
                     .values(status="running", attempts=Job.attempts + 1))
    db.commit()
    if res.rowcount == 0:
        return None
    db.refresh(job)
    return job


def run_once() -> bool:
    """Process a single due job. Returns False when the queue is idle."""
    with SessionLocal() as db:
        job = _claim(db)
        if not job:
            return False
        try:
            HANDLERS[job.type](db, job, json.loads(job.payload))
            job.status, job.finished_at, job.last_error = "done", utcnow(), None
        except Exception as exc:  # noqa: BLE001 - handler failures are recorded, never crash the loop
            db.rollback()
            db.refresh(job)
            job.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            if job.attempts >= job.max_attempts:
                job.status, job.finished_at = "dead", utcnow()      # dead-letter, visible via /api/jobs
                log.error("job %s dead after %s attempts: %s", job.id, job.attempts, job.last_error)
            else:
                job.status = "pending"
                job.run_at = utcnow() + timedelta(seconds=BACKOFF * 2 ** (job.attempts - 1))
        db.commit()
        return True


def recover_stuck():
    """Jobs left 'running' by a crash are re-queued at startup (at-least-once delivery)."""
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.status == "running").values(status="pending"))
        db.commit()


def loop(stop: threading.Event):
    last_scan = float("-inf")
    while not stop.is_set():
        try:
            if time.monotonic() - last_scan > SCAN_EVERY:       # simple scheduler for the recurring job
                last_scan = time.monotonic()
                with SessionLocal() as db:
                    enqueue(db, "overdue_scan", {})
                    db.commit()
            if not run_once():
                stop.wait(1.0)
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            stop.wait(2.0)
