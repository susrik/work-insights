from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from insights.config import Config
from insights.db.models import Base, Mention
from insights.db.session import make_engine, make_session_factory


def test_sqlite_engine_enforces_foreign_keys(tmp_path: Path) -> None:
    config = Config.model_validate({"database": {"url": f"sqlite:///{tmp_path / 'fk.db'}"}})
    engine = make_engine(config)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Mention(fingerprint="x", path="a.md", summary="s", thread_id=999))
        with pytest.raises(IntegrityError):
            session.flush()
    engine.dispose()
