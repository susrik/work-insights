"""Shared fixtures. No network: the LLM is ``FakeLLM``; the DB is a file-backed
SQLite in ``tmp_path`` (jobs run in daemon threads, so in-memory would not
be shared)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from insights.config import Config
from insights.core.llm import FakeLLM
from insights.db.models import Base
from insights.db.session import SessionFactory, make_session_factory
from insights.server.app import create_app


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "storage": {"data_dir": str(tmp_path)},
            "database": {"url": f"sqlite:///{tmp_path / 'test.db'}"},
            "llm": {"api_key": "test-key", "model": "cheap", "smart_model": "smart"},
            "notes": {
                "enabled": True,
                "root": str(tmp_path / "notes"),
                "people_dir": "team/people",
                "exclude": ["team/people/former", "overviews"],
                "repo_url_template": "https://example.org/notes/blob/main/{path}",
            },
            "jira": {"base_url": "https://jira.example.org/"},
        }
    )


@pytest.fixture
def engine(config: Config) -> Iterator[Engine]:
    eng = create_engine(
        config.database.url,
        poolclass=NullPool,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(eng, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> SessionFactory:
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory: SessionFactory) -> Iterator[Session]:
    with session_factory() as s:
        yield s


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM(responses=["fake response"])


@pytest.fixture
def client(
    config: Config, session_factory: SessionFactory, fake_llm: FakeLLM
) -> Iterator[TestClient]:
    app = create_app(config, llm=fake_llm)
    app.state.session_factory = session_factory
    with TestClient(app) as c:
        yield c
