from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from insights.core import changes, labels, mentions, people, threads
from insights.core.jira_keys import find_jira_keys, normalize_keys, project_of
from insights.core.threads import UNSET, ThreadFilter
from insights.db.models import Actor, ThreadKind, ThreadPriority, ThreadStatus


def test_jira_key_helpers() -> None:
    assert find_jira_keys("see DBOARD3-12 and chrn-1 and DBOARD3-12, AB-3") == [
        "DBOARD3-12",
        "AB-3",
    ]
    assert find_jira_keys(None) == []
    assert normalize_keys([" dboard3-1 ", "bad", "DBOARD3-1"]) == ["DBOARD3-1"]
    assert project_of("DBOARD3-12") == "DBOARD3"


def test_create_update_thread(session: Session) -> None:
    me = people.create_person(session, "Alex", is_self=True)
    root = threads.create_thread(
        session, "  Dashboard   alarms ", labels=["Dashboard"], jira_keys=["dboard3-1", "x"]
    )
    assert root.title == "Dashboard alarms"
    assert [lb.name for lb in root.labels] == ["dashboard"]
    assert root.jira_keys == ["DBOARD3-1"]
    assert root.created_by == Actor.USER
    child = threads.create_thread(
        session,
        "Fix availability checker",
        parent_id=root.id,
        kind=ThreadKind.AGREEMENT,
        owner_id=me.id,
        labels=["bugs"],
        due_date=date(2026, 11, 1),
    )
    assert child.parent is root
    assert sorted(lb.name for lb in child.labels) == ["bugs", "dashboard"]
    assert threads.ancestors(child) == [root]
    assert threads.ancestors(root) == []

    with pytest.raises(ValueError):
        threads.create_thread(session, "   ")
    with pytest.raises(ValueError):
        threads.create_thread(session, "x", parent_id=999)

    threads.update_thread(
        session,
        child.id,
        title="Fix the checker",
        description="details",
        status=ThreadStatus.DONE,
        priority=ThreadPriority.HIGH,
        kind=ThreadKind.WORK,
        owner_id=None,
        due_date=None,
    )
    assert child.title == "Fix the checker"
    assert child.completed_at is not None
    assert child.owner_id is None
    assert child.due_date is None
    assert child.kind == ThreadKind.WORK
    threads.update_thread(session, child.id, status=ThreadStatus.OPEN, description="")
    assert child.completed_at is None
    assert child.description is None
    # untouched fields do not create change records
    before = len(changes.list_changes(session))
    threads.update_thread(session, child.id, title="   ", owner_id=UNSET, due_date=UNSET)
    assert len(changes.list_changes(session)) == before


def test_list_threads_filters(session: Session) -> None:
    me = people.create_person(session, "Alex", is_self=True)
    a = threads.create_thread(session, "Alpha work", labels=["l1"], owner_id=me.id)
    b = threads.create_thread(session, "Beta goal", kind=ThreadKind.GOAL, parent_id=a.id)
    c = threads.create_thread(session, "Gamma done", provisional=True, actor=Actor.AI)
    threads.update_thread(session, c.id, status=ThreadStatus.DONE)
    l1 = labels.find_label(session, "l1")
    assert l1 is not None

    ids = lambda rows: sorted(t.id for t in rows)  # noqa: E731
    assert ids(threads.list_threads(session)) == [a.id, b.id]
    assert ids(threads.list_threads(session, ThreadFilter(status="all"))) == [a.id, b.id, c.id]
    assert ids(threads.list_threads(session, ThreadFilter(status="done"))) == [c.id]
    assert ids(threads.list_threads(session, ThreadFilter(kind=ThreadKind.GOAL))) == [b.id]
    assert ids(threads.list_threads(session, ThreadFilter(owner_id=me.id))) == [a.id]
    assert ids(threads.list_threads(session, ThreadFilter(label_ids=[l1.id]))) == [a.id, b.id]
    assert ids(threads.list_threads(session, ThreadFilter(roots_only=True))) == [a.id]
    assert ids(threads.list_threads(session, ThreadFilter(search="beta"))) == [b.id]
    assert ids(threads.list_threads(session, ThreadFilter(status="all", provisional=True))) == [
        c.id
    ]
    with pytest.raises(ValueError):
        threads.list_threads(session, ThreadFilter(status="bogus"))


