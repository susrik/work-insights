"""User-facing JSON API (CLI and external callers). GUI fragments live in gui.py."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from insights import __version__
from insights.config import Config
from insights.core import changes, labels, mentions, people, threads
from insights.core.llm import LLMClient
from insights.db.models import OPEN_STATUSES, Actor, JobRun, Thread, ThreadKind
from insights.db.session import SessionFactory
from insights.jobs.log import entry_payload
from insights.jobs.registry import JobRegistry, UnknownJobError, list_runs
from insights.server import schemas as s
from insights.server.deps import get_config, get_llm, get_registry, get_session, get_session_factory

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]
ConfigDep = Annotated[Config, Depends(get_config)]


def _bad(e: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _not_found(e: ValueError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(e))


@router.get("/status", response_model=s.StatusOut)
def status(session: SessionDep, config: ConfigDep) -> s.StatusOut:
    open_count = session.scalar(
        select(func.count()).select_from(Thread).where(Thread.status.in_(OPEN_STATUSES))
    )
    return s.StatusOut(
        version=__version__,
        threads_open=open_count or 0,
        mentions=mentions.counts_by_state(session),
        llm_enabled=config.llm.enabled,
        notes_enabled=config.notes.enabled,
    )


# --- people ---


@router.get("/people", response_model=list[s.PersonOut])
def list_people(session: SessionDep) -> list[s.PersonOut]:
    return [s.PersonOut.of(p) for p in people.list_people(session)]


@router.post("/people", response_model=s.PersonOut, status_code=201)
def create_person(body: s.PersonIn, session: SessionDep) -> s.PersonOut:
    try:
        person = people.create_person(session, **body.model_dump())
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.PersonOut.of(person)


@router.patch("/people/{person_id}", response_model=s.PersonOut)
def update_person(person_id: int, body: s.PersonUpdate, session: SessionDep) -> s.PersonOut:
    try:
        person = people.update_person(session, person_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.PersonOut.of(person)


@router.delete("/people/{person_id}", status_code=204)
def delete_person(person_id: int, session: SessionDep) -> None:
    try:
        people.delete_person(session, person_id)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()


# --- labels ---


@router.get("/labels", response_model=list[s.LabelOut])
def list_labels(session: SessionDep) -> list[s.LabelOut]:
    return [s.LabelOut.of(lb) for lb in labels.list_labels(session)]


@router.post("/labels", response_model=s.LabelOut, status_code=201)
def create_label(body: s.LabelIn, session: SessionDep) -> s.LabelOut:
    try:
        label = labels.create_label(session, **body.model_dump())
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.LabelOut.of(label)


@router.patch("/labels/{label_id}", response_model=s.LabelOut)
def update_label(label_id: int, body: s.LabelUpdate, session: SessionDep) -> s.LabelOut:
    try:
        label = labels.update_label(session, label_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.LabelOut.of(label)


@router.delete("/labels/{label_id}", status_code=204)
def delete_label(label_id: int, session: SessionDep) -> None:
    try:
        labels.delete_label(session, label_id)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()


# --- threads ---


@router.get("/threads", response_model=list[s.ThreadOut])
def list_threads(
    session: SessionDep,
    status: str = "open",
    kind: ThreadKind | None = None,
    owner_id: int | None = None,
    label_id: Annotated[list[int] | None, Query()] = None,
    provisional: bool | None = None,
    q: str | None = None,
    roots_only: bool = False,
) -> list[s.ThreadOut]:
    flt = threads.ThreadFilter(
        status=status,
        kind=kind,
        owner_id=owner_id,
        label_ids=label_id or [],
        provisional=provisional,
        search=q,
        roots_only=roots_only,
    )
    try:
        rows = threads.list_threads(session, flt)
    except ValueError as e:
        raise _bad(e) from e
    return [s.ThreadOut.of(t) for t in rows]


@router.post("/threads", response_model=s.ThreadOut, status_code=201)
def create_thread(body: s.ThreadIn, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.create_thread(session, **body.model_dump())
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


@router.get("/threads/{thread_id}", response_model=s.ThreadOut)
def get_thread(thread_id: int, session: SessionDep) -> s.ThreadOut:
    try:
        return s.ThreadOut.of(threads.get_thread(session, thread_id))
    except ValueError as e:
        raise _not_found(e) from e


@router.patch("/threads/{thread_id}", response_model=s.ThreadOut)
def update_thread(thread_id: int, body: s.ThreadUpdate, session: SessionDep) -> s.ThreadOut:
    fields = body.model_dump(exclude_unset=True)
    try:
        thread = threads.update_thread(session, thread_id, **fields)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


@router.delete("/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: int, session: SessionDep) -> None:
    try:
        threads.delete_thread(session, thread_id)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()


@router.post("/threads/{thread_id}/confirm", response_model=s.ThreadOut)
def confirm_thread(thread_id: int, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.confirm_thread(session, thread_id)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


@router.put("/threads/{thread_id}/parent", response_model=s.ThreadOut)
def set_parent(thread_id: int, body: s.ThreadParent, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.set_parent(session, thread_id, body.parent_id)
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


@router.put("/threads/{thread_id}/labels", response_model=s.ThreadOut)
def set_labels(thread_id: int, body: s.ThreadLabels, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.set_labels(session, thread_id, body.labels)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


@router.post("/threads/{thread_id}/jira", response_model=s.ThreadOut)
def add_jira_keys(thread_id: int, body: s.ThreadJiraKeys, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.add_jira_keys(session, thread_id, body.keys)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


@router.delete("/threads/{thread_id}/jira/{key}", response_model=s.ThreadOut)
def remove_jira_key(thread_id: int, key: str, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.remove_jira_key(session, thread_id, key)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


@router.post("/threads/{thread_id}/links", response_model=s.ThreadOut)
def link_thread(thread_id: int, body: s.ThreadLinkIn, session: SessionDep) -> s.ThreadOut:
    try:
        threads.link_threads(session, thread_id, body.other_id)
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.ThreadOut.of(threads.get_thread(session, thread_id))


@router.delete("/threads/{thread_id}/links/{other_id}", response_model=s.ThreadOut)
def unlink_thread(thread_id: int, other_id: int, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.get_thread(session, thread_id)
    except ValueError as e:
        raise _not_found(e) from e
    threads.unlink_threads(session, thread_id, other_id)
    session.commit()
    return s.ThreadOut.of(thread)


@router.post("/threads/{thread_id}/merge", response_model=s.ThreadOut)
def merge_thread(thread_id: int, body: s.MergeIn, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.merge_threads(session, thread_id, body.from_id)
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


@router.get("/threads/{thread_id}/mentions", response_model=list[s.MentionOut])
def thread_mentions(thread_id: int, session: SessionDep) -> list[s.MentionOut]:
    try:
        threads.get_thread(session, thread_id)
    except ValueError as e:
        raise _not_found(e) from e
    flt = mentions.MentionFilter(state="all", thread_id=thread_id)
    return [s.MentionOut.of(m) for m in mentions.list_mentions(session, flt)]


# --- mentions ---


@router.get("/mentions", response_model=list[s.MentionOut])
def list_mentions(
    session: SessionDep,
    state: str = "provisional",
    thread_id: int | None = None,
    owner_id: int | None = None,
    job_run_id: int | None = None,
    q: str | None = None,
    limit: int = 500,
) -> list[s.MentionOut]:
    flt = mentions.MentionFilter(
        state=state,
        thread_id=thread_id,
        owner_id=owner_id,
        job_run_id=job_run_id,
        search=q,
        limit=limit,
    )
    try:
        rows = mentions.list_mentions(session, flt)
    except ValueError as e:
        raise _bad(e) from e
    return [s.MentionOut.of(m) for m in rows]


@router.put("/mentions/{mention_id}/thread", response_model=s.MentionOut)
def place_mention(mention_id: int, body: s.MentionPlace, session: SessionDep) -> s.MentionOut:
    try:
        mention = mentions.place_mention(session, mention_id, body.thread_id, actor=Actor.USER)
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.MentionOut.of(mention)


@router.post("/mentions/{mention_id}/confirm", response_model=s.MentionOut)
def confirm_mention(mention_id: int, session: SessionDep) -> s.MentionOut:
    try:
        mention = mentions.confirm_mention(session, mention_id)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.MentionOut.of(mention)


@router.post("/mentions/{mention_id}/set-aside", response_model=s.MentionOut)
def set_aside(mention_id: int, session: SessionDep) -> s.MentionOut:
    try:
        mention = mentions.set_aside(session, mention_id)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.MentionOut.of(mention)


@router.post("/mentions/new-thread", response_model=s.ThreadOut, status_code=201)
def new_thread_from_mentions(body: s.NewThreadFromMentions, session: SessionDep) -> s.ThreadOut:
    try:
        thread = threads.create_thread(
            session,
            body.title,
            kind=body.kind,
            parent_id=body.parent_id,
            owner_id=body.owner_id,
        )
        for mention_id in body.mention_ids:
            mentions.place_mention(session, mention_id, thread.id, actor=Actor.USER)
    except ValueError as e:
        raise _bad(e) from e
    session.commit()
    return s.ThreadOut.of(thread)


# --- changes / undo ---


@router.get("/changes", response_model=list[s.ChangeOut])
def list_changes(
    session: SessionDep,
    entity: str | None = None,
    entity_id: int | None = None,
    job_run_id: int | None = None,
    limit: int = 100,
) -> list[s.ChangeOut]:
    rows = changes.list_changes(session, entity, entity_id, job_run_id, limit)
    return [s.ChangeOut.of(c) for c in rows]


@router.post("/changes/{change_id}/undo", response_model=s.ChangeOut)
def undo_change(change_id: int, session: SessionDep) -> s.ChangeOut:
    try:
        change = changes.undo(session, change_id)
    except ValueError as e:
        raise _not_found(e) from e
    session.commit()
    return s.ChangeOut.of(change)


@router.post("/jobs/runs/{run_id}/revert", response_model=s.RevertOut)
def revert_run(run_id: int, session: SessionDep) -> s.RevertOut:
    if session.get(JobRun, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    count = changes.revert_run(session, run_id)
    session.commit()
    return s.RevertOut(reverted=count)


# --- jobs ---


@router.get("/jobs", response_model=list[str])
def list_jobs(registry: Annotated[JobRegistry, Depends(get_registry)]) -> list[str]:
    return registry.names()


@router.get("/jobs/runs", response_model=list[s.JobRunOut])
def job_runs(
    session: SessionDep, job_name: str | None = None, limit: int = 50
) -> list[s.JobRunOut]:
    return [s.JobRunOut.of(r) for r in list_runs(session, job_name, limit)]


@router.get("/jobs/runs/{run_id}", response_model=s.JobRunOut)
def job_run(run_id: int, session: SessionDep) -> s.JobRunOut:
    run = session.get(JobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return s.JobRunOut.of(run)


@router.get("/jobs/runs/{run_id}/log", response_model=list[s.JobLogEntryOut])
def job_run_log(run_id: int, session: SessionDep) -> list[s.JobLogEntryOut]:
    run = session.get(JobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return [s.JobLogEntryOut(**entry_payload(e)) for e in run.log_entries]


@router.post("/jobs/{name}/run", response_model=s.JobRunOut | s.JobStarted)
def run_job(
    name: str,
    registry: Annotated[JobRegistry, Depends(get_registry)],
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    config: ConfigDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    wait: bool = True,
    full: bool = False,
) -> s.JobRunOut | s.JobStarted:
    params = {"full": "true"} if full else {}
    try:
        if wait:
            run = registry.run(name, session_factory, config, llm, params)
            return s.JobRunOut.of(run)
        run_id = registry.run_async(name, session_factory, config, llm, params)
    except UnknownJobError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return s.JobStarted(run_id=run_id)
