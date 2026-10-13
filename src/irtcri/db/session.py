"""Engine / session management."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from irtcri.core.config import get_settings
from irtcri.db.base import Base

_engines: dict[str, Engine] = {}


def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url not in _engines:
        kwargs = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":///" in url and not url.endswith(":memory:"):
                Path(url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
            if url.endswith(":memory:") or url == "sqlite://":
                from sqlalchemy.pool import StaticPool
                kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(engine, "connect")
            def _fk_on(dbapi_conn, _):  # enforce FKs on SQLite like PostgreSQL does
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        _engines[url] = engine
    return _engines[url]


def session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


def create_all(url: str | None = None) -> None:
    """Create tables directly (dev/test). Production uses Alembic migrations."""
    from irtcri.db import models  # noqa: F401  (register models)
    Base.metadata.create_all(get_engine(url))


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    s = session_factory(url)()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
