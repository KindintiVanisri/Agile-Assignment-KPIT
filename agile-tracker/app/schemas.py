from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

StoryStatus = Literal["backlog", "todo", "in_progress", "done"]
TaskStatus = Literal["todo", "in_progress", "done"]
Priority = Literal["low", "medium", "high"]
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(pattern=EMAIL, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(ORM):
    id: int
    name: str
    email: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=5000)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    status: TaskStatus = "todo"
    estimate_hours: Optional[int] = Field(default=None, ge=0, le=1000)
    due_date: Optional[date] = None
    assignee_id: Optional[int] = None


class TaskPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[TaskStatus] = None
    estimate_hours: Optional[int] = Field(default=None, ge=0, le=1000)
    due_date: Optional[date] = None
    assignee_id: Optional[int] = None


class TaskOut(ORM):
    id: int
    story_id: int
    title: str
    status: TaskStatus
    estimate_hours: Optional[int]
    due_date: Optional[date]
    assignee_id: Optional[int]


class StoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    status: StoryStatus = "backlog"
    priority: Priority = "medium"
    points: Optional[int] = Field(default=None, ge=0, le=100)
    assignee_id: Optional[int] = None


class StoryPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    status: Optional[StoryStatus] = None
    priority: Optional[Priority] = None
    points: Optional[int] = Field(default=None, ge=0, le=100)
    position: Optional[int] = Field(default=None, ge=0)
    assignee_id: Optional[int] = None


class Progress(BaseModel):
    done: int
    total: int


class StoryOut(ORM):
    id: int
    project_id: int
    title: str
    description: str
    status: StoryStatus
    priority: Priority
    points: Optional[int]
    position: int
    assignee_id: Optional[int]
    progress: Progress
    tasks: list[TaskOut] = []


class ProjectOut(ORM):
    id: int
    name: str
    description: str
    owner_id: int
    created_at: datetime


class ProjectDetail(ProjectOut):
    stories: list[StoryOut]


class NotificationOut(ORM):
    id: int
    message: str
    read: bool
    created_at: datetime


class JobOut(ORM):
    id: int
    type: str
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error: Optional[str]
    finished_at: Optional[datetime]
