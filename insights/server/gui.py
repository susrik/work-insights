"""GUI pages and htmx fragments. Pages extend ``_layout.html``; fragments
(``_*.html``) are what htmx swaps in, and a mutation returns the fragment it
belongs to. Business logic stays in ``insights.core``."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from insights.config import Config
from insights.core import changes, mentions, threads
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
    context.setdefault("config", request.app.state.config)
    context.setdefault("statuses", list(ThreadStatus))
    context.setdefault("priorities", list(ThreadPriority))
    context.setdefault("kinds", list(ThreadKind))
    context.setdefault("today", date.today())
    return templates.TemplateResponse(request, name, context)


# --- pages ---


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request, session: SessionDep, registry: Annotated[JobRegistry, Depends(get_registry)]
) -> HTMLResponse:
    return render(request, "jobs.html", page="jobs", jobs=registry.names(), runs=list_runs(session))


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, session: SessionDep, run: int | None = None) -> HTMLResponse:
    return render(
        request,
        "review.html",
        page="review",
        counts=mentions.counts_by_state(session),
        intake_runs=list_runs(session, job_name="intake", limit=10),
        **_review_context(session, run),
    )


def _review_context(session: Session, run_id: int | None) -> dict[str, Any]:
    return {
        "groups": mentions.review_groups(session, run_id),
        "open_threads": threads.list_threads(session, threads.ThreadFilter(status="open")),
        "run_id": run_id,
    }


# --- review fragments ---


def _review_fragment(request: Request, session: Session, run_id: int | None) -> HTMLResponse:
    session.commit()
    return render(request, "_review_groups.html", **_review_context(session, run_id))


@router.post("/gui/review/mentions/{mention_id}/confirm", response_class=HTMLResponse)
def review_confirm(
    mention_id: int, request: Request, session: SessionDep, run: int | None = None
) -> HTMLResponse:
    _guard(lambda: mentions.confirm_mention(session, mention_id))
    return _review_fragment(request, session, run)


@router.post("/gui/review/mentions/{mention_id}/set-aside", response_class=HTMLResponse)
def review_set_aside(
    mention_id: int, request: Request, session: SessionDep, run: int | None = None
) -> HTMLResponse:
    _guard(lambda: mentions.set_aside(session, mention_id))
    return _review_fragment(request, session, run)


@router.post("/gui/review/mentions/{mention_id}/move", response_class=HTMLResponse)
def review_move(
    mention_id: int,
    request: Request,
    session: SessionDep,
    thread_id: Annotated[int, Form()],
    run: int | None = None,
) -> HTMLResponse:
    _guard(lambda: mentions.place_mention(session, mention_id, thread_id))
    return _review_fragment(request, session, run)


@router.post("/gui/review/mentions/{mention_id}/new-thread", response_class=HTMLResponse)
def review_new_thread(
    mention_id: int,
    request: Request,
    session: SessionDep,
    title: Annotated[str, Form()],
    kind: Annotated[ThreadKind, Form()] = ThreadKind.WORK,
    run: int | None = None,
) -> HTMLResponse:
    def go() -> None:
        thread = threads.create_thread(session, title, kind=kind)
        mentions.place_mention(session, mention_id, thread.id)

    _guard(go)
    return _review_fragment(request, session, run)


@router.post("/gui/review/mentions/{mention_id}/restore", response_class=HTMLResponse)
def review_restore(
    mention_id: int, request: Request, session: SessionDep, run: int | None = None
) -> HTMLResponse:
    """Bring a set-aside mention back as unplaced so it can be put somewhere."""
    _guard(lambda: mentions.place_mention(session, mention_id, None, confirmed=False))
    return _review_fragment(request, session, run)


@router.post("/gui/review/threads/{thread_id}/confirm", response_class=HTMLResponse)
def review_confirm_thread(
    thread_id: int, request: Request, session: SessionDep, run: int | None = None
) -> HTMLResponse:
    _guard(lambda: threads.confirm_thread(session, thread_id))
    return _review_fragment(request, session, run)


@router.post("/gui/review/threads/{thread_id}/dismiss", response_class=HTMLResponse)
def review_dismiss_thread(
    thread_id: int, request: Request, session: SessionDep, run: int | None = None
) -> HTMLResponse:
    """Drop a provisional thread; its mentions become unplaced."""
    _guard(lambda: threads.delete_thread(session, thread_id))
    return _review_fragment(request, session, run)


@router.post("/gui/review/runs/{run_id}/revert", response_class=HTMLResponse)
def review_revert_run(run_id: int, request: Request, session: SessionDep) -> HTMLResponse:
    changes.revert_run(session, run_id)
    return _review_fragment(request, session, None)


def _guard(action: Any) -> None:
    try:
        action()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


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
