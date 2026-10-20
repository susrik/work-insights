from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from insights.cli import main as cli_main
from insights.cli.main import cli


def _use_transport(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    monkeypatch.setattr(cli_main, "_transport", httpx.MockTransport(handler))  # type: ignore[arg-type]


@pytest.fixture
def runner(client: TestClient, config_file: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """Route the CLI's httpx client through the in-process TestClient."""

    def forward(request: httpx.Request) -> httpx.Response:
        response = client.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.read(),
            headers={k: v for k, v in request.headers.items() if k != "host"},
        )
        return httpx.Response(response.status_code, content=response.content)

    _use_transport(monkeypatch, forward)
    return CliRunner()


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "insights.json"
    path.write_text(json.dumps({"api_base_url": "http://testserver"}))
    monkeypatch.setenv("INSIGHTS_CONFIG_FILENAME", str(path))
    return path


def _ok(runner: CliRunner, *args: str) -> object:
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.output) if result.output.strip().startswith(("{", "[")) else None


def test_cli_roundtrip(runner: CliRunner) -> None:
    status = _ok(runner, "status")
    assert status["ok"]  # type: ignore[index]

    me = _ok(runner, "person", "add", "Alex", "--self", "--alias", "E")
    assert me["is_self"]  # type: ignore[index]
    assert len(_ok(runner, "person", "list")) == 1  # type: ignore[arg-type]

    _ok(runner, "label", "add", "dash", "--path", "swd/dashboard", "--jira", "DBOARD3")
    assert _ok(runner, "label", "list")[0]["jira_projects"] == ["DBOARD3"]  # type: ignore[index]

    root = _ok(runner, "thread", "add", "Root", "--label", "dash", "--jira", "AB-1")
    rid = root["id"]  # type: ignore[index]
    child = _ok(runner, "thread", "add", "Child", "--kind", "goal", "--owner-id", str(me["id"]))  # type: ignore[index]
    cid = child["id"]  # type: ignore[index]
    _ok(runner, "thread", "parent", str(cid), str(rid))
    _ok(runner, "thread", "parent", str(cid))
    _ok(runner, "thread", "set", str(cid), "--status", "done", "--title", "Child!")
    listed = _ok(
        runner,
        "thread",
        "list",
        "--status",
        "all",
        "--kind",
        "goal",
        "-q",
        "child",
        "--owner-id",
        str(me["id"]),
    )  # type: ignore[index]
    assert listed[0]["title"] == "Child!"  # type: ignore[index]
    assert _ok(runner, "thread", "list", "--provisional") == []
    assert _ok(runner, "thread", "list", "--jira", "ab")[0]["id"] == rid  # type: ignore[index]
    _ok(runner, "thread", "confirm", str(rid))
    _ok(runner, "thread", "merge", str(rid), str(cid))
    result = runner.invoke(cli, ["thread", "show", str(rid)])
    assert result.exit_code == 0 and '"Root"' in result.output

    assert _ok(runner, "mention", "list", "--thread-id", str(rid), "--run-id", "1") == []
    result = runner.invoke(cli, ["mention", "confirm", "999"])
    assert result.exit_code != 0 and "404" in result.output
    result = runner.invoke(cli, ["mention", "move", "999", str(rid)])
    assert result.exit_code != 0
    result = runner.invoke(cli, ["mention", "aside", "999"])
    assert result.exit_code != 0

    assert "housekeeping" in _ok(runner, "jobs", "list")  # type: ignore[operator]
    run = _ok(runner, "jobs", "run", "housekeeping", "--full")
    assert run["status"] == "success"  # type: ignore[index]
    started = _ok(runner, "jobs", "run", "housekeeping", "--no-wait")
    assert "run_id" in started  # type: ignore[operator]
    assert _ok(runner, "jobs", "runs", "--job", "housekeeping", "--limit", "5")
    result = runner.invoke(cli, ["jobs", "log", str(run["id"])])  # type: ignore[index]
    assert result.exit_code == 0 and "[step ]" in result.output
    assert _ok(runner, "jobs", "revert", str(run["id"])) == {"reverted": 0}  # type: ignore[index]

    assert _ok(runner, "insight", "list", "--thread", str(rid), "--status", "all") == []
    result = runner.invoke(cli, ["insight", "apply", "999"])
    assert result.exit_code != 0 and "404" in result.output
    result = runner.invoke(cli, ["insight", "dismiss", "999"])
    assert result.exit_code != 0

    changes = _ok(runner, "changes", "list", "--run-id", "0")
    assert changes == []
    changes = _ok(runner, "changes", "list")
    assert changes
    undone = _ok(runner, "changes", "undo", str(changes[0]["id"]))  # type: ignore[index]
    assert undone["reverted_at"]  # type: ignore[index]


def test_cli_connection_error(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _use_transport(monkeypatch, refuse)
    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code != 0 and "cannot reach" in result.output


def test_cli_error_without_json(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_transport(monkeypatch, lambda r: httpx.Response(500, text="oops"))
    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code != 0 and "oops" in result.output


def test_db_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa

    monkeypatch.chdir(tmp_path)  # must work outside the repo root
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"database": {"url": url}}))
    result = CliRunner().invoke(cli, ["db", "upgrade", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    names = sa.inspect(sa.create_engine(url)).get_table_names()
    assert {"threads", "mentions", "labels", "changes", "insights", "job_runs"} <= set(names)
