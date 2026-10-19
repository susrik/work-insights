"""Deterministic ranking of open threads for the Today page.

Computed on request, never stored. Signals, in decreasing weight: priority,
overdue / due soon, blocked pushed down, mine beats delegated, untouched for
a while, open for long, recently mentioned in notes, and a per-day jitter so
equal scores rotate instead of sticking.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from insights.core.people import get_self
from insights.core.threads import ThreadFilter, list_threads
from insights.db.models import OPEN_STATUSES, Thread, ThreadPriority, ThreadStatus, utcnow

_PRIORITY_SCORE = {
    ThreadPriority.CRITICAL: 100,
    ThreadPriority.HIGH: 50,
    ThreadPriority.NORMAL: 20,
    ThreadPriority.LOW: 5,
}


@dataclass
class Ranked:
    thread: Thread
    score: float
    reasons: list[str]


def score_thread(
    thread: Thread,
    now: datetime,
    self_id: int | None = None,
    jitter: float = 0.0,
) -> Ranked:
    score = float(_PRIORITY_SCORE[thread.priority])
    reasons = [f"priority {thread.priority.value}"]

    if thread.due_date is not None:
        days_left = (thread.due_date - now.date()).days
        if days_left < 0:
            score += 80
            reasons.append(f"overdue by {-days_left}d")
        elif days_left <= 3:
            score += 40
            reasons.append(f"due in {days_left}d")
        elif days_left <= 14:
            score += 10
            reasons.append(f"due in {days_left}d")

    if thread.status == ThreadStatus.BLOCKED:
        score -= 30
        reasons.append("blocked")
    elif thread.status == ThreadStatus.IN_PROGRESS:
        score += 10
        reasons.append("in progress")

    if self_id is not None and thread.owner_id == self_id:
        score += 15
        reasons.append("mine")

    stale_days = (now - thread.updated_at).days
    if stale_days >= 7:
        score += min(stale_days, 30)
        reasons.append(f"untouched {stale_days}d")

    age_days = (now - thread.created_at).days
    if age_days >= 30:
        score += min(age_days // 30 * 5, 20)
        reasons.append(f"open {age_days}d")

    if thread.last_mentioned_at is not None and (now - thread.last_mentioned_at).days <= 7:
        score += 10
        reasons.append("in recent notes")

    if thread.provisional:
        score -= 10
        reasons.append("unconfirmed")

    return Ranked(thread=thread, score=score + jitter, reasons=reasons)


def rank_threads(session: Session, now: datetime | None = None, limit: int = 10) -> list[Ranked]:
    now = now or utcnow()
    me = get_self(session)
    rng = random.Random(now.date().isoformat())
    ranked = [
        score_thread(t, now, self_id=me.id if me else None, jitter=rng.random())
        for t in list_threads(session, ThreadFilter(status="open"))
    ]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:limit]


@dataclass
class Attention:
    """Threads that need a decision rather than work: overdue, blocked,
    or not mentioned for a while (slippage)."""

    overdue: list[Thread]
    blocked: list[Thread]
    quiet: list[Thread]


def attention(session: Session, now: datetime | None = None, quiet_days: int = 21) -> Attention:
    now = now or utcnow()
    open_threads = session.scalars(
        select(Thread).where(Thread.status.in_(OPEN_STATUSES)).order_by(Thread.due_date)
    ).all()
    today = now.date()
    cutoff = now - timedelta(days=quiet_days)
    return Attention(
        overdue=[t for t in open_threads if t.due_date is not None and t.due_date < today],
        blocked=[t for t in open_threads if t.status == ThreadStatus.BLOCKED],
        quiet=[
            t
            for t in open_threads
            if not t.provisional
            and (t.last_mentioned_at or t.created_at) < cutoff
            and t.status != ThreadStatus.BLOCKED
        ],
    )
