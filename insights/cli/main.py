"""``insights`` CLI: click commands over the HTTP ``/api``.

Every command takes ``--config``; the server address comes from
``api_base_url`` in the config. ``insights db upgrade`` is the one command
that talks to the database directly (it runs Alembic).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click
import httpx

from insights.config import Config, config_option

# Tests replace this with an in-process transport; None means a real socket.
_transport: httpx.BaseTransport | None = None


@contextmanager
def _client(config: Config, timeout: float = 600) -> Iterator[httpx.Client]:
    try:
        with httpx.Client(
            base_url=config.api_base_url, timeout=timeout, transport=_transport
        ) as client:
            yield client
    except httpx.ConnectError as e:
        raise click.ClickException(f"cannot reach the server at {config.api_base_url}: {e}") from e


def _call(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise click.ClickException(f"{response.status_code}: {detail}")
    return response.json() if response.content else None


def _echo(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, default=str))


@click.group()
def cli() -> None:
    """work-insights: plan work from your notes."""


@cli.command()
@config_option
def status(config: Config) -> None:
    """Show server status and counts."""
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/status"))


# --- db ---


@cli.group()
def db() -> None:
    """Database maintenance."""


@db.command("upgrade")
@click.option("--config", "config_path", default=None, help="Config file to read the DB URL from.")
def db_upgrade(config_path: str | None) -> None:
    """Apply pending Alembic migrations."""
    import os

    from alembic import command
    from alembic.config import Config as AlembicConfig

    from insights.config import CONFIG_ENV_VAR
    from insights.db import alembic as alembic_pkg

    if config_path:
        os.environ[CONFIG_ENV_VAR] = config_path
    # Point at the installed package so this works from any directory; the
    # developer `alembic` CLI reads the same location from pyproject.toml.
    alembic_config = AlembicConfig()
    alembic_config.set_main_option("script_location", str(Path(alembic_pkg.__file__).parent))
    command.upgrade(alembic_config, "head")


# --- people ---


@cli.group()
def person() -> None:
    """Manage people."""


@person.command("list")
@config_option
def person_list(config: Config) -> None:
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/people"))


@person.command("add")
@click.argument("name")
@click.option("--self", "is_self", is_flag=True, help="This person is you.")
@click.option("--notes-dir", default=None, help="Their folder in the notes tree.")
@click.option("--alias", "aliases", multiple=True)
@config_option
def person_add(
    name: str, is_self: bool, notes_dir: str | None, aliases: tuple[str, ...], config: Config
) -> None:
    body = {"name": name, "is_self": is_self, "notes_dir": notes_dir, "aliases": list(aliases)}
    with _client(config) as client:
        _echo(_call(client, "POST", "/api/people", json=body))


# --- labels ---


@cli.group()
def label() -> None:
    """Manage labels."""


@label.command("list")
@config_option
def label_list(config: Config) -> None:
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/labels"))


@label.command("add")
@click.argument("name")
@click.option("--description", default=None)
@click.option("--path", "paths", multiple=True, help="Note path prefix hint (repeatable).")
@click.option("--jira", "projects", multiple=True, help="Jira project key hint (repeatable).")
@config_option
def label_add(
    name: str,
    description: str | None,
    paths: tuple[str, ...],
    projects: tuple[str, ...],
    config: Config,
) -> None:
    body = {
        "name": name,
        "description": description,
        "note_path_prefixes": list(paths),
        "jira_projects": list(projects),
    }
    with _client(config) as client:
        _echo(_call(client, "POST", "/api/labels", json=body))


# --- threads ---


@cli.group()
def thread() -> None:
    """Manage threads."""


@thread.command("list")
@click.option("--status", default="open", help="open | all | open|in_progress|blocked|done|dropped")
@click.option("--kind", default=None, help="work | agreement | goal")
@click.option("--owner-id", type=int, default=None)
@click.option("--provisional", is_flag=True, help="Only AI-created threads awaiting review.")
@click.option("-q", "search", default=None, help="Title search.")
@click.option("--jira", default=None, help="Substring of a linked Jira key.")
@config_option
def thread_list(
    status: str,
    kind: str | None,
    owner_id: int | None,
    provisional: bool,
    search: str | None,
    jira: str | None,
    config: Config,
) -> None:
    params: dict[str, Any] = {"status": status}
    if kind:
        params["kind"] = kind
    if owner_id is not None:
        params["owner_id"] = owner_id
    if provisional:
        params["provisional"] = "true"
    if search:
        params["q"] = search
    if jira:
        params["jira"] = jira
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/threads", params=params))


@thread.command("add")
@click.argument("title")
@click.option("--kind", default="work", help="work | agreement | goal")
@click.option("--owner-id", type=int, default=None)
@click.option("--parent-id", type=int, default=None)
@click.option("--priority", default="normal")
@click.option("--due", default=None, help="YYYY-MM-DD")
@click.option("--label", "labels", multiple=True)
@click.option("--jira", "jira_keys", multiple=True)
@config_option
def thread_add(
    title: str,
    kind: str,
    owner_id: int | None,
    parent_id: int | None,
    priority: str,
    due: str | None,
    labels: tuple[str, ...],
    jira_keys: tuple[str, ...],
    config: Config,
) -> None:
    body = {
        "title": title,
        "kind": kind,
        "owner_id": owner_id,
        "parent_id": parent_id,
        "priority": priority,
        "due_date": due,
        "labels": list(labels),
        "jira_keys": list(jira_keys),
    }
    with _client(config) as client:
        _echo(_call(client, "POST", "/api/threads", json=body))


@thread.command("show")
@click.argument("thread_id", type=int)
@config_option
def thread_show(thread_id: int, config: Config) -> None:
    with _client(config) as client:
        _echo(_call(client, "GET", f"/api/threads/{thread_id}"))
        _echo(_call(client, "GET", f"/api/threads/{thread_id}/mentions"))


@thread.command("set")
@click.argument("thread_id", type=int)
@click.option("--title", default=None)
@click.option("--status", default=None)
@click.option("--priority", default=None)
@click.option("--kind", default=None)
@click.option("--owner-id", type=int, default=None)
@config_option
def thread_set(
    thread_id: int,
    title: str | None,
    status: str | None,
    priority: str | None,
    kind: str | None,
    owner_id: int | None,
    config: Config,
) -> None:
    body = {
        k: v
        for k, v in {
            "title": title,
            "status": status,
            "priority": priority,
            "kind": kind,
            "owner_id": owner_id,
        }.items()
        if v is not None
    }
    with _client(config) as client:
        _echo(_call(client, "PATCH", f"/api/threads/{thread_id}", json=body))


@thread.command("confirm")
@click.argument("thread_id", type=int)
@config_option
def thread_confirm(thread_id: int, config: Config) -> None:
    """Accept an AI-created thread and its provisional mentions."""
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/threads/{thread_id}/confirm"))


@thread.command("parent")
@click.argument("thread_id", type=int)
@click.argument("parent_id", type=int, required=False)
@config_option
def thread_parent(thread_id: int, parent_id: int | None, config: Config) -> None:
    """Set (or, with no PARENT_ID, clear) a thread's parent."""
    with _client(config) as client:
        _echo(
            _call(client, "PUT", f"/api/threads/{thread_id}/parent", json={"parent_id": parent_id})
        )


