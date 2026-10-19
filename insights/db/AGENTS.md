# insights/db

- `models.py`: SQLAlchemy 2.0 declarative models. Enums are stored as short
  strings (`native_enum=False`) so SQLite and Postgres behave the same.
  Timestamps are naive UTC (`utcnow()`).
- `session.py`: `make_engine` (turns on SQLite foreign keys),
  `make_session_factory` (`expire_on_commit=False`).
- `alembic/`: migrations. Config is `[tool.alembic]` in `pyproject.toml`;
  `env.py` reads the DB URL from the insights config. Batch mode is on so
  ALTERs work on SQLite.

After editing `models.py`:

```sh
alembic revision --autogenerate -m "what changed"
alembic upgrade head && alembic check
```

Review the generated file: autogenerate misses renames and server
defaults. Keep `versions/` excluded from ruff and coverage (already set).
Never edit a migration that has shipped; add a new one.
