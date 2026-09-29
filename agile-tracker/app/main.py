import os, threading
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from . import schemas as s, worker
from .db import Base, engine, get_db
from .models import Job, Notification, Project, Story, Task, User
from .security import current_user, hash_password, issue_token, throttle_auth, verify_password

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_stop = threading.Event()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    thread = None
    if os.getenv("WORKER_ENABLED", "1") == "1":
        worker.recover_stuck()
        _stop.clear()
        thread = threading.Thread(target=worker.loop, args=(_stop,), daemon=True, name="worker")
        thread.start()
    yield
    _stop.set()
    if thread:
        thread.join(timeout=3)


app = FastAPI(title="Agile Tracker API", version="1.0.0", lifespan=lifespan,
              description="Project → User Story → Task tracking for small teams.")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
    return resp


api = APIRouter(prefix="/api")


def get_or_404(db: Session, model, id_: int):
    obj = db.get(model, id_)
    if not obj:
        raise HTTPException(404, f"{model.__name__} {id_} not found")
    return obj


def check_user(db: Session, uid: Optional[int]):
    if uid is not None and not db.get(User, uid):
        raise HTTPException(422, f"assignee_id {uid} is not a known user")


def notify_assignment(db, actor: User, assignee_id: Optional[int], what: str, title: str):
    if assignee_id and assignee_id != actor.id:
        worker.enqueue(db, "notify", {"user_id": assignee_id,
                                      "message": f"{actor.name} assigned you {what} '{title}'"})


# ---------- auth ----------
@api.post("/auth/register", response_model=s.TokenOut, status_code=201, dependencies=[Depends(throttle_auth)])
def register(body: s.RegisterIn, db: Session = Depends(get_db)):
    user = User(name=body.name.strip(), email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email already registered")
    return {"access_token": issue_token(db, user), "user": user}


@api.post("/auth/login", response_model=s.TokenOut, dependencies=[Depends(throttle_auth)])
def login(body: s.LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"access_token": issue_token(db, user), "user": user}


@api.get("/me", response_model=s.UserOut)
def me(user: User = Depends(current_user)):
    return user


@api.get("/users", response_model=list[s.UserOut])
def users(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return db.scalars(select(User).order_by(User.name)).all()


# ---------- projects ----------
@api.get("/projects", response_model=list[s.ProjectOut])
def list_projects(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return db.scalars(select(Project).order_by(Project.created_at.desc())).all()


@api.post("/projects", response_model=s.ProjectOut, status_code=201)
def create_project(body: s.ProjectIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = Project(**body.model_dump(), owner_id=user.id)
    db.add(p); db.commit()
    return p


@api.get("/projects/{pid}", response_model=s.ProjectDetail)
def project_tree(pid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    p = db.scalar(select(Project).where(Project.id == pid)
                  .options(selectinload(Project.stories).selectinload(Story.tasks)))  # 3 queries, no N+1
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@api.patch("/projects/{pid}", response_model=s.ProjectOut)
def update_project(pid: int, body: s.ProjectPatch, db: Session = Depends(get_db), _: User = Depends(current_user)):
    p = get_or_404(db, Project, pid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    return p


@api.delete("/projects/{pid}", status_code=204)
def delete_project(pid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_or_404(db, Project, pid)
    if p.owner_id != user.id:
        raise HTTPException(403, "Only the project owner can delete it")
    db.delete(p); db.commit()


# ---------- stories ----------
@api.post("/projects/{pid}/stories", response_model=s.StoryOut, status_code=201)
def create_story(pid: int, body: s.StoryIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    get_or_404(db, Project, pid)
    check_user(db, body.assignee_id)
    count = db.scalar(select(func.count(Story.id)).where(Story.project_id == pid))
    st = Story(**body.model_dump(), project_id=pid, position=count)
    db.add(st)
    notify_assignment(db, user, body.assignee_id, "story", body.title)
    db.commit()
    return st


@api.patch("/stories/{sid}", response_model=s.StoryOut)
def update_story(sid: int, body: s.StoryPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    st = get_or_404(db, Story, sid)
    data = body.model_dump(exclude_unset=True)
    if "assignee_id" in data:
        check_user(db, data["assignee_id"])
        if data["assignee_id"] != st.assignee_id:
            notify_assignment(db, user, data["assignee_id"], "story", st.title)
    for k, v in data.items():
        setattr(st, k, v)
    db.commit()
    return st


@api.delete("/stories/{sid}", status_code=204)
def delete_story(sid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    db.delete(get_or_404(db, Story, sid)); db.commit()


# ---------- tasks ----------
@api.post("/stories/{sid}/tasks", response_model=s.TaskOut, status_code=201)
def create_task(sid: int, body: s.TaskIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    st = get_or_404(db, Story, sid)
    check_user(db, body.assignee_id)
    t = Task(**body.model_dump(), story_id=sid)
    db.add(t)
    notify_assignment(db, user, body.assignee_id, "task", body.title)
    if st.status == "done":
        st.status = "in_progress"        # new work re-opens a finished story
    db.commit()
    return t


@api.patch("/tasks/{tid}", response_model=s.TaskOut)
def update_task(tid: int, body: s.TaskPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = get_or_404(db, Task, tid)
    data = body.model_dump(exclude_unset=True)
    if "assignee_id" in data:
        check_user(db, data["assignee_id"])
        if data["assignee_id"] != t.assignee_id:
            notify_assignment(db, user, data["assignee_id"], "task", t.title)
    for k, v in data.items():
        setattr(t, k, v)
    db.flush()
    # Roll-up rule: story status follows its tasks.
    st = t.story
    if st.tasks and all(x.status == "done" for x in st.tasks):
        st.status = "done"
    elif t.status in ("in_progress", "done") and st.status in ("backlog", "todo", "done"):
        st.status = "in_progress"
    db.commit()
    return t


@api.delete("/tasks/{tid}", status_code=204)
def delete_task(tid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    db.delete(get_or_404(db, Task, tid)); db.commit()


# ---------- notifications & jobs ----------
@api.get("/notifications", response_model=list[s.NotificationOut])
def notifications(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(select(Notification).where(Notification.user_id == user.id)
                      .order_by(Notification.id.desc()).limit(50)).all()


@api.post("/notifications/read", status_code=204)
def mark_read(db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.read.is_(False)).update({"read": True})
    db.commit()


@api.get("/jobs", response_model=list[s.JobOut])
def jobs(status: Optional[str] = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    q = select(Job).order_by(Job.id.desc()).limit(100)
    if status:
        q = q.where(Job.status == status)
    return db.scalars(q).all()


@api.post("/jobs/overdue-scan", status_code=202)
def trigger_scan(db: Session = Depends(get_db), _: User = Depends(current_user)):
    worker.enqueue(db, "overdue_scan", {}); db.commit()
    return {"queued": True}


app.include_router(api)
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))