@thread.command("merge")
@click.argument("into_id", type=int)
@click.argument("from_id", type=int)
@config_option
def thread_merge(into_id: int, from_id: int, config: Config) -> None:
    """Merge FROM_ID into INTO_ID."""
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/threads/{into_id}/merge", json={"from_id": from_id}))


# --- mentions ---


@cli.group()
def mention() -> None:
    """Review mentions."""


@mention.command("list")
@click.option("--state", default="provisional", help="provisional|confirmed|set_aside|unplaced|all")
@click.option("--thread-id", type=int, default=None)
@click.option("--run-id", type=int, default=None)
@config_option
def mention_list(state: str, thread_id: int | None, run_id: int | None, config: Config) -> None:
    params: dict[str, Any] = {"state": state}
    if thread_id is not None:
        params["thread_id"] = thread_id
    if run_id is not None:
        params["job_run_id"] = run_id
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/mentions", params=params))


@mention.command("confirm")
@click.argument("mention_id", type=int)
@config_option
def mention_confirm(mention_id: int, config: Config) -> None:
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/mentions/{mention_id}/confirm"))


@mention.command("move")
@click.argument("mention_id", type=int)
@click.argument("thread_id", type=int)
@config_option
def mention_move(mention_id: int, thread_id: int, config: Config) -> None:
    with _client(config) as client:
        _echo(
            _call(
                client, "PUT", f"/api/mentions/{mention_id}/thread", json={"thread_id": thread_id}
            )
        )


