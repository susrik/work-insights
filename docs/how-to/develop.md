# Develop

## Checks

All tool configuration is in `pyproject.toml`. The three commands that must
be green before a commit:

```sh
pytest           # coverage gate: 95%
mypy insights    # strict
ruff check . && ruff format --check .
```

## Database migrations

Models are in `insights/db/models.py`. After changing them:

```sh
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
alembic check        # confirms models and migrations agree
```

Alembic is configured in `[tool.alembic]`; the database URL comes from the
insights config, not from an ini file. Migration files use batch mode so
they run on SQLite.

## Tests

- `tests/conftest.py` provides `config`, `session`, `fake_llm` and `client`
  fixtures. The database is a file in `tmp_path`; the LLM is `FakeLLM` with
  scripted responses.
- No test touches the network or the real notes tree.
- Test data must not contain real names, note text or secrets.

## Adding a job

Register a function in `insights/jobs/builtin.py`:

```python
@registry.register("my_job")
def my_job(ctx: JobContext) -> str:
    log = ctx.log()
    log.step("starting")
    ...
    return "one-line result"
```

Use `ctx.session`, `ctx.config`, `ctx.llm`, `ctx.flag("full")`. Log LLM
calls with `log.llm_call` / `log.llm_reply` so the GUI shows them. Pass
`actor=Actor.AI, job_run_id=ctx.job_run.id` into `core` functions so the
run can be reverted.
