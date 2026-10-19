# Architecture

work-insights is a single-process Python web application for one user. It
reads a tree of markdown notes, keeps its own SQLite database, and talks to
one external service: an OpenAI-compatible LLM endpoint.

## Layers

```
insights/
  config.py      pydantic config, loaded from insights.json
  db/            SQLAlchemy models, session factory, Alembic migrations
  core/          service functions: (Session, args) -> ORM objects
  jobs/          registry, per-run log, live broadcast, scheduler, built-ins
  server/        FastAPI app: /api (JSON) and GUI pages (htmx fragments)
  cli/           click commands; thin client over /api
  integrations/  notes reader (read-only); reserved Discord/Google
```

Rules that keep it simple:

- `core` never imports FastAPI. Everything a page or an endpoint does is a
  call into `core` followed by a commit.
- `server/api.py` and `server/gui.py` are both thin. The GUI renders
  templates; the API renders pydantic schemas. They share no code beyond
  `core`.
- The CLI only speaks HTTP to `/api` (except `insights db upgrade`, which
  runs Alembic directly).
- Every mutation to a thread or mention goes through `core.changes.record`
  so it can be undone.

## Intake, the central flow

One LLM pass per note: given the note text, the list of open threads (title,
kind, labels, Jira keys, owner) and the people, the model returns the
mentions it found and, for each, either an existing thread id, a new
provisional thread, or "set aside". The result is applied immediately. The
Review page shows what the run did; each decision is one click to confirm,
move, or set aside, and the whole run can be reverted.

Deterministic signals are applied before and after the model: Jira keys in a
mention go straight to a thread that already links that key; label hints
apply labels from the note path and Jira project.

## Jobs

Jobs are plain functions registered by name. Each run writes a `JobRun`
row and streams `JobLogEntry` rows; the GUI tails them over
server-sent events through an in-process hub. The scheduler is APScheduler
with cron strings from the config. The server is one process; the hub is
not shared across workers.

## Why SQLite

One user, one laptop, no concurrency beyond a background job thread.
Postgres is on the roadmap for a server deployment.
