# insights/server

- `app.py`: app factory `create_app(config, llm)`; lifespan attaches the
  broadcast hub, marks interrupted runs, starts the scheduler.
- `deps.py`: FastAPI dependencies reading `app.state`.
- `api.py`: JSON `/api`. Every endpoint: call `core`, map `ValueError` to
  400/404, `session.commit()`, return a schema.
- `schemas.py`: pydantic in/out models. `XOut.of(orm_obj)` builds output.
- `gui.py`: pages (`*.html`, extend `_layout.html`) and htmx fragments
  (`_*.html`). A mutation returns the fragment it belongs to; the Review
  actions all re-render `_review_groups.html`, keeping `?run=` if set.
- Templates get `config` for free (`render()` adds it) to build note and
  Jira links via `config.notes.note_url` / `config.jira.issue_url`.
- `templates/`, `static/theme.css`: htmx 2, Tailwind 4 (browser build),
  daisyUI 5 from CDN. Theme tokens are plain hex in `theme.css`
  (Paul Tol colour-blind-safe palette).

Rules:

- No business logic in endpoints or templates; it goes in `core`.
- Provisional items get the `.provisional` class (dashed accent edge).
- The SSE endpoint replays persisted log entries, then tails the hub.
  Keep `data-job-log-stream` on any element that should stream.
- Add a nav entry in `_layout.html` when you add a page, and set `page=`
  in `render()` so it highlights.
