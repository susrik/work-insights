"""Runtime configuration.

The config file (default ``insights.json``, override with
``INSIGHTS_CONFIG_FILENAME``) is private and never committed; see
``insights.example.json`` for the documented shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click
from pydantic import BaseModel, Field

DEFAULT_CONFIG_FILENAME = "insights.json"
CONFIG_ENV_VAR = "INSIGHTS_CONFIG_FILENAME"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 9900


class StorageConfig(BaseModel):
    data_dir: Path = Path("./data")


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./data/insights.db"


class LLMConfig(BaseModel):
    base_url: str | None = None
    api_key: str = ""
    model: str = "gpt-4o-mini"
    smart_model: str = "gpt-4o"
    request_timeout: float = 240

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class JiraConfig(BaseModel):
    """Only ``base_url`` is used in v1 (to render links). Credentials are
    reserved for the future read-only fetch; see docs/explanation/roadmap.md."""

    base_url: str | None = None
    username: str | None = None
    api_token: str | None = None

    def issue_url(self, key: str) -> str | None:
        if not self.base_url:
            return None
        return f"{self.base_url.rstrip('/')}/browse/{key}"


class NotesConfig(BaseModel):
    enabled: bool = False
    root: Path = Path("~/notes")
    people_dir: str = "people"
    exclude: list[str] = Field(default_factory=list)
    window_days: int = 90
    max_file_chars: int = 40000
    repo_url_template: str | None = None

    @property
    def root_path(self) -> Path:
        return self.root.expanduser()

    def note_url(self, rel_path: str) -> str | None:
        if not self.repo_url_template:
            return None
        return self.repo_url_template.format(path=rel_path)


class JobsConfig(BaseModel):
    schedules: dict[str, str] = Field(default_factory=dict)
    log_retention_days: int = 30
    log_prompt_preview_chars: int = 600


class DiscordConfig(BaseModel):
    """Reserved: notifications and a command listener are on the roadmap."""

    enabled: bool = False
    token: str | None = None
    channel_id: int | None = None


class GoogleConfig(BaseModel):
    """Reserved: calendar/email integration is on the roadmap."""

    enabled: bool = False
    client_id: str | None = None
    client_secret: str | None = None


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    api_base_url: str = "http://127.0.0.1:9900"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    jira: JiraConfig = Field(default_factory=JiraConfig)
    notes: NotesConfig = Field(default_factory=NotesConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    google: GoogleConfig = Field(default_factory=GoogleConfig)


def config_filename(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_FILENAME))


def load_config(filename: str | Path | None = None) -> Config:
    path = config_filename(filename)
    if not path.exists():
        return Config()
    with path.open() as f:
        return Config.model_validate(json.load(f))


def _validate_config_option(
    ctx: click.Context, _param: click.Parameter, value: str | None
) -> Config:
    try:
        return load_config(value)
    except (OSError, ValueError) as e:
        raise click.BadParameter(str(e)) from e


config_option = click.option(
    "--config",
    "config",
    type=click.Path(dir_okay=False),
    default=None,
    callback=_validate_config_option,
    help=f"Config file (default: {DEFAULT_CONFIG_FILENAME} or ${CONFIG_ENV_VAR}).",
)
