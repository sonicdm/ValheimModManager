from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import get_settings

Base = declarative_base()
_engine = None
SessionLocal = None


def init_db() -> None:
    global _engine, SessionLocal
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    settings.staging_dir.mkdir(parents=True, exist_ok=True)

    db_url = f"sqlite:///{settings.db_path.as_posix()}"
    # timeout: seconds sqlite waits on locks before raising "database is locked"
    _engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )

    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL lets readers proceed while a writer holds a transaction (startup scan
        # + package refresh + health checks overlap otherwise).
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=_engine)


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        init_db()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session() -> Session:
    if SessionLocal is None:
        init_db()
    assert SessionLocal is not None
    return SessionLocal()
