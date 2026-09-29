import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
if URL.startswith("sqlite:///"):
    os.makedirs(os.path.dirname(URL.replace("sqlite:///", "")) or ".", exist_ok=True)

engine = create_engine(URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _pragmas(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")   # SQLite ignores FKs unless enabled
    cur.execute("PRAGMA journal_mode=WAL")  # API + worker thread can read/write concurrently
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
