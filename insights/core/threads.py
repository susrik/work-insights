"""Threads: create, edit, tree operations, related links, Jira keys, labels.

Every mutation is recorded through ``core.changes`` so it can be undone.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from insights.core import changes
from insights.core.jira_keys import normalize_keys
from insights.core.labels import get_or_create_labels
from insights.db.models import (
    OPEN_STATUSES,
    Actor,
    JiraLink,
    Label,
    Thread,
    ThreadKind,
    ThreadLink,
    ThreadPriority,
    ThreadStatus,
    utcnow,
)


@dataclass
class ThreadFilter:
    status: str = "open"  # open | all | <ThreadStatus>
    kind: ThreadKind | None = None
    owner_id: int | None = None
    label_ids: list[int] = field(default_factory=list)
    provisional: bool | None = None
    search: str | None = None
    roots_only: bool = False


def get_thread(session: Session, thread_id: int) -> Thread:
    thread = session.get(Thread, thread_id)
    if thread is None:
        raise ValueError(f"thread {thread_id} not found")
    return thread


def list_threads(session: Session, flt: ThreadFilter | None = None) -> list[Thread]:
    flt = flt or ThreadFilter()
    stmt = (
        select(Thread)
        .options(selectinload(Thread.labels), selectinload(Thread.jira_links))
        .order_by(Thread.updated_at.desc())
    )
    if flt.status == "open":
        stmt = stmt.where(Thread.status.in_(OPEN_STATUSES))
    elif flt.status != "all":
        stmt = stmt.where(Thread.status == ThreadStatus(flt.status))
    if flt.kind:
        stmt = stmt.where(Thread.kind == flt.kind)
    if flt.owner_id is not None:
        stmt = stmt.where(Thread.owner_id == flt.owner_id)
    if flt.provisional is not None:
        stmt = stmt.where(Thread.provisional.is_(flt.provisional))
    if flt.roots_only:
        stmt = stmt.where(Thread.parent_id.is_(None))
    if flt.search:
        stmt = stmt.where(Thread.title.ilike(f"%{flt.search.strip()}%"))
    threads = list(session.scalars(stmt))
    if flt.label_ids:
        wanted = set(flt.label_ids)
        threads = [t for t in threads if wanted & {lb.id for lb in t.labels}]
    return threads


def create_thread(
    session: Session,
    title: str,
    kind: ThreadKind = ThreadKind.WORK,
    description: str | None = None,
    priority: ThreadPriority = ThreadPriority.NORMAL,
    owner_id: int | None = None,
    parent_id: int | None = None,
    due_date: date | None = None,
    labels: list[str] | None = None,
    jira_keys: list[str] | None = None,
    provisional: bool = False,
    actor: Actor = Actor.USER,
    job_run_id: int | None = None,
) -> Thread:
    title = " ".join(title.split())
    if not title:
        raise ValueError("title is required")
    parent = get_thread(session, parent_id) if parent_id is not None else None
    thread = Thread(
        title=title,
        kind=kind,
        description=description or None,
        priority=priority,
        owner_id=owner_id,
        parent=parent,
        due_date=due_date,
        provisional=provisional,
        created_by=actor,
        job_run_id=job_run_id,
    )
    session.add(thread)
    if parent:
        thread.labels = list(parent.labels)
    session.flush()
    if labels:
        for label in get_or_create_labels(session, labels):
            if label not in thread.labels:
                thread.labels.append(label)
    for key in normalize_keys(jira_keys):
        thread.jira_links.append(JiraLink(key=key))
    session.flush()
    changes.record(
        session,
        changes.ENTITY_THREAD,
        thread.id,
        changes.FIELD_CREATED,
        None,
        title,
        actor=actor,
        job_run_id=job_run_id,
    )
    return thread


class Unset(enum.Enum):
    """Sentinel so ``None`` can mean "clear this field" in ``update_thread``."""

    UNSET = "unset"


UNSET = Unset.UNSET


def update_thread(
    session: Session,
    thread_id: int,
    title: str | None = None,
    description: str | None = None,
    kind: ThreadKind | None = None,
    status: ThreadStatus | None = None,
    priority: ThreadPriority | None = None,
    owner_id: int | Unset | None = UNSET,
    due_date: date | Unset | None = UNSET,
    actor: Actor = Actor.USER,
    job_run_id: int | None = None,
) -> Thread:
    thread = get_thread(session, thread_id)
    fields: dict[str, object] = {}
    if title is not None and " ".join(title.split()):
        fields["title"] = " ".join(title.split())
    if description is not None:
        fields["description"] = description or None
    if kind is not None:
        fields["kind"] = kind
    if status is not None:
        fields["status"] = status
    if priority is not None:
        fields["priority"] = priority
    if owner_id is not UNSET:
        fields["owner_id"] = owner_id
    if due_date is not UNSET:
        fields["due_date"] = due_date
    for name, value in fields.items():
        before = getattr(thread, name)
        if before == value:
            continue
        setattr(thread, name, value)
        changes.record(
            session, changes.ENTITY_THREAD, thread.id, name, before, value, actor, job_run_id
        )
    if "status" in fields:
        thread.completed_at = utcnow() if thread.status == ThreadStatus.DONE else None
    session.flush()
    return thread


def confirm_thread(session: Session, thread_id: int) -> Thread:
    """Accept an AI-created thread and all its provisional mentions."""
    from insights.core.mentions import confirm_mentions_on_thread

    thread = get_thread(session, thread_id)
    if thread.provisional:
        thread.provisional = False
        changes.record(session, changes.ENTITY_THREAD, thread.id, "provisional", True, False)
    confirm_mentions_on_thread(session, thread.id)
    session.flush()
    return thread


def delete_thread(session: Session, thread_id: int) -> None:
    """Delete a thread. Mentions are detached (back to unplaced), children are
    re-parented to the deleted thread's parent."""
    thread = get_thread(session, thread_id)
    for mention in list(thread.mentions):
        changes.record(
            session, changes.ENTITY_MENTION, mention.id, "thread_id", mention.thread_id, None
        )
        mention.thread = None
    for child in list(thread.children):
        changes.record(
            session, changes.ENTITY_THREAD, child.id, "parent_id", child.parent_id, thread.parent_id
        )
        child.parent = thread.parent
    session.delete(thread)
    session.flush()


