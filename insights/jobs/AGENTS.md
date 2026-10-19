# insights/jobs

Background jobs are plain functions `(JobContext) -> str | None`
registered by name on `registry` (`registry.py`). Importing `builtin.py`
registers the built-ins; `server/app.py` does that.

- `registry.py`: `JobRegistry.run` (sync) and `run_async` (daemon thread);
  records a `JobRun`, catches exceptions into `status=failed`.
- `log.py`: `JobLog` writes `JobLogEntry` rows, committing each one, then
  hands the payload to the broadcast hub. Use `llm_call`/`llm_reply`
  around every model call so the GUI shows the prompt size, tier and tokens.
- `broadcast.py`: in-process pub/sub for the SSE live tail. DB is the
  source of truth; the hub only adds latency-free delivery.
- `scheduler.py`: APScheduler cron from `config.jobs.schedules`.

Writing a job:

- Return a one-line result string; it is shown in the runs table.
- Pass `actor=Actor.AI, job_run_id=ctx.job_run.id` to every `core` call
  that mutates, so `revert_run` works.
- `ctx.add_tokens(n)` after each LLM call; `ctx.flag("full")` for the
  "ignore last run" option.
- Commit in sensible batches; a failed job is rolled back from the last
  commit, not from the start.
