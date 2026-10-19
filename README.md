# work-insights

A work planner for a team lead. It reads your markdown meeting notes,
extracts the things that need doing, places them on **threads** of work,
agreements and goals, and helps you review and reorganise with as few
clicks as possible. AI does the first pass; you confirm, move, or undo.

Single user, single process, SQLite. It talks to nobody except the LLM
endpoint you configure.

## Quick start

```sh
pip install -e ".[dev]" uvicorn
cp insights.example.json insights.json   # edit: llm, notes.root
insights db upgrade
python -m insights.server                # http://127.0.0.1:9900
```

Full walk-through: [docs/tutorials/getting-started.md](docs/tutorials/getting-started.md).

## Documentation

- [Tutorials](docs/tutorials/) — getting started
- [How-to](docs/how-to/) — run intake, prepare a meeting, review proposals, develop
- [Reference](docs/reference/) — concepts, configuration
- [Explanation](docs/explanation/) — architecture, requirements, roadmap

## Status

v1 complete: intake, Review, Threads (tree, edit, reparent, merge, links,
undo), Today (focus ranking, overdue / blocked / quiet), Person and Label
pages for meeting prep, and `insights_scan`, which proposes split / merge /
reparent / done / stale changes you apply or dismiss on the thread. What
comes next is in the [roadmap](docs/explanation/roadmap.md).

## Development

```sh
pytest && mypy insights && ruff check . && ruff format --check .
```

All configuration is in `pyproject.toml`. See [AGENTS.md](AGENTS.md).
