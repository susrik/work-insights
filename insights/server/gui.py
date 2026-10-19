"""GUI pages and htmx fragments. Pages extend ``_layout.html``; fragments
(``_*.html``) are what htmx swaps in, and a mutation returns the fragment it
belongs to. Business logic stays in ``insights.core``."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from insights.config import Config
from insights.core import mentions
from insights.core.llm import LLMClient
from insights.db.models import JobRun, JobRunStatus, ThreadKind, ThreadPriority, ThreadStatus
from insights.db.session import SessionFactory
from insights.jobs.log import entry_payload
from insights.jobs.registry import JobRegistry, UnknownJobError, list_runs
from insights.server.deps import get_config, get_llm, get_registry, get_session, get_session_factory

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

SessionDep = Annotated[Session, Depends(get_session)]
ConfigDep = Annotated[Config, Depends(get_config)]


def render(request: Request, name: str, **context: Any) -> HTMLResponse:
    context.setdefault("statuses", list(ThreadStatus))
    context.setdefault("priorities", list(ThreadPriority))
    context.setdefault("kinds", list(ThreadKind))
    context.setdefault("today", date.today())
    return templates.TemplateResponse(request, name, context)


# --- pages ---


@router.get("/", response_class=HTMLResponse)
def today_page(request: Request, session: SessionDep, config: ConfigDep) -> HTMLResponse:
    counts = mentions.counts_by_state(session)
    warnings: list[str] = []
    if not config.llm.enabled:
        warnings.append("No LLM API key configured: intake and insights are unavailable.")
    if not config.notes.enabled:
        warnings.append("Notes are disabled: set notes.enabled and notes.root in the config.")
    return render(
        request,
        "today.html",
        page="today",
        mention_counts=counts,
        warnings=warnings,
        last_runs=list_runs(session, limit=5),
    )


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request, session: SessionDep, registry: Annotated[JobRegistry, Depends(get_registry)]
) -> HTMLResponse:
    return render(request, "jobs.html", page="jobs", jobs=registry.names(), runs=list_runs(session))


# --- job fragments ---


@router.get("/gui/jobs/runs", response_class=HTMLResponse)
def job_runs_fragment(request: Request, session: SessionDep) -> HTMLResponse:
    return render(request, "_job_runs.html", runs=list_runs(session))


@router.post("/gui/jobs/{name}/run", response_class=HTMLResponse)
def run_job_fragment(
    name: str,
    request: Request,
    registry: Annotated[JobRegistry, Depends(get_registry)],
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    full: bool = False,
) -> HTMLResponse:
    params = {"full": "true"} if full else {}
    try:
        run_id = registry.run_async(name, session_factory, config, llm, params)
    except UnknownJobError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    with session_factory() as session:
        run = session.get(JobRun, run_id)
        return render(request, "_job_log.html", run=run, entries=[])


@router.get("/gui/jobs/runs/{run_id}/log", response_class=HTMLResponse)
def job_log_fragment(run_id: int, request: Request, session: SessionDep) -> HTMLResponse:
    run = session.get(JobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    entries = [entry_payload(e) for e in run.log_entries]
    return render(request, "_job_log.html", run=run, entries=entries)


@router.get("/gui/jobs/runs/{run_id}/events")
async def job_events(
    run_id: int,
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    after: int = 0,
) -> StreamingResponse:
    """SSE: replay persisted entries after ``after``, then live-tail the hub."""
    hub = request.app.state.broadcast_hub

    def line(event: str, payload: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    def entry_html(payload: dict[str, Any]) -> str:
        return templates.get_template("_job_log_entry.html").render(entry=payload)

    async def stream() -> Any:
        last = after
        with session_factory() as session:
            run = session.get(JobRun, run_id)
            if run is None:
                yield line("error", {"html": "<p class='text-error'>run not found</p>"})
                return
            for entry in run.log_entries:
                if entry.id > last:
                    payload = entry_payload(entry)
                    last = entry.id
                    yield line("log", {"id": last, "html": entry_html(payload)})
            finished = run.status != JobRunStatus.RUNNING
        if finished:
            yield line("done", {"html": ""})
            return
        async for event in hub.subscribe(run_id, after_id=last):
            if event.get("_done"):
                yield line("done", {"html": ""})
                return
            yield line("log", {"id": event["id"], "html": entry_html(event)})

    return StreamingResponse(stream(), media_type="text/event-stream")
