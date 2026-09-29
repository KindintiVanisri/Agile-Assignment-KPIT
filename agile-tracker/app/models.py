from datetime import datetime, date, timezone
from typing import Optional
from sqlalchemy import ForeignKey, String, Text, Integer, Date, DateTime, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256; raw token never stored
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stories: Mapped[list["Story"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Story.position, Story.id")


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (
        CheckConstraint("status IN ('backlog','todo','in_progress','done')", name="ck_story_status"),
        CheckConstraint("priority IN ('low','medium','high')", name="ck_story_priority"),
        Index("ix_story_project_status", "project_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="backlog")
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    project: Mapped[Project] = relationship(back_populates="stories")
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="story", cascade="all, delete-orphan", order_by="Task.id")

    @property
    def progress(self) -> dict:
        done = sum(t.status == "done" for t in self.tasks)
        return {"done": done, "total": len(self.tasks)}


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('todo','in_progress','done')", name="ck_task_status"),
        Index("ix_task_due", "due_date", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="todo")
    estimate_hours: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    story: Mapped[Story] = relationship(back_populates="tasks")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(String(300))
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)  # makes retries idempotent
    read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_job_claim", "status", "run_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|running|done|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