def test_tree_operations(session: Session) -> None:
    a = threads.create_thread(session, "A", labels=["pa"])
    b = threads.create_thread(session, "B", parent_id=a.id)
    c = threads.create_thread(session, "C", parent_id=b.id)
    d = threads.create_thread(session, "D")
    assert threads.descendant_ids(session, a.id) == {b.id, c.id}

    with pytest.raises(ValueError):
        threads.set_parent(session, a.id, a.id)
    with pytest.raises(ValueError):
        threads.set_parent(session, a.id, c.id)
    threads.set_parent(session, d.id, a.id)
    assert d.parent is a
    assert [lb.name for lb in d.labels] == ["pa"]
    threads.set_parent(session, d.id, None)
    assert d.parent is None
    threads.set_parent(session, d.id, None)  # no-op

    threads.delete_thread(session, b.id)
    session.expire_all()
    assert threads.get_thread(session, c.id).parent_id == a.id
    with pytest.raises(ValueError):
        threads.get_thread(session, b.id)


def test_merge_and_links(session: Session) -> None:
    into = threads.create_thread(session, "Keep", labels=["x"], jira_keys=["AB-1"])
    loser = threads.create_thread(
        session, "Lose", labels=["y"], jira_keys=["AB-1", "AB-2"], description="desc"
    )
    child = threads.create_thread(session, "Child", parent_id=loser.id)
    other = threads.create_thread(session, "Other")
    threads.link_threads(session, loser.id, other.id)
    threads.link_threads(session, loser.id, into.id)
    assert threads.link_threads(session, other.id, loser.id) is not None  # idempotent
    m, _ = mentions.upsert_mention(session, mentions.MentionInput(path="n.md", summary="do x"))
    mentions.place_mention(session, m.id, loser.id)

    with pytest.raises(ValueError):
        threads.merge_threads(session, into.id, into.id)
    with pytest.raises(ValueError):
        threads.merge_threads(session, child.id, loser.id)
    with pytest.raises(ValueError):
        threads.link_threads(session, into.id, into.id)

    threads.merge_threads(session, into.id, loser.id)
    session.expire_all()
    into = threads.get_thread(session, into.id)
    assert sorted(lb.name for lb in into.labels) == ["x", "y"]
    assert sorted(into.jira_keys) == ["AB-1", "AB-2"]
    assert into.description == "desc"
    assert threads.get_thread(session, child.id).parent_id == into.id
    assert mentions.get_mention(session, m.id).thread_id == into.id
    assert [t.id for t in into.related] == [other.id]

    threads.unlink_threads(session, other.id, into.id)
    threads.unlink_threads(session, other.id, into.id)
    session.expire_all()
    assert threads.get_thread(session, into.id).related == []


def test_labels_and_jira_on_thread(session: Session) -> None:
    t = threads.create_thread(session, "T")
    threads.set_labels(session, t.id, ["One", "two"])
    assert sorted(lb.name for lb in t.labels) == ["one", "two"]
    extra = labels.create_label(session, "three")
    threads.add_labels(session, t.id, [extra, extra])
    assert len(t.labels) == 3

    threads.add_jira_keys(session, t.id, ["ab-1", "AB-1", "AB-2"])
    assert t.jira_keys == ["AB-1", "AB-2"]
    assert t.jira_links[0].project == "AB"
    threads.remove_jira_key(session, t.id, "ab-1")
    assert t.jira_keys == ["AB-2"]
    assert threads.threads_for_jira_keys(session, ["AB-2", "ZZ-9"]) == [t]
    assert threads.threads_for_jira_keys(session, []) == []
    threads.update_thread(session, t.id, status=ThreadStatus.DROPPED)
    assert threads.threads_for_jira_keys(session, ["AB-2"]) == []


def test_confirm_thread(session: Session) -> None:
    t = threads.create_thread(session, "AI made", provisional=True, actor=Actor.AI, job_run_id=None)
    m, _ = mentions.upsert_mention(session, mentions.MentionInput(path="n.md", summary="s"))
    mentions.place_mention(session, m.id, t.id, actor=Actor.AI)
    assert m.state.value == "provisional"
    threads.confirm_thread(session, t.id)
    assert t.provisional is False
    assert m.state.value == "confirmed"
    threads.confirm_thread(session, t.id)  # idempotent
