"""Database engine + session setup.

Local dev defaults to a file-based SQLite db (zero setup — just run.ps1).
On the droplet set DATABASE_URL to the managed Postgres, e.g.
    postgresql+psycopg2://user:pass@host:5432/threepl
The ORM models are portable; db/01_schema.sql is the canonical Postgres DDL.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}")

# check_same_thread only matters for SQLite; harmless to branch on the scheme.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
