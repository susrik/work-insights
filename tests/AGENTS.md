# tests

pytest with a 95% coverage gate (`pyproject.toml`). Run `pytest`; browser
tests (`-m browser`) are opt-in and excluded by default.

Fixtures in `conftest.py`:

- `config`: a `Config` pointing at `tmp_path` (SQLite file, fake notes root,
  fake Jira/notes URLs).
- `engine` / `session_factory` / `session`: tables created from the models
  (migrations are tested separately in `test_cli.py::test_db_upgrade`).
- `fake_llm`: `FakeLLM` with scripted responses; inspect `.calls`.
- `client`: `TestClient` over `create_app` with the fixtures above.

Rules:

- No network, no real notes, no real names. Invented people (`Robert`,
  `Sam`), invented keys (`AB-1`, `DBOARD3-12`).
- One file per module under test; `test_api.py` covers `/api`,
  `test_gui.py` pages and SSE, `test_cli.py` the CLI end to end.
- `filterwarnings = error`: a new warning fails the suite; fix it rather
  than ignore it.
- Long lines are allowed in tests (`E501` ignored); nothing else is.
