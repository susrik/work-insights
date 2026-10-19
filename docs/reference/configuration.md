# Configuration

The config file is `insights.json` in the working directory, or the path in
`$INSIGHTS_CONFIG_FILENAME`, or `--config` on the CLI. Missing keys take the
defaults below. See `insights.example.json` for a complete example.

| Key | Default | Meaning |
|-----|---------|---------|
| `server.host` | `127.0.0.1` | Bind address |
| `server.port` | `9900` | Port |
| `storage.data_dir` | `./data` | Working files |
| `database.url` | `sqlite:///./data/insights.db` | SQLAlchemy URL |
| `api_base_url` | `http://127.0.0.1:9900` | Where the CLI finds the server |
| `llm.base_url` | none | OpenAI-compatible endpoint |
| `llm.api_key` | empty | Empty disables all AI features |
| `llm.model` | `gpt-4o-mini` | Regular tier, used for intake |
| `llm.smart_model` | `gpt-4o` | Smart tier, used for structure insights |
| `llm.request_timeout` | `240` | Seconds |
| `jira.base_url` | none | Used only to render links to issues |
| `notes.enabled` | `false` | Turn on the notes reader |
| `notes.root` | `~/notes` | Notes tree (read-only) |
| `notes.people_dir` | `people` | Folder with one subfolder per person |
| `notes.exclude` | `[]` | Path prefixes to skip |
| `notes.window_days` | `90` | How far back a full intake looks |
| `notes.max_file_chars` | `40000` | Larger files are truncated |
| `notes.repo_url_template` | none | e.g. `https://github.com/you/notes/blob/main/{path}` |
| `jobs.schedules` | `{}` | `{"intake": "0 7 * * 1-5"}` cron strings |
| `jobs.log_retention_days` | `30` | Housekeeping prunes older logs |
| `jobs.log_prompt_preview_chars` | `600` | Prompt preview length in the live log |
| `discord.enabled` | `false` | Reserved |
| `google.enabled` | `false` | Reserved |