def descendant_ids(session: Session, thread_id: int) -> set[int]:
    ids: set[int] = set()
    frontier = [thread_id]
    while frontier:
        rows = session.scalars(select(Thread.id).where(Thread.parent_id.in_(frontier))).all()
        new = [r for r in rows if r not in ids]
        ids.update(new)
        frontier = new
    return ids


def set_parent(
    session: Session,
    thread_id: int,
    parent_id: int | None,
    actor: Actor = Actor.USER,
    job_run_id: int | None = None,
) -> Thread:
    thread = get_thread(session, thread_id)
    parent: Thread | None = None
    if parent_id is not None:
        if parent_id == thread_id:
            raise ValueError("a thread cannot be its own parent")
        if parent_id in descendant_ids(session, thread_id):
            raise ValueError("cannot move a thread under one of its descendants")
        parent = get_thread(session, parent_id)
        for label in parent.labels:
            if label not in thread.labels:
                thread.labels.append(label)
    if thread.parent_id != parent_id:
        changes.record(
            session,
            changes.ENTITY_THREAD,
            thread.id,
            "parent_id",
            thread.parent_id,
            parent_id,
            actor,
            job_run_id,
        )
        thread.parent = parent
    session.flush()
    return thread


def ancestors(thread: Thread) -> list[Thread]:
    chain: list[Thread] = []
    current = thread.parent
    while current is not None and current not in chain:
        chain.append(current)
        current = current.parent
    chain.reverse()
    return chain


def merge_threads(
    session: Session,
    into_id: int,
    from_id: int,
    actor: Actor = Actor.USER,
    job_run_id: int | None = None,
) -> Thread:
    """Move everything from ``from_id`` onto ``into_id`` and delete it."""
    if into_id == from_id:
        raise ValueError("cannot merge a thread into itself")
    into = get_thread(session, into_id)
    loser = get_thread(session, from_id)
    if into_id in descendant_ids(session, from_id):
        raise ValueError("cannot merge a thread into one of its descendants")
    for mention in list(loser.mentions):
        changes.record(
            session,
            changes.ENTITY_MENTION,
            mention.id,
            "thread_id",
            from_id,
            into_id,
            actor,
            job_run_id,
        )
        mention.thread = into
    for child in list(loser.children):
        changes.record(
            session,
            changes.ENTITY_THREAD,
            child.id,
            "parent_id",
            from_id,
            into_id,
            actor,
            job_run_id,
        )
        child.parent = into
    for label in loser.labels:
        if label not in into.labels:
            into.labels.append(label)
    existing_keys = set(into.jira_keys)
    for link in list(loser.jira_links):
        if link.key not in existing_keys:
            into.jira_links.append(JiraLink(key=link.key))
    for other in loser.related:
        if other.id != into_id:
            link_threads(session, into_id, other.id)
    if loser.description and not into.description:
        into.description = loser.description
    session.delete(loser)
    session.flush()
    return into


def link_threads(session: Session, a_id: int, b_id: int) -> ThreadLink | None:
    if a_id == b_id:
        raise ValueError("cannot link a thread to itself")
    get_thread(session, a_id)
    get_thread(session, b_id)
    lo, hi = sorted((a_id, b_id))
    existing = session.scalar(
        select(ThreadLink).where(ThreadLink.source_id == lo, ThreadLink.target_id == hi)
    )
    if existing:
        return existing
    link = ThreadLink(source_id=lo, target_id=hi)
    session.add(link)
    session.flush()
    return link


def unlink_threads(session: Session, a_id: int, b_id: int) -> None:
    lo, hi = sorted((a_id, b_id))
    link = session.scalar(
        select(ThreadLink).where(ThreadLink.source_id == lo, ThreadLink.target_id == hi)
    )
    if link:
        session.delete(link)
        session.flush()


def set_labels(session: Session, thread_id: int, names: list[str]) -> Thread:
    thread = get_thread(session, thread_id)
    thread.labels = get_or_create_labels(session, names)
    session.flush()
    return thread


def add_labels(session: Session, thread_id: int, labels: list[Label]) -> Thread:
    thread = get_thread(session, thread_id)
    for label in labels:
        if label not in thread.labels:
            thread.labels.append(label)
    session.flush()
    return thread


def add_jira_keys(session: Session, thread_id: int, keys: list[str]) -> Thread:
    thread = get_thread(session, thread_id)
    existing = set(thread.jira_keys)
    for key in normalize_keys(keys):
        if key not in existing:
            thread.jira_links.append(JiraLink(key=key))
            existing.add(key)
    session.flush()
    return thread


def remove_jira_key(session: Session, thread_id: int, key: str) -> Thread:
    thread = get_thread(session, thread_id)
    for link in list(thread.jira_links):
        if link.key == key.upper():
            thread.jira_links.remove(link)
    session.flush()
    return thread


def threads_for_jira_keys(session: Session, keys: list[str]) -> list[Thread]:
    keys = normalize_keys(keys)
    if not keys:
        return []
    links = session.scalars(select(JiraLink).where(JiraLink.key.in_(keys)))
    seen: list[Thread] = []
    for link in links:
        if link.thread not in seen and link.thread.is_open:
            seen.append(link.thread)
    return seen
