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
- [How-to](docs/how-to/) — run intake, develop
- [Reference](docs/reference/) — concepts, configuration
- [Explanation](docs/explanation/) — architecture, requirements, roadmap

## Status

Phase 3 of 5: intake reads notes, extracts mentions and places them on
threads; the Review page is where you confirm, move or undo. Threads,
Today, Person and Label pages come next
([roadmap](docs/explanation/roadmap.md)).

## Development

```sh
pytest && mypy insights && ruff check . && ruff format --check .
```

All configuration is in `pyproject.toml`. See [AGENTS.md](AGENTS.md).
