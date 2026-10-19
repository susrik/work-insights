"""Per-person and per-label views: what to bring to a 1:1 or a project
meeting. Open threads split by kind and status, recently finished ones, and
the mentions still waiting for review."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from insights.core.labels import get_label
from insights.core.people import get_person
from insights.db.models import (
    OPEN_STATUSES,
    Label,
    Mention,
    MentionState,
    Person,
    Thread,
    ThreadKind,
    ThreadStatus,
    utcnow,
)


@dataclass
class Summary:
    agreements: list[Thread] = field(default_factory=list)
    goals: list[Thread] = field(default_factory=list)
    work: list[Thread] = field(default_factory=list)
    blocked: list[Thread] = field(default_factory=list)
    overdue: list[Thread] = field(default_factory=list)
    quiet: list[Thread] = field(default_factory=list)
    recently_done: list[Thread] = field(default_factory=list)
    to_review: list[Mention] = field(default_factory=list)

    @property
    def open_count(self) -> int:
        return len(self.agreements) + len(self.goals) + len(self.work)


def _summarise(
    threads: list[Thread], to_review: list[Mention], now: datetime, quiet_days: int, done_days: int
) -> Summary:
    summary = Summary(to_review=to_review)
    today = now.date()
    quiet_cutoff = now - timedelta(days=quiet_days)
    done_cutoff = now - timedelta(days=done_days)
    for t in threads:
        if t.status in OPEN_STATUSES:
            bucket = {
                ThreadKind.AGREEMENT: summary.agreements,
                ThreadKind.GOAL: summary.goals,
                ThreadKind.WORK: summary.work,
            }[t.kind]
            bucket.append(t)
            if t.status == ThreadStatus.BLOCKED:
                summary.blocked.append(t)
            if t.due_date is not None and t.due_date < today:
                summary.overdue.append(t)
            if (t.last_mentioned_at or t.created_at) < quiet_cutoff and not t.provisional:
                summary.quiet.append(t)
        elif t.status == ThreadStatus.DONE and t.completed_at and t.completed_at >= done_cutoff:
            summary.recently_done.append(t)
    for bucket in (summary.agreements, summary.goals, summary.work):
        bucket.sort(key=lambda t: (t.due_date is None, t.due_date, -t.updated_at.timestamp()))
    summary.recently_done.sort(key=lambda t: t.completed_at or now, reverse=True)
    return summary


def person_summary(
    session: Session,
    person_id: int,
    now: datetime | None = None,
    quiet_days: int = 21,
    done_days: int = 30,
) -> tuple[Person, Summary]:
    now = now or utcnow()
    person = get_person(session, person_id)
    threads = list(session.scalars(select(Thread).where(Thread.owner_id == person.id)))
    to_review = list(
        session.scalars(
            select(Mention)
            .where(Mention.owner_id == person.id, Mention.state == MentionState.PROVISIONAL)
            .order_by(Mention.last_seen_at.desc())
        )
    )
    return person, _summarise(threads, to_review, now, quiet_days, done_days)


def label_summary(
    session: Session,
    label_id: int,
    now: datetime | None = None,
    quiet_days: int = 21,
    done_days: int = 30,
) -> tuple[Label, Summary]:
    now = now or utcnow()
    label = get_label(session, label_id)
    threads = list(label.threads)
    thread_ids = [t.id for t in threads]
    to_review = (
        list(
            session.scalars(
                select(Mention)
                .where(Mention.thread_id.in_(thread_ids), Mention.state == MentionState.PROVISIONAL)
                .order_by(Mention.last_seen_at.desc())
            )
        )
        if thread_ids
        else []
    )
    return label, _summarise(threads, to_review, now, quiet_days, done_days)
