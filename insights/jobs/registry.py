"""Job registry: every execution (scheduled or on-demand) records a JobRun.

Jobs are plain callables ``(JobContext) -> str | None`` registered by name.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from insights.config import Config
from insights.core.llm import LLMClient
from insights.db.models import JobRun, JobRunStatus, utcnow
from insights.db.session import SessionFactory
from insights.jobs import broadcast
from insights.jobs.log import JobLog


@dataclass
class JobContext:
    session: Session
    config: Config
    llm: LLMClient
    job_run: JobRun
    params: dict[str, str] = field(default_factory=dict)

    def add_tokens(self, count: int) -> None:
        self.job_run.tokens_used += count

    def flag(self, name: str) -> bool:
        return self.params.get(name, "").lower() in {"1", "true", "yes"}

    def log(self) -> JobLog:
        return JobLog(
            self.session,
            self.job_run.id,
            self.config.llm.model,
            self.config.llm.smart_model,
            preview_chars=self.config.jobs.log_prompt_preview_chars,
            on_entry=lambda payload: broadcast.publish(self.job_run.id, payload),
        )


JobFunc = Callable[[JobContext], str | None]


class UnknownJobError(Exception):
    pass


@dataclass
class JobRegistry:
    jobs: dict[str, JobFunc] = field(default_factory=dict)

    def register(self, name: str) -> Callable[[JobFunc], JobFunc]:
        def decorator(func: JobFunc) -> JobFunc:
            self.jobs[name] = func
            return func

        return decorator

    def names(self) -> list[str]:
        return sorted(self.jobs)

    def run(
        self,
        name: str,
        session_factory: SessionFactory,
        config: Config,
        llm: LLMClient,
        params: dict[str, str] | None = None,
        job_run_id: int | None = None,
    ) -> JobRun:
        if name not in self.jobs:
            raise UnknownJobError(f"unknown job: {name}")
        with session_factory() as session:
            job_run = session.get(JobRun, job_run_id) if job_run_id is not None else None
            if job_run is None:
                job_run = JobRun(job_name=name, payload=dict(params or {}))
                session.add(job_run)
                session.commit()
            context = JobContext(
                session=session, config=config, llm=llm, job_run=job_run, params=params or {}
            )
            log = context.log()
            log.step(f"job '{name}' started")
            try:
                job_run.result = self.jobs[name](context)
                job_run.status = JobRunStatus.SUCCESS
                log.step("done")
            except Exception:
                session.rollback()
                job_run = session.merge(job_run)
                job_run.status = JobRunStatus.FAILED
                job_run.error = traceback.format_exc()
                last_line = job_run.error.splitlines()[-1]
                log.error(f"failed: {last_line}")
            job_run.finished_at = utcnow()
            session.commit()
            broadcast.close(job_run.id)
            return job_run

    def run_async(
        self,
        name: str,
        session_factory: SessionFactory,
        config: Config,
        llm: LLMClient,
        params: dict[str, str] | None = None,
    ) -> int:
        """Create the JobRun now and execute in a daemon thread; returns the run id."""
        if name not in self.jobs:
            raise UnknownJobError(f"unknown job: {name}")
        with session_factory() as session:
            job_run = JobRun(job_name=name, payload=dict(params or {}))
            session.add(job_run)
            session.commit()
            run_id = job_run.id
        threading.Thread(
            target=self.run,
            args=(name, session_factory, config, llm),
            kwargs={"params": params, "job_run_id": run_id},
            daemon=True,
        ).start()
        return run_id


registry = JobRegistry()


def mark_interrupted_runs(session: Session) -> int:
    """Fail JobRuns left 'running' by a dead process. Called at server startup."""
    stale = list(session.scalars(select(JobRun).where(JobRun.status == JobRunStatus.RUNNING)))
    for run in stale:
        run.status = JobRunStatus.FAILED
        run.error = "interrupted: server restarted"
        run.finished_at = utcnow()
    session.commit()
    return len(stale)


def list_runs(session: Session, job_name: str | None = None, limit: int = 50) -> list[JobRun]:
    stmt = select(JobRun).order_by(JobRun.id.desc()).limit(limit)
    if job_name:
        stmt = stmt.where(JobRun.job_name == job_name)
    return list(session.scalars(stmt))


def last_successful_run(session: Session, job_name: str) -> JobRun | None:
    return session.scalar(
        select(JobRun)
        .where(JobRun.job_name == job_name, JobRun.status == JobRunStatus.SUCCESS)
        .order_by(JobRun.id.desc())
    )
