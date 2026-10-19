# AGENTS.md

work-insights is a single-user work planner: it reads markdown meeting
notes, extracts **mentions**, places them on **threads** (work, agreements,
goals), and helps the user review and reorganise. Python 3.12+, FastAPI,
SQLAlchemy 2.0 (sync), Alembic, htmx + Tailwind + daisyUI, click. SQLite.

Read [docs/reference/concepts.md](docs/reference/concepts.md) first for
the vocabulary; do not reintroduce "task" or "nugget".

## Rules

- All tool config lives in `pyproject.toml`. No `mypy.ini`, `ruff.toml`,
  `alembic.ini`, `pytest.ini`.
- Before finishing any change: `pytest` (95% coverage gate), `mypy insights`
  (strict), `ruff check .`, `ruff format .` must all pass.
- This repo is public. Never commit `insights.json`, real names, note text
  or credentials. Tests use invented names.
- The notes tree is read-only. The app makes no network calls except to the
  configured LLM.
- Every thread/mention mutation is recorded via `core.changes.record` so it
  can be undone. AI jobs pass `actor=Actor.AI, job_run_id=...`.
- Schema changes need an Alembic migration (`alembic revision --autogenerate`).
- Keep AGENTS.md files short and human-readable. Update the one nearest the
  code you change.

## Where things are

| Path | Purpose | Guide |
|------|---------|-------|
| `insights/core/` | Service layer, no web imports | [AGENTS.md](insights/core/AGENTS.md) |
| `insights/db/` | Models, session, migrations | [AGENTS.md](insights/db/AGENTS.md) |
| `insights/jobs/` | Job registry, logging, scheduler | [AGENTS.md](insights/jobs/AGENTS.md) |
| `insights/server/` | FastAPI `/api` and GUI | [AGENTS.md](insights/server/AGENTS.md) |
| `insights/cli/` | click CLI over `/api` | [AGENTS.md](insights/cli/AGENTS.md) |
| `tests/` | pytest suite | [AGENTS.md](tests/AGENTS.md) |
| `docs/` | Diátaxis documentation | [AGENTS.md](docs/AGENTS.md) |

Design background: [docs/explanation/architecture.md](docs/explanation/architecture.md),
[requirements.md](docs/explanation/requirements.md),
[roadmap.md](docs/explanation/roadmap.md).
