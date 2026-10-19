"""Change log: every mutation to a thread or mention is recorded so it can be
shown and undone. Services call ``record``; ``undo`` / ``revert_run`` replay
the ``before`` values.

Undo covers: field changes on threads and mentions (including ``thread_id``
placement and ``parent_id``), thread creation (reverted by dropping the thread
and detaching its mentions), and thread deletion is never performed by AI.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Enum, select
from sqlalchemy.orm import Session

from insights.db.models import Actor, Change, Mention, Thread, ThreadStatus, utcnow

ENTITY_THREAD = "thread"
ENTITY_MENTION = "mention"
FIELD_CREATED = "created"


def record(
    session: Session,
    entity: str,
    entity_id: int,
    field: str,
    before: Any,
    after: Any,
    actor: Actor = Actor.USER,
    job_run_id: int | None = None,
) -> Change:
    change = Change(
        actor=actor,
        job_run_id=job_run_id,
        entity=entity,
        entity_id=entity_id,
        field=field,
        before=_plain(before),
        after=_plain(after),
    )
    session.add(change)
    session.flush()
    return change


def _plain(value: Any) -> Any:
    """JSON-safe representation: enums by value, dates in ISO format."""
    if value is None or isinstance(value, bool | int | float | str | list | dict):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, date | datetime):
        return value.isoformat()
    return str(value)


def list_changes(
    session: Session,
    entity: str | None = None,
    entity_id: int | None = None,
    job_run_id: int | None = None,
    limit: int = 100,
) -> list[Change]:
    stmt = select(Change).order_by(Change.id.desc()).limit(limit)
    if entity:
        stmt = stmt.where(Change.entity == entity)
    if entity_id is not None:
        stmt = stmt.where(Change.entity_id == entity_id)
    if job_run_id is not None:
        stmt = stmt.where(Change.job_run_id == job_run_id)
    return list(session.scalars(stmt))


def undo(session: Session, change_id: int) -> Change:
    change = session.get(Change, change_id)
    if change is None:
        raise ValueError(f"change {change_id} not found")
    if change.reverted_at is not None:
        return change
    _revert(session, change)
    change.reverted_at = utcnow()
    session.flush()
    return change


def revert_run(session: Session, job_run_id: int) -> int:
    """Undo every change made by a job run, newest first. Returns the count."""
    changes = list(
        session.scalars(
            select(Change)
            .where(Change.job_run_id == job_run_id, Change.reverted_at.is_(None))
            .order_by(Change.id.desc())
        )
    )
    now = utcnow()
    for change in changes:
        _revert(session, change)
        change.reverted_at = now
    session.flush()
    return len(changes)


def _revert(session: Session, change: Change) -> None:
    if change.entity == ENTITY_THREAD:
        thread = session.get(Thread, change.entity_id)
        if thread is None:
            return
        if change.field == FIELD_CREATED:
            _drop_created_thread(session, thread)
        else:
            _set(session, thread, change.field, change.before)
    elif change.entity == ENTITY_MENTION:
        mention = session.get(Mention, change.entity_id)
        if mention is None:
            return
        _set(session, mention, change.field, change.before)


def _drop_created_thread(session: Session, thread: Thread) -> None:
    parent = thread.parent
    for mention in list(thread.mentions):
        mention.thread = None
    for child in list(thread.children):
        child.parent = parent
    session.delete(thread)
    session.flush()


def _set(session: Session, obj: Thread | Mention, field: str, value: Any) -> None:
    column = type(obj).__table__.columns.get(field)
    if column is None:
        raise ValueError(f"cannot undo field {field!r} on {type(obj).__name__}")
    value = _coerce(column.type, value)
    # Parent/thread references go through the relationship so loaded
    # collections stay in sync with the foreign key.
    if isinstance(obj, Thread) and field == "parent_id":
        obj.parent = session.get(Thread, value) if value is not None else None
    elif isinstance(obj, Mention) and field == "thread_id":
        obj.thread = session.get(Thread, value) if value is not None else None
        if value is None:
            obj.placed_by = None
            obj.placement_reason = None
    else:
        setattr(obj, field, value)
    if isinstance(obj, Thread) and field == "status":
        obj.completed_at = utcnow() if obj.status == ThreadStatus.DONE else None
    session.flush()


def _coerce(column_type: Any, value: Any) -> Any:
    """Turn the JSON-stored ``before`` value back into the column's Python type."""
    if value is None:
        return None
    if isinstance(column_type, Enum) and column_type.enum_class is not None:
        return column_type.enum_class(value)
    if isinstance(column_type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column_type, Date):
        return date.fromisoformat(value)
    return value
