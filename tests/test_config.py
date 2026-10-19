from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from insights.config import (
    CONFIG_ENV_VAR,
    Config,
    JiraConfig,
    NotesConfig,
    _validate_config_option,
    config_filename,
    load_config,
)


def test_defaults_when_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.server.port == 9900
    assert config.llm.enabled is False
    assert config.notes.enabled is False


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "custom.json"
    path.write_text(json.dumps({"server": {"port": 1234}}))
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    assert config_filename() == path
    assert load_config().server.port == 1234
    assert config_filename("explicit.json") == Path("explicit.json")


def test_notes_helpers() -> None:
    notes = NotesConfig(root=Path("~/n"), repo_url_template="https://x/{path}")
    assert notes.root_path.is_absolute()
    assert notes.note_url("a/b.md") == "https://x/a/b.md"
    assert NotesConfig().note_url("a.md") is None


def test_jira_issue_url() -> None:
    assert JiraConfig(base_url="https://j/").issue_url("AB-1") == "https://j/browse/AB-1"
    assert JiraConfig().issue_url("AB-1") is None


def test_click_callback(tmp_path: Path) -> None:
    good = tmp_path / "ok.json"
    good.write_text("{}")
    assert isinstance(_validate_config_option(None, None, str(good)), Config)  # type: ignore[arg-type]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(click.BadParameter):
        _validate_config_option(None, None, str(bad))  # type: ignore[arg-type]