@mention.command("aside")
@click.argument("mention_id", type=int)
@config_option
def mention_aside(mention_id: int, config: Config) -> None:
    """Set a mention aside (noise, already done, not actionable)."""
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/mentions/{mention_id}/set-aside"))


# --- changes ---


@cli.group()
def changes() -> None:
    """Change history and undo."""


@changes.command("list")
@click.option("--run-id", type=int, default=None)
@click.option("--limit", type=int, default=50)
@config_option
def changes_list(run_id: int | None, limit: int, config: Config) -> None:
    params: dict[str, Any] = {"limit": limit}
    if run_id is not None:
        params["job_run_id"] = run_id
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/changes", params=params))


@changes.command("undo")
@click.argument("change_id", type=int)
@config_option
def changes_undo(change_id: int, config: Config) -> None:
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/changes/{change_id}/undo"))


# --- insights ---


@cli.group()
def insight() -> None:
    """AI structure proposals."""


@insight.command("list")
@click.option("--thread", "thread_id", type=int, default=None)
@click.option("--status", default="new", help="new | applied | dismissed | all")
@config_option
def insight_list(thread_id: int | None, status: str, config: Config) -> None:
    params: dict[str, Any] = {"status": status}
    if thread_id is not None:
        params["thread_id"] = thread_id
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/insights", params=params))


@insight.command("apply")
@click.argument("insight_id", type=int)
@config_option
def insight_apply(insight_id: int, config: Config) -> None:
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/insights/{insight_id}/apply"))


@insight.command("dismiss")
@click.argument("insight_id", type=int)
@config_option
def insight_dismiss(insight_id: int, config: Config) -> None:
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/insights/{insight_id}/dismiss"))


# --- jobs ---


@cli.group()
def jobs() -> None:
    """Background jobs."""


@jobs.command("list")
@config_option
def jobs_list(config: Config) -> None:
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/jobs"))


@jobs.command("run")
@click.argument("name")
@click.option("--full", is_flag=True, help="Ignore the last run and process everything.")
@click.option("--no-wait", is_flag=True, help="Return immediately with the run id.")
@config_option
def jobs_run(name: str, full: bool, no_wait: bool, config: Config) -> None:
    params = {"wait": "false" if no_wait else "true", "full": "true" if full else "false"}
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/jobs/{name}/run", params=params))


@jobs.command("runs")
@click.option("--job", "job_name", default=None)
@click.option("--limit", type=int, default=20)
@config_option
def jobs_runs(job_name: str | None, limit: int, config: Config) -> None:
    params: dict[str, Any] = {"limit": limit}
    if job_name:
        params["job_name"] = job_name
    with _client(config) as client:
        _echo(_call(client, "GET", "/api/jobs/runs", params=params))


@jobs.command("log")
@click.argument("run_id", type=int)
@config_option
def jobs_log(run_id: int, config: Config) -> None:
    with _client(config) as client:
        for entry in _call(client, "GET", f"/api/jobs/runs/{run_id}/log"):
            click.echo(
                f"{(entry['created_at'] or '')[11:19]} [{entry['kind']:5}] {entry['message']}"
            )


@jobs.command("revert")
@click.argument("run_id", type=int)
@config_option
def jobs_revert(run_id: int, config: Config) -> None:
    """Undo every change a job run made."""
    with _client(config) as client:
        _echo(_call(client, "POST", f"/api/jobs/runs/{run_id}/revert"))
