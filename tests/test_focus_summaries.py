from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from insights.core import focus, labels, mentions, people, summaries, threads
from insights.db.models import Actor, ThreadKind, ThreadPriority, ThreadStatus

NOW = datetime(2026, 10, 19, 9, 0)


def build(session: Session) -> dict[str, int]:
    me = people.create_person(session, "Alex", is_self=True)
    robert = people.create_person(session, "Robert")
    lb = labels.create_label(session, "dashboard")
    ids: dict[str, int] = {"me": me.id, "robert": robert.id, "label": lb.id}

    overdue = threads.create_thread(
        session, "Overdue mine", owner_id=me.id, due_date=date(2026, 10, 1), labels=["dashboard"]
    )
    soon = threads.create_thread(
        session,
        "Due soon",
        owner_id=robert.id,
        due_date=date(2026, 10, 21),
        priority=ThreadPriority.HIGH,
    )
    blocked = threads.create_thread(
        session, "Blocked agreement", kind=ThreadKind.AGREEMENT, owner_id=robert.id
    )
    threads.update_thread(session, blocked.id, status=ThreadStatus.BLOCKED)
    quiet = threads.create_thread(
        session, "Quiet goal", kind=ThreadKind.GOAL, owner_id=robert.id, labels=["dashboard"]
    )
    fresh = threads.create_thread(
        session, "Fresh provisional", owner_id=robert.id, provisional=True, actor=Actor.AI
    )
    done = threads.create_thread(
        session, "Done last week", owner_id=robert.id, labels=["dashboard"]
    )
    threads.update_thread(session, done.id, status=ThreadStatus.DONE)
    old_done = threads.create_thread(session, "Done long ago", owner_id=robert.id)
    threads.update_thread(session, old_done.id, status=ThreadStatus.DONE)
    m, _ = mentions.upsert_mention(
        session, mentions.MentionInput(path="n.md", summary="Robert thing", owner_id=robert.id)
    )
    mentions.place_mention(session, m.id, quiet.id, actor=Actor.AI)
    session.flush()

    long_ago = NOW - timedelta(days=60)
    for t in (overdue, soon, blocked, quiet, fresh, done, old_done):
        t.created_at = NOW - timedelta(days=2)
        t.updated_at = NOW - timedelta(days=1)
    quiet.created_at = long_ago
    quiet.updated_at = NOW - timedelta(days=40)
    quiet.last_mentioned_at = long_ago
    soon.last_mentioned_at = NOW - timedelta(days=1)
    soon.status = ThreadStatus.IN_PROGRESS
    done.completed_at = NOW - timedelta(days=5)
    old_done.completed_at = NOW - timedelta(days=90)
    session.flush()
    # onupdate=utcnow would overwrite updated_at on flush; set it with SQL
    from sqlalchemy import update

    from insights.db.models import Thread

    session.execute(
        update(Thread).where(Thread.id == quiet.id).values(updated_at=NOW - timedelta(days=40))
    )
    session.expire_all()
    session.commit()
    ids.update(
        overdue=overdue.id,
        soon=soon.id,
        blocked=blocked.id,
        quiet=quiet.id,
        fresh=fresh.id,
        done=done.id,
    )
    return ids


def test_rank_threads(session: Session) -> None:
    ids = build(session)
    ranked = focus.rank_threads(session, now=NOW)
    titles = [r.thread.title for r in ranked]
    assert titles[0] == "Overdue mine"
    assert titles[1] == "Due soon"
    assert titles[-1] == "Blocked agreement"
    reasons = {r.thread.id: r.reasons for r in ranked}
    assert "overdue by 18d" in reasons[ids["overdue"]] and "mine" in reasons[ids["overdue"]]
    assert "due in 2d" in reasons[ids["soon"]] and "in progress" in reasons[ids["soon"]]
    assert "in recent notes" in reasons[ids["soon"]]
    assert "blocked" in reasons[ids["blocked"]]
    assert "untouched 40d" in reasons[ids["quiet"]] and "open 60d" in reasons[ids["quiet"]]
    assert "unconfirmed" in reasons[ids["fresh"]]
    assert len(focus.rank_threads(session, now=NOW, limit=2)) == 2
    # stable jitter: same day, same order
    assert [r.thread.id for r in focus.rank_threads(session, now=NOW)] == [
        r.thread.id for r in ranked
    ]


def test_due_in_two_weeks_reason(session: Session) -> None:
    t = threads.create_thread(session, "Soonish", due_date=date(2026, 10, 30))
    t.created_at = t.updated_at = NOW
    assert "due in 11d" in focus.score_thread(t, NOW).reasons


def test_attention(session: Session) -> None:
    ids = build(session)
    att = focus.attention(session, now=NOW)
    assert [t.id for t in att.overdue] == [ids["overdue"]]
    assert [t.id for t in att.blocked] == [ids["blocked"]]
    assert [t.id for t in att.quiet] == [ids["quiet"]]


def test_person_summary(session: Session) -> None:
    ids = build(session)
    person, s = summaries.person_summary(session, ids["robert"], now=NOW)
    assert person.name == "Robert"
    assert [t.title for t in s.agreements] == ["Blocked agreement"]
    assert [t.title for t in s.goals] == ["Quiet goal"]
    assert [t.title for t in s.work] == ["Due soon", "Fresh provisional"]
    assert s.open_count == 4
    assert [t.title for t in s.blocked] == ["Blocked agreement"]
    assert [t.title for t in s.quiet] == ["Quiet goal"]
    assert s.overdue == []
    assert [t.title for t in s.recently_done] == ["Done last week"]
    assert [m.summary for m in s.to_review] == ["Robert thing"]

    _, mine = summaries.person_summary(session, ids["me"], now=NOW)
    assert [t.title for t in mine.overdue] == ["Overdue mine"]
    with pytest.raises(ValueError):
        summaries.person_summary(session, 999)


def test_label_summary(session: Session) -> None:
    ids = build(session)
    label, s = summaries.label_summary(session, ids["label"], now=NOW)
    assert label.name == "dashboard"
    assert {t.title for t in s.work} == {"Overdue mine"}
    assert [t.title for t in s.goals] == ["Quiet goal"]
    assert [t.title for t in s.recently_done] == ["Done last week"]
    assert [m.summary for m in s.to_review] == ["Robert thing"]
    empty = labels.create_label(session, "empty")
    _, s2 = summaries.label_summary(session, empty.id, now=NOW)
    assert s2.open_count == 0 and s2.to_review == []
