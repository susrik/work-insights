from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from insights.core import changes, mentions, people, threads
from insights.core.mentions import MentionFilter, MentionInput
from insights.db.models import Actor, MentionKind, MentionState, ThreadStatus


def test_upsert_dedupes(session: Session) -> None:
    first, created = mentions.upsert_mention(
        session, MentionInput(path="a.md", summary="Fix the  Checker!", jira_keys=["ab-1"])
    )
    assert created
    again, created = mentions.upsert_mention(
        session,
        MentionInput(path="a.md", summary="fix the checker", jira_keys=["AB-2"], excerpt="new"),
    )
    assert not created
    assert again is first
    assert first.jira_keys == ["AB-1", "AB-2"]
    assert first.excerpt == "new"
    other, created = mentions.upsert_mention(
        session, MentionInput(path="b.md", summary="fix the checker")
    )
    assert created and other is not first
    assert mentions.counts_by_state(session) == {"provisional": 2, "confirmed": 0, "set_aside": 0}


def test_place_confirm_set_aside(session: Session) -> None:
    t = threads.create_thread(session, "T")
    m, _ = mentions.upsert_mention(session, MentionInput(path="a.md", summary="s"))

    mentions.place_mention(session, m.id, t.id, actor=Actor.AI, job_run_id=None, reason="match")
    assert m.state == MentionState.PROVISIONAL
    assert m.placed_by == Actor.AI
    assert m.placement_reason == "match"
    assert t.last_mentioned_at is not None

    mentions.confirm_mention(session, m.id)
    assert m.state == MentionState.CONFIRMED
    assert m.reviewed_at is not None

    mentions.place_mention(session, m.id, None)
    assert m.thread_id is None
    assert m.state == MentionState.CONFIRMED

    mentions.place_mention(session, m.id, t.id, actor=Actor.AI, confirmed=True)
    assert m.state == MentionState.CONFIRMED

    mentions.set_aside(session, m.id)
    assert m.thread_id is None
    assert m.state == MentionState.SET_ASIDE
    mentions.set_aside(session, m.id)  # idempotent

    with pytest.raises(ValueError):
        mentions.place_mention(session, m.id, 999)
    with pytest.raises(ValueError):
        mentions.get_mention(session, 999)


def test_confirm_mentions_on_thread(session: Session) -> None:
    t = threads.create_thread(session, "T")
    ids = []
    for i in range(3):
        m, _ = mentions.upsert_mention(session, MentionInput(path="a.md", summary=f"s{i}"))
        mentions.place_mention(session, m.id, t.id, actor=Actor.AI)
        ids.append(m.id)
    mentions.confirm_mention(session, ids[0])
    assert mentions.confirm_mentions_on_thread(session, t.id) == 2
    assert all(mentions.get_mention(session, i).state == MentionState.CONFIRMED for i in ids)


def test_list_mentions_filters(session: Session) -> None:
    bob = people.create_person(session, "Robert")
    t = threads.create_thread(session, "T")
    a, _ = mentions.upsert_mention(
        session,
        MentionInput(path="x/a.md", summary="alpha", owner_id=bob.id, kind=MentionKind.RISK),
    )
    b, _ = mentions.upsert_mention(session, MentionInput(path="y/b.md", summary="beta"))
    c, _ = mentions.upsert_mention(session, MentionInput(path="y/c.md", summary="gamma"))
    mentions.place_mention(session, a.id, t.id, actor=Actor.AI, job_run_id=None)
    mentions.confirm_mention(session, b.id)
    mentions.set_aside(session, c.id)

    ids = lambda rows: sorted(m.id for m in rows)  # noqa: E731
    assert ids(mentions.list_mentions(session)) == [a.id]
    assert ids(mentions.list_mentions(session, MentionFilter(state="all"))) == [a.id, b.id, c.id]
    assert ids(mentions.list_mentions(session, MentionFilter(state="unplaced"))) == [b.id]
    assert ids(mentions.list_mentions(session, MentionFilter(state="all", thread_id=t.id))) == [
        a.id
    ]
    assert ids(mentions.list_mentions(session, MentionFilter(state="all", owner_id=bob.id))) == [
        a.id
    ]
    assert ids(mentions.list_mentions(session, MentionFilter(state="all", path_prefix="y/"))) == [
        b.id,
        c.id,
    ]
    assert ids(mentions.list_mentions(session, MentionFilter(state="all", search="GAM"))) == [c.id]
    assert ids(mentions.list_mentions(session, MentionFilter(state="all", job_run_id=1))) == []
    with pytest.raises(ValueError):
        mentions.list_mentions(session, MentionFilter(state="bogus"))


