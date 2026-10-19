"""Threads, People, Labels and Today pages with their htmx fragments. Every
mutation calls ``core`` and returns the fragment it belongs to."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from insights.core import changes, focus, labels, mentions, people, summaries, threads
from insights.db.models import Thread, ThreadKind, ThreadPriority, ThreadStatus
from insights.jobs.registry import list_runs
from insights.server.gui import ConfigDep, SessionDep, render

router = APIRouter()


def _guard(action: Any) -> Any:
    try:
        return action()
    except ValueError as e:
        status = 404 if "not found" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e)) from e


# --- Today ---


@router.get("/", response_class=HTMLResponse)
def today_page(request: Request, session: SessionDep, config: ConfigDep) -> HTMLResponse:
    warnings: list[str] = []
    if not config.llm.enabled:
        warnings.append("No LLM API key configured: intake and insights are unavailable.")
    if not config.notes.enabled:
        warnings.append("Notes are disabled: set notes.enabled and notes.root in the config.")
    return render(
        request,
        "today.html",
        page="today",
        mention_counts=mentions.counts_by_state(session),
        warnings=warnings,
        last_runs=list_runs(session, limit=5),
        ranked=focus.rank_threads(session),
        attention=focus.attention(session),
    )


# --- Threads ---


@router.get("/threads", response_class=HTMLResponse)
def threads_page(
    request: Request,
    session: SessionDep,
    status: str = "open",
    q: str | None = None,
    label: int | None = None,
    owner: int | None = None,
    selected: int | None = None,
) -> HTMLResponse:
    flt = threads.ThreadFilter(
        status=status,
        search=q or None,
        label_ids=[label] if label else [],
        owner_id=owner,
    )
    return render(
        request,
        "threads.html",
        page="threads",
        tree=_tree(threads.list_threads(session, flt)),
        parent_options=_tree(threads.list_threads(session, threads.ThreadFilter(status="open"))),
        filters={"status": status, "q": q or "", "label": label, "owner": owner},
        all_labels=labels.list_labels(session),
        all_people=people.list_people(session),
        selected=_thread_context(session, selected) if selected else None,
    )


def _tree(rows: list[Thread]) -> list[tuple[Thread, int]]:
    """Flatten threads into (thread, depth) in tree order. A thread whose
    parent is not in the result set is shown as a root."""
    ids = {t.id for t in rows}
    by_parent: dict[int | None, list[Thread]] = {}
    for t in rows:
        key = t.parent_id if t.parent_id in ids else None
        by_parent.setdefault(key, []).append(t)
    out: list[tuple[Thread, int]] = []

    def walk(parent: int | None, depth: int) -> None:
        for t in sorted(by_parent.get(parent, []), key=lambda t: (t.priority_rank, t.title)):
            out.append((t, depth))
            walk(t.id, depth + 1)

    walk(None, 0)
    return out


def _thread_context(session: Session, thread_id: int) -> dict[str, Any]:
    thread = _guard(lambda: threads.get_thread(session, thread_id))
    return {
        "thread": thread,
        "ancestors": threads.ancestors(thread),
        "mentions": mentions.list_mentions(
            session, mentions.MentionFilter(state="all", thread_id=thread.id)
        ),
        "changes": changes.list_changes(session, changes.ENTITY_THREAD, thread.id, limit=20),
        "candidates": [
            t
            for t in threads.list_threads(session, threads.ThreadFilter(status="open"))
            if t.id != thread.id
        ],
        "all_labels": labels.list_labels(session),
        "all_people": people.list_people(session),
    }


def _thread_fragment(request: Request, session: Session, thread_id: int) -> HTMLResponse:
    session.commit()
    return render(request, "_thread_detail.html", **_thread_context(session, thread_id))


@router.get("/gui/threads/{thread_id}", response_class=HTMLResponse)
def thread_detail(thread_id: int, request: Request, session: SessionDep) -> HTMLResponse:
    return render(request, "_thread_detail.html", **_thread_context(session, thread_id))


@router.post("/gui/threads", response_class=HTMLResponse)
def thread_create(
    request: Request,
    session: SessionDep,
    title: Annotated[str, Form()],
    kind: Annotated[ThreadKind, Form()] = ThreadKind.WORK,
    parent_id: Annotated[int | None, Form()] = None,
) -> RedirectResponse:
    thread = _guard(lambda: threads.create_thread(session, title, kind=kind, parent_id=parent_id))
    session.commit()
    return RedirectResponse(f"/threads?selected={thread.id}", status_code=303)


@router.post("/gui/threads/{thread_id}", response_class=HTMLResponse)
def thread_update(
    thread_id: int,
    request: Request,
    session: SessionDep,
    title: Annotated[str, Form()],
    kind: Annotated[ThreadKind, Form()],
    status: Annotated[ThreadStatus, Form()],
    priority: Annotated[ThreadPriority, Form()],
    description: Annotated[str, Form()] = "",
    owner_id: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
    labels_text: Annotated[str, Form()] = "",
    jira_text: Annotated[str, Form()] = "",
) -> HTMLResponse:
    def go() -> None:
        threads.update_thread(
            session,
            thread_id,
            title=title,
            description=description,
            kind=kind,
            status=status,
            priority=priority,
            owner_id=int(owner_id) if owner_id else None,
            due_date=date.fromisoformat(due_date) if due_date else None,
        )
        threads.set_labels(session, thread_id, _split(labels_text))
        thread = threads.get_thread(session, thread_id)
        wanted = {k.upper() for k in _split(jira_text)}
        for key in thread.jira_keys:
            if key not in wanted:
                threads.remove_jira_key(session, thread_id, key)
        threads.add_jira_keys(session, thread_id, sorted(wanted))

    _guard(go)
    return _thread_fragment(request, session, thread_id)


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


@router.post("/gui/threads/{thread_id}/parent", response_class=HTMLResponse)
def thread_set_parent(
    thread_id: int,
    request: Request,
    session: SessionDep,
    parent_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    _guard(lambda: threads.set_parent(session, thread_id, int(parent_id) if parent_id else None))
    return _thread_fragment(request, session, thread_id)


@router.post("/gui/threads/{thread_id}/merge", response_class=HTMLResponse)
def thread_merge(
    thread_id: int,
    request: Request,
    session: SessionDep,
    from_id: Annotated[int, Form()],
) -> HTMLResponse:
    _guard(lambda: threads.merge_threads(session, thread_id, from_id))
    return _thread_fragment(request, session, thread_id)


@router.post("/gui/threads/{thread_id}/link", response_class=HTMLResponse)
def thread_link(
    thread_id: int,
    request: Request,
    session: SessionDep,
    other_id: Annotated[int, Form()],
) -> HTMLResponse:
    _guard(lambda: threads.link_threads(session, thread_id, other_id))
    return _thread_fragment(request, session, thread_id)


@router.post("/gui/threads/{thread_id}/unlink/{other_id}", response_class=HTMLResponse)
def thread_unlink(
    thread_id: int, other_id: int, request: Request, session: SessionDep
) -> HTMLResponse:
    threads.unlink_threads(session, thread_id, other_id)
    return _thread_fragment(request, session, thread_id)


@router.post("/gui/threads/{thread_id}/confirm", response_class=HTMLResponse)
def thread_confirm(thread_id: int, request: Request, session: SessionDep) -> HTMLResponse:
    _guard(lambda: threads.confirm_thread(session, thread_id))
    return _thread_fragment(request, session, thread_id)


@router.post("/gui/threads/{thread_id}/status", response_class=HTMLResponse)
def thread_status(
    thread_id: int,
    request: Request,
    session: SessionDep,
    status: Annotated[ThreadStatus, Form()],
) -> HTMLResponse:
    """Quick status change from a list; returns an empty fragment so the row
    can be removed, or the detail when the thread is selected."""
    _guard(lambda: threads.update_thread(session, thread_id, status=status))
    return _thread_fragment(request, session, thread_id)


@router.post("/gui/threads/{thread_id}/delete")
def thread_delete(thread_id: int, request: Request, session: SessionDep) -> RedirectResponse:
    _guard(lambda: threads.delete_thread(session, thread_id))
    session.commit()
    return RedirectResponse("/threads", status_code=303)


@router.post("/gui/threads/{thread_id}/mentions/{mention_id}/detach", response_class=HTMLResponse)
def thread_detach_mention(
    thread_id: int, mention_id: int, request: Request, session: SessionDep
) -> HTMLResponse:
    _guard(lambda: mentions.place_mention(session, mention_id, None))
    return _thread_fragment(request, session, thread_id)


@router.post("/gui/changes/{change_id}/undo", response_class=HTMLResponse)
def change_undo(
    change_id: int, request: Request, session: SessionDep, thread_id: int
) -> HTMLResponse:
    _guard(lambda: changes.undo(session, change_id))
    return _thread_fragment(request, session, thread_id)


# --- People ---


@router.get("/people", response_class=HTMLResponse)
def people_page(request: Request, session: SessionDep) -> HTMLResponse:
    return render(request, "people.html", page="people", people=people.list_people(session))


@router.post("/gui/people")
def person_create(
    session: SessionDep,
    name: Annotated[str, Form()],
    notes_dir: Annotated[str, Form()] = "",
    aliases: Annotated[str, Form()] = "",
    is_self: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    _guard(
        lambda: people.create_person(
            session, name, is_self=is_self, notes_dir=notes_dir or None, aliases=_split(aliases)
        )
    )
    session.commit()
    return RedirectResponse("/people", status_code=303)


@router.post("/gui/people/{person_id}")
def person_update(
    person_id: int,
    session: SessionDep,
    name: Annotated[str, Form()],
    notes_dir: Annotated[str, Form()] = "",
    aliases: Annotated[str, Form()] = "",
    is_self: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    _guard(
        lambda: people.update_person(
            session,
            person_id,
            name=name,
            is_self=is_self,
            notes_dir=notes_dir,
            aliases=_split(aliases),
        )
    )
    session.commit()
    return RedirectResponse(f"/people/{person_id}", status_code=303)


@router.post("/gui/people/{person_id}/delete")
def person_delete(person_id: int, session: SessionDep) -> RedirectResponse:
    _guard(lambda: people.delete_person(session, person_id))
    session.commit()
    return RedirectResponse("/people", status_code=303)


@router.get("/people/{person_id}", response_class=HTMLResponse)
def person_page(person_id: int, request: Request, session: SessionDep) -> HTMLResponse:
    person, summary = _guard(lambda: summaries.person_summary(session, person_id))
    return render(
        request,
        "person.html",
        page="people",
        person=person,
        summary=summary,
        subject=person.name,
        edit_url=f"/gui/people/{person.id}",
    )


# --- Labels ---


@router.get("/labels", response_class=HTMLResponse)
def labels_page(request: Request, session: SessionDep) -> HTMLResponse:
    return render(request, "labels.html", page="labels", labels=labels.list_labels(session))


@router.post("/gui/labels")
def label_create(
    session: SessionDep,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    note_path_prefixes: Annotated[str, Form()] = "",
    jira_projects: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _guard(
        lambda: labels.create_label(
            session,
            name,
            description=description,
            note_path_prefixes=_split(note_path_prefixes),
            jira_projects=_split(jira_projects),
        )
    )
    session.commit()
    return RedirectResponse("/labels", status_code=303)


@router.post("/gui/labels/{label_id}")
def label_update(
    label_id: int,
    session: SessionDep,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    note_path_prefixes: Annotated[str, Form()] = "",
    jira_projects: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _guard(
        lambda: labels.update_label(
            session,
            label_id,
            name=name,
            description=description,
            note_path_prefixes=_split(note_path_prefixes),
            jira_projects=_split(jira_projects),
        )
    )
    session.commit()
    return RedirectResponse(f"/labels/{label_id}", status_code=303)


@router.post("/gui/labels/{label_id}/delete")
def label_delete(label_id: int, session: SessionDep) -> RedirectResponse:
    _guard(lambda: labels.delete_label(session, label_id))
    session.commit()
    return RedirectResponse("/labels", status_code=303)


@router.get("/labels/{label_id}", response_class=HTMLResponse)
def label_page(label_id: int, request: Request, session: SessionDep) -> HTMLResponse:
    label, summary = _guard(lambda: summaries.label_summary(session, label_id))
    return render(
        request,
        "label.html",
        page="labels",
        label=label,
        summary=summary,
        subject=label.name,
        edit_url=f"/gui/labels/{label.id}",
    )
