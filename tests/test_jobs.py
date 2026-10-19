from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from insights.config import Config
from insights.core.llm import FakeLLM, LLMResult
from insights.db.models import JobLogEntry, JobRun, JobRunStatus, utcnow
from insights.db.session import SessionFactory
from insights.jobs import broadcast
from insights.jobs.broadcast import BroadcastHub
from insights.jobs.log import JobLog
from insights.jobs.registry import (
    JobContext,
    JobRegistry,
    UnknownJobError,
    last_successful_run,
    list_runs,
    mark_interrupted_runs,
    registry,
)
from insights.jobs.scheduler import build_scheduler


def test_registry_run_success_and_failure(
    session_factory: SessionFactory, config: Config, fake_llm: FakeLLM
) -> None:
    reg = JobRegistry()

    @reg.register("ok")
    def ok(ctx: JobContext) -> str:
        ctx.add_tokens(5)
        assert ctx.flag("full")
        ctx.log().info("hello", a=1)
        return "fine"

    @reg.register("boom")
    def boom(ctx: JobContext) -> str:
        raise RuntimeError("kaboom")

    run = reg.run("ok", session_factory, config, fake_llm, {"full": "true"})
    assert run.status == JobRunStatus.SUCCESS
    assert run.result == "fine" and run.tokens_used == 5
    run = reg.run("boom", session_factory, config, fake_llm)
    assert run.status == JobRunStatus.FAILED
    assert "kaboom" in (run.error or "")
    try:
        reg.run("missing", session_factory, config, fake_llm)
    except UnknownJobError:
        pass
    else:
        raise AssertionError("expected UnknownJobError")

    with session_factory() as session:
        assert reg.names() == ["boom", "ok"]
        assert [r.job_name for r in list_runs(session)] == ["boom", "ok"]
        assert last_successful_run(session, "ok") is not None
        assert last_successful_run(session, "boom") is None
        kinds = [e.kind for e in list_runs(session, "boom")[0].log_entries]
        assert kinds == ["step", "error"]


def test_run_async(session_factory: SessionFactory, config: Config, fake_llm: FakeLLM) -> None:
    reg = JobRegistry()

    @reg.register("slow")
    def slow(ctx: JobContext) -> str:
        return "done"

    run_id = reg.run_async("slow", session_factory, config, fake_llm)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with session_factory() as session:
            run = session.get(JobRun, run_id)
            assert run is not None
            if run.status != JobRunStatus.RUNNING:
                break
        time.sleep(0.05)
    assert run.status == JobRunStatus.SUCCESS
    try:
        reg.run_async("missing", session_factory, config, fake_llm)
    except UnknownJobError:
        pass
    else:
        raise AssertionError("expected UnknownJobError")


def test_mark_interrupted_runs(session: Session) -> None:
    session.add(JobRun(job_name="x"))
    session.add(JobRun(job_name="y", status=JobRunStatus.SUCCESS))
    session.commit()
    assert mark_interrupted_runs(session) == 1
    assert mark_interrupted_runs(session) == 0


def test_job_log_llm_lines(session: Session) -> None:
    run = JobRun(job_name="x")
    session.add(run)
    session.commit()
    seen: list[dict[str, object]] = []
    log = JobLog(session, run.id, "cheap", "smart", preview_chars=5, on_entry=seen.append)
    log.llm_call(True, "a long prompt", system="sys")
    log.llm_reply(False, LLMResult(text="", tokens_used=7, elapsed_ms=1500))
    log.step("s")
    log.error("e")
    assert [e["kind"] for e in seen] == ["wait", "llm", "step", "error"]
    assert seen[0]["meta"]["preview"] == "a lon"  # type: ignore[index]
    assert seen[0]["meta"]["model"] == "smart"  # type: ignore[index]
    assert "7 tokens in 1.5s" in str(seen[1]["message"])
    assert session.query(JobLogEntry).count() == 4


def test_housekeeping_prunes(
    session_factory: SessionFactory, config: Config, fake_llm: FakeLLM
) -> None:
    with session_factory() as session:
        old = JobRun(job_name="old", started_at=utcnow() - timedelta(days=60))
        session.add(old)
        session.flush()
        session.add(JobLogEntry(run_id=old.id, kind="info", message="m"))
        session.commit()
    run = registry.run("housekeeping", session_factory, config, fake_llm)
    assert run.result == "pruned 1 log entries"
    run = registry.run("housekeeping", session_factory, config, fake_llm)
    assert run.result == "pruned 0 log entries"


def test_scheduler_builds_only_known_jobs(
    config: Config, session_factory: SessionFactory, fake_llm: FakeLLM
) -> None:
    config.jobs.schedules = {"housekeeping": "0 3 * * *", "unknown": "* * * * *"}
    scheduler = build_scheduler(config, registry, session_factory, fake_llm)
    assert [j.id for j in scheduler.get_jobs()] == ["housekeeping"]


def test_broadcast_hub() -> None:
    async def scenario() -> list[dict[str, object]]:
        hub = BroadcastHub()
        hub.attach(asyncio.get_running_loop())
        hub.publish(1, {"id": 1})
        hub.publish(1, {"id": 2})
        received: list[dict[str, object]] = []

        async def consume() -> None:
            async for event in hub.subscribe(1, after_id=1):
                received.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        hub.publish(1, {"id": 1})  # stale, filtered
        hub.publish(1, {"id": 3})
        hub.close(1)
        await asyncio.wait_for(task, 2)
        return received

    events = asyncio.run(scenario())
    assert events == [{"id": 2}, {"id": 3}, {"_done": True}]


def test_broadcast_module_helpers() -> None:
    broadcast.set_hub(None)
    broadcast.publish(1, {"id": 1})  # no hub: no-op
    broadcast.close(1)
    hub = BroadcastHub()
    broadcast.set_hub(hub)
    hub.publish(1, {"id": 1})  # loop not attached: buffered only
    hub.close(1)
    broadcast.set_hub(None)
