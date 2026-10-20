"""Threads, People, Labels and Today pages with their htmx fragments. Every
mutation calls ``core`` and returns the fragment it belongs to."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from insights.core import (
    changes,
    focus,
    insights_scan,
    labels,
    mentions,
    people,
    summaries,
    threads,
)
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
        open_insights=insights_scan.list_insights(session, limit=20),
    )


# --- Threads ---


STATUS_FILTERS = ["open", "all", *(s.value for s in ThreadStatus if s is not ThreadStatus.OPEN)]
CONFIRMED_FILTERS = {"all": None, "confirmed": False, "provisional": True}
THREAD_COLUMNS = [
    ("kind", "Kind"),
    ("status", "Status"),
    ("priority", "Priority"),
    ("owner", "Owner"),
    ("labels", "Labels"),
    ("jira", "Jira"),
    ("due", "Due"),
    ("updated", "Updated"),
    ("confirmed", "Confirmed"),
]


def _opt_int(raw: str, name: str, problems: list[str]) -> int | None:
    if not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        problems.append(f"Ignored {name}: {raw!r} is not a number.")
        return None


def _thread_filters(
    status: str, q: str, label: str, owner: str, confirmed: str, jira: str
) -> tuple[threads.ThreadFilter, dict[str, Any], list[str]]:
    """Parse the filter form leniently: form selects submit "" for "any", and a
    hand-edited URL must never produce an error page, only a warning."""
    problems: list[str] = []
    if status not in STATUS_FILTERS:
        problems.append(f"Ignored status: {status!r} is not one of {', '.join(STATUS_FILTERS)}.")
        status = "open"
    if confirmed not in CONFIRMED_FILTERS:
        problems.append(f"Ignored confirmed: {confirmed!r} is not all, confirmed or provisional.")
        confirmed = "all"
    label_id = _opt_int(label, "label", problems)
    owner_id = _opt_int(owner, "owner", problems)
    flt = threads.ThreadFilter(
        status=status,
        search=q.strip() or None,
        label_ids=[label_id] if label_id else [],
        owner_id=owner_id,
        provisional=CONFIRMED_FILTERS[confirmed],
        jira=jira.strip() or None,
    )
    values = {
        "status": status,
        "q": q,
        "label": label_id,
        "owner": owner_id,
        "confirmed": confirmed,
        "jira": jira,
    }
    return flt, values, problems


def _list_url(values: dict[str, Any]) -> str:
    """``/threads?...`` carrying the current filters, for push-url and refresh."""
    query = urlencode({k: v for k, v in values.items() if v not in ("", None)})
    return f"/threads?{query}"


@router.get("/threads", response_class=HTMLResponse)
def threads_page(
    request: Request,
    session: SessionDep,
    status: str = "open",
    q: str = "",
    label: str = "",
    owner: str = "",
    confirmed: str = "all",
    jira: str = "",
    selected: int | None = None,
) -> HTMLResponse:
    flt, values, problems = _thread_filters(status, q, label, owner, confirmed, jira)
    return render(
        request,
        "threads.html",
        page="threads",
        tree=_tree(threads.list_threads(session, flt)),
        parent_options=_tree(threads.list_threads(session, threads.ThreadFilter(status="open"))),
        filters=values,
        list_url=_list_url(values),
        filter_problems=problems,
        status_filters=STATUS_FILTERS,
        confirmed_filters=list(CONFIRMED_FILTERS),
        columns=THREAD_COLUMNS,
        all_labels=labels.list_labels(session),
        all_people=people.list_people(session),
        selected=_thread_context(session, selected) if selected else None,
    )


@router.get("/gui/threads/list", response_class=HTMLResponse)
def thread_list(
    request: Request,
    session: SessionDep,
    status: str = "open",
    q: str = "",
    label: str = "",
    owner: str = "",
    confirmed: str = "all",
    jira: str = "",
    selected: int | None = None,
) -> HTMLResponse:
    """The list pane alone; re-fetched after any thread mutation."""
    flt, values, _ = _thread_filters(status, q, label, owner, confirmed, jira)
    return render(
        request,
        "_thread_list.html",
        tree=_tree(threads.list_threads(session, flt)),
        columns=THREAD_COLUMNS,
        list_url=_list_url(values),
        selected_id=selected,
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
        "insights": insights_scan.list_insights(session, thread.id),
        "candidates": [
            t
            for t in threads.list_threads(session, threads.ThreadFilter(status="open"))
            if t.id != thread.id
        ],
        "all_labels": labels.list_labels(session),
        "all_people": people.list_people(session),
    }


THREADS_CHANGED = {"HX-Trigger": "threads-changed"}


def _thread_fragment(request: Request, session: Session, thread_id: int) -> HTMLResponse:
    """Commit a mutation and return the detail panel; the ``HX-Trigger``
    header makes the list pane refresh itself."""
    session.commit()
    response = render(request, "_thread_detail.html", **_thread_context(session, thread_id))
    response.headers.update(THREADS_CHANGED)
    return response


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


@router.post("/gui/threads/{thread_id}/delete", response_class=HTMLResponse)
def thread_delete(thread_id: int, request: Request, session: SessionDep) -> HTMLResponse:
    """htmx delete: the detail panel empties and the list refreshes."""
    _guard(lambda: threads.delete_thread(session, thread_id))
    session.commit()
    return HTMLResponse('<p class="opacity-60">Select a thread.</p>', headers=THREADS_CHANGED)


@router.post("/gui/threads/{thread_id}/mentions/{mention_id}/detach", response_class=HTMLResponse)
def thread_detach_mention(
    thread_id: int, mention_id: int, request: Request, session: SessionDep
) -> HTMLResponse:
    _guard(lambda: mentions.place_mention(session, mention_id, None))
    return _thread_fragment(request, session, thread_id)


@router.post("/gui/insights/{insight_id}/apply", response_class=HTMLResponse)
def insight_apply(
    insight_id: int, request: Request, session: SessionDep, thread_id: int
) -> HTMLResponse:
    insight = _guard(lambda: insights_scan.apply(session, insight_id))
    # a merge keeps thread_id; nothing else deletes the thread
    return _thread_fragment(request, session, insight.thread_id or thread_id)


@router.post("/gui/insights/{insight_id}/dismiss", response_class=HTMLResponse)
def insight_dismiss(
    insight_id: int, request: Request, session: SessionDep, thread_id: int
) -> HTMLResponse:
    _guard(lambda: insights_scan.dismiss(session, insight_id))
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
