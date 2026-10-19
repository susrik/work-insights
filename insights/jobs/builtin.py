"""Built-in jobs. Importing this module registers them."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select

from insights.db.models import JobLogEntry, JobRun, utcnow
from insights.jobs.registry import JobContext, registry


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
