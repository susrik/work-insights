# insights/cli

`main.py` is a thin click client over `/api`. Every command takes
`--config` (shared `config_option` callback) and reads `api_base_url`
from it. Output is JSON (`_echo`) so it can be piped into `jq`.

- One group per noun: `person`, `label`, `thread`, `mention`, `changes`,
  `jobs`, plus `status` and `db upgrade`.
- `_call` turns HTTP errors into `click.ClickException` with the server's
  `detail`.
- `_transport` is a module-level hook so tests can route through the
  in-process app without a socket; leave it `None` in production code.
- `db upgrade` is the only command that bypasses the API (runs Alembic).

When you add an endpoint, add the matching command here and a line in
`tests/test_cli.py::test_cli_roundtrip`.
