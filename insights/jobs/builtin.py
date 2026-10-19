"""Built-in jobs. Importing this module registers them."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select

from insights.core.intake import run_intake
from insights.db.models import JobLogEntry, JobRun, utcnow
from insights.jobs.registry import JobContext, last_successful_run, registry


@registry.register("housekeeping")
def housekeeping(ctx: JobContext) -> str:
    """Prune job logs older than ``jobs.log_retention_days``."""
    cutoff = utcnow() - timedelta(days=ctx.config.jobs.log_retention_days)
    old_runs = ctx.session.scalars(select(JobRun.id).where(JobRun.started_at < cutoff)).all()
    pruned = 0
    if old_runs:
        pruned = (
            ctx.session.scalar(
                select(func.count())
                .select_from(JobLogEntry)
                .where(JobLogEntry.run_id.in_(list(old_runs)))
            )
            or 0
        )
        ctx.session.execute(delete(JobLogEntry).where(JobLogEntry.run_id.in_(list(old_runs))))
        ctx.session.commit()
    ctx.log().info(f"pruned {pruned} log entries from {len(old_runs)} old runs")
    return f"pruned {pruned} log entries"


@registry.register("intake")
def intake(ctx: JobContext) -> str:
    """Read notes changed since the last successful run, extract mentions and
    place them on threads. ``full=true`` re-reads the whole window."""
    log = ctx.log()
    notes = ctx.config.notes
    if not notes.enabled:
        log.error("notes.enabled is false; nothing to do")
        return "notes disabled"
    if not ctx.config.llm.api_key:
        log.error("llm.api_key is not set; intake needs the LLM")
        return "llm not configured"
    since = None
    if not ctx.flag("full"):
        last = last_successful_run(ctx.session, "intake")
        since = last.started_at if last else None
    summary = run_intake(ctx.session, ctx.llm, notes, since, job_run_id=ctx.job_run.id, log=log)
    ctx.add_tokens(summary.tokens)
    return summary.as_text()
