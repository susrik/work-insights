from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, make_url
from sqlalchemy.orm import Session, sessionmaker

from insights.config import Config

SessionFactory = sessionmaker[Session]


def ensure_sqlite_dir(url: str) -> None:
    """Create the parent folder of a file-backed SQLite database."""
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite") and parsed.database and parsed.database != ":memory:":
        Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def make_engine(config: Config) -> Engine:
    ensure_sqlite_dir(config.database.url)
    engine = create_engine(config.database.url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn: object, _record: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False)
