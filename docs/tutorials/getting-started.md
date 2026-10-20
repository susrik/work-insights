# Getting started

By the end of this tutorial you will have the server running, a person and
a label created, and your first thread. It takes about ten minutes.

## 1. Install

```sh
python3 -m venv ~/.virtualenvs/work-insights
~/.virtualenvs/work-insights/bin/pip install -e ".[dev]" uvicorn
```

Python 3.12 or newer is required.

## 2. Configure

Copy the example config and edit it:

```sh
cp insights.example.json insights.json
```

`insights.json` is git-ignored and is where your private values live. For
this tutorial set at least:

- `llm.base_url`, `llm.api_key`, `llm.model`: any OpenAI-compatible endpoint.
  Leave `api_key` empty to run without AI for now.
- `notes.root`: the folder with your markdown notes, and `notes.enabled: true`.

## 3. Create the database

```sh
insights db upgrade
```

This applies the Alembic migrations to the SQLite file named in
`database.url` (default `./data/insights.db`).

## 4. Start the server

```sh
python -m insights.server
```

Open <http://127.0.0.1:9900>. The Today page shows a warning for anything
still unconfigured.

## 5. Add yourself, a colleague and a label

In a second terminal:

```sh
insights person add "Me" --self
insights person add "Robert" --notes-dir team/people/robert
insights label add dashboard --path team/dashboard --jira DBOARD3
```

The `--notes-dir` and `--path` values are folders under `notes.root`. They
let intake attribute mentions to Robert and apply the `dashboard` label
without being told.

## 6. Create a thread

```sh
insights thread add "Dashboard alarm cleanup" --label dashboard --jira DBOARD3-12
insights thread list
```

You now have a thread with a label and a Jira link. Every change from here
is recorded; try:

```sh
insights thread set 1 --status done
insights changes list
insights changes undo <id>
```

## 7. Look around

- **Threads** shows the tree; click a thread to edit it, move it under a
  parent, merge another into it, or undo a change from its history. Filter
  by status, confirmed/provisional, label, owner, title or Jira key; pick
  and order the list columns with **Columns**; drag the divider to resize
  the list. The list updates as soon as you save or delete a thread.
- **Today** ranks open threads and lists what needs a decision.
- **People** and **Labels** open the meeting-prep views.

## Next

- [Run intake over your notes](../how-to/run-intake.md) once an LLM is
  configured.
- [Prepare a meeting](../how-to/prepare-a-meeting.md).
- [Review structure proposals](../how-to/review-proposals.md) once a few
  threads exist.
- Read [concepts](../reference/concepts.md) for the vocabulary.
