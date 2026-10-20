# insights/server

- `app.py`: app factory `create_app(config, llm)`; lifespan attaches the
  broadcast hub, marks interrupted runs, starts the scheduler.
- `deps.py`: FastAPI dependencies reading `app.state`.
- `api.py`: JSON `/api`. Every endpoint: call `core`, map `ValueError` to
  400/404, `session.commit()`, return a schema.
- `schemas.py`: pydantic in/out models. `XOut.of(orm_obj)` builds output.
- `gui.py`: `render()`, the Review and Jobs pages, SSE log tail.
- `gui_pages.py`: Today, Threads (`_thread_list.html` + `_thread_detail.html`
  panes), People/Person, Labels/Label. Plain forms that create redirect
  (303); htmx forms return the fragment they belong to. `_guard` maps
  `ValueError` to 404 ("not found") or 400.
- Threads page: `_thread_filters` parses the filter form leniently (selects
  submit `""`; bad values become a warning, never an error). Every thread
  mutation answers with `HX-Trigger: threads-changed`, which makes
  `#thread-list` re-fetch `/gui/threads/list` with the current filters.
  The list renders every column; the column choice/order, the selected-row
  highlight and the splitter width are handled by the script in
  `threads.html` and kept in `localStorage`.
- Templates: pages (`*.html`) extend `_layout.html`; fragments are
  `_*.html`; shared badges/links/lists are macros in `_macros.html`;
  `_summary.html` is shared by the Person and Label pages.
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