def test_undo_field_changes(session: Session) -> None:
    t = threads.create_thread(session, "Original", due_date=date(2026, 1, 1))
    threads.update_thread(
        session, t.id, title="Renamed", status=ThreadStatus.DONE, due_date=date(2026, 2, 2)
    )
    recent = changes.list_changes(session, entity="thread", entity_id=t.id)
    by_field = {c.field: c for c in recent}
    assert by_field["title"].before == "Original"
    assert by_field["status"].after == "done"
    assert by_field["due_date"].before == "2026-01-01"

    changes.undo(session, by_field["title"].id)
    changes.undo(session, by_field["status"].id)
    changes.undo(session, by_field["due_date"].id)
    assert t.title == "Original"
    assert t.status == ThreadStatus.OPEN
    assert t.completed_at is None
    assert t.due_date == date(2026, 1, 1)
    # second undo is a no-op
    assert changes.undo(session, by_field["title"].id).reverted_at is not None
    assert t.title == "Original"
    with pytest.raises(ValueError):
        changes.undo(session, 999)


def test_undo_mention_placement_and_datetime(session: Session) -> None:
    t = threads.create_thread(session, "T")
    m, _ = mentions.upsert_mention(session, MentionInput(path="a.md", summary="s"))
    mentions.place_mention(session, m.id, t.id)
    placement = changes.list_changes(session, entity="mention", entity_id=m.id)
    thread_change = next(c for c in placement if c.field == "thread_id")
    changes.undo(session, thread_change.id)
    assert m.thread_id is None
    # datetime round trip through the JSON column
    c = changes.record(session, "thread", t.id, "completed_at", t.created_at, None)
    changes.undo(session, c.id)
    assert t.completed_at == t.created_at
    # unknown field is rejected
    bad = changes.record(session, "thread", t.id, "nope", 1, 2)
    with pytest.raises(ValueError):
        changes.undo(session, bad.id)


def test_revert_run_drops_created_threads(session: Session) -> None:
    run_id = 1
    from insights.db.models import JobRun

    session.add(JobRun(id=run_id, job_name="intake"))
    session.flush()
    parent = threads.create_thread(session, "Parent")
    t = threads.create_thread(
        session,
        "AI thread",
        provisional=True,
        actor=Actor.AI,
        job_run_id=run_id,
        parent_id=parent.id,
    )
    kid = threads.create_thread(session, "Kid", parent_id=t.id)
    m, _ = mentions.upsert_mention(session, MentionInput(path="a.md", summary="s"))
    mentions.place_mention(session, m.id, t.id, actor=Actor.AI, job_run_id=run_id)
    threads.update_thread(
        session, parent.id, title="Parent renamed", actor=Actor.AI, job_run_id=run_id
    )

    # created thread + mention placement + rename (state stayed provisional)
    assert changes.revert_run(session, run_id) == 3
    session.expire_all()
    with pytest.raises(ValueError):
        threads.get_thread(session, t.id)
    assert threads.get_thread(session, kid.id).parent_id == parent.id
    assert mentions.get_mention(session, m.id).thread_id is None
    assert threads.get_thread(session, parent.id).title == "Parent"
    assert changes.revert_run(session, run_id) == 0
    # undo of a change whose entity is gone is harmless
    gone = changes.record(session, "thread", 12345, "title", "a", "b")
    changes.undo(session, gone.id)
    gone_m = changes.record(session, "mention", 12345, "state", "a", "b")
    changes.undo(session, gone_m.id)
