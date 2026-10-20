from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from insights.core import labels, mentions, people, threads
from insights.db.models import Actor, Label, Person, Thread, ThreadStatus
from insights.db.session import SessionFactory


def seed(session: Session) -> dict[str, int]:
    me = people.create_person(session, "Alex", is_self=True)
    robert = people.create_person(session, "Robert", notes_dir="team/people/robert")
    lb = labels.create_label(session, "dashboard", jira_projects=["DBOARD3"])
    root = threads.create_thread(
        session,
        "Root work",
        owner_id=me.id,
        labels=["dashboard"],
        jira_keys=["DBOARD3-1"],
        due_date=date(2026, 1, 1),
    )
    child = threads.create_thread(session, "Child work", parent_id=root.id, owner_id=robert.id)
    other = threads.create_thread(session, "Other agreement", kind="agreement", owner_id=robert.id)  # type: ignore[arg-type]
    prov = threads.create_thread(session, "AI thread", provisional=True, actor=Actor.AI)
    m, _ = mentions.upsert_mention(
        session,
        mentions.MentionInput(path="team/people/robert/x.md", summary="Do x", owner_id=robert.id),
    )
    mentions.place_mention(session, m.id, child.id, actor=Actor.AI, reason="r")
    session.commit()
    return {
        "me": me.id,
        "robert": robert.id,
        "label": lb.id,
        "root": root.id,
        "child": child.id,
        "other": other.id,
        "prov": prov.id,
        "mention": m.id,
    }


def test_today_page(client: TestClient, session_factory: SessionFactory) -> None:
    with session_factory() as session:
        seed(session)
    text = client.get("/").text
    assert "Focus" in text and "Root work" in text
    assert "overdue by" in text
    assert "Needs a decision" in text
    assert "1 mentions" in text


def test_threads_page_and_filters(client: TestClient, session_factory: SessionFactory) -> None:
    with session_factory() as session:
        ids = seed(session)
    text = client.get("/threads").text
    assert "Root work" in text and "Child work" in text and "Select a thread" in text
    assert "padding-left: 1.25rem" in text  # child is indented
    assert "alert-warning" not in text

    def tree_of(url: str) -> str:
        page = client.get(url).text
        return page[page.index('id="thread-tree"') :]

    assert "Root work" not in tree_of("/threads?q=child")
    assert "Other agreement" not in tree_of(f"/threads?label={ids['label']}")
    assert "Child work" in tree_of(f"/threads?label={ids['label']}")  # inherited from parent
    assert "Root work" not in tree_of(f"/threads?owner={ids['robert']}")
    prov = tree_of("/threads?confirmed=provisional")
    assert "AI thread" in prov and "Root work" not in prov
    conf = tree_of("/threads?confirmed=confirmed")
    assert "AI thread" not in conf and "Root work" in conf
    jira = tree_of("/threads?jira=dboard3")
    assert "Root work" in jira and "Child work" not in jira

    # the form submits "" for "any"; that must never be an error
    r = client.get("/threads", params={"q": "", "status": "open", "label": "", "owner": ""})
    assert r.status_code == 200 and "alert-warning" not in r.text and "Root work" in r.text
    # a hand-edited URL is tolerated with a warning
    r = client.get("/threads?label=abc&owner=x&status=bogus&confirmed=maybe")
    assert r.status_code == 200 and "Root work" in r.text
    assert r.text.count("Ignored ") == 4

    text = client.get(f"/threads?selected={ids['child']}").text
    assert 'name="title" value="Child work"' in text and "Do x" in text
    assert f'data-thread-id="{ids["child"]}"' in text and "thread-row-selected" in text
    assert client.get("/threads?selected=999").status_code == 404

    r = client.get(f"/gui/threads/list?q=child&selected={ids['child']}")
    assert r.status_code == 200 and "Child work" in r.text and "Root work" not in r.text
    assert "thread-row-selected" in r.text and "selected=" in r.text
    assert "<html" not in r.text
    assert client.get(f"/gui/threads/{ids['root']}").status_code == 200
    assert client.get("/gui/threads/999").status_code == 404


def test_thread_create_and_update(client: TestClient, session_factory: SessionFactory) -> None:
    with session_factory() as session:
        ids = seed(session)
    r = client.post(
        "/gui/threads",
        data={"title": "Brand new", "kind": "goal", "parent_id": ids["root"]},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/threads?selected=")
    new_id = int(r.headers["location"].rsplit("=", 1)[1])
    with session_factory() as session:
        t = session.get(Thread, new_id)
        assert (
            t is not None
            and t.parent_id == ids["root"]
            and [lb.name for lb in t.labels] == ["dashboard"]
        )

    r = client.post(
        f"/gui/threads/{ids['root']}",
        data={
            "title": "Root renamed",
            "kind": "work",
            "status": "in_progress",
            "priority": "high",
            "owner_id": str(ids["robert"]),
            "due_date": "2026-12-01",
            "labels_text": "dashboard, platform",
            "jira_text": "DBOARD3-2; ab-7",
            "description": "desc",
        },
    )
    assert r.status_code == 200 and 'value="Root renamed"' in r.text
    assert r.headers["HX-Trigger"] == "threads-changed"
    with session_factory() as session:
        t = threads.get_thread(session, ids["root"])
        assert t.status == ThreadStatus.IN_PROGRESS and t.owner_id == ids["robert"]
        assert t.due_date == date(2026, 12, 1) and t.description == "desc"
        assert sorted(lb.name for lb in t.labels) == ["dashboard", "platform"]
        assert t.jira_keys == ["AB-7", "DBOARD3-2"]

    # clear owner and due date
    r = client.post(
        f"/gui/threads/{ids['root']}",
        data={"title": "Root renamed", "kind": "work", "status": "open", "priority": "normal"},
    )
    assert r.status_code == 200
    with session_factory() as session:
        t = threads.get_thread(session, ids["root"])
        assert t.owner_id is None and t.due_date is None and t.labels == [] and t.jira_keys == []
    assert (
        client.post(
            "/gui/threads/999",
            data={"title": "x", "kind": "work", "status": "open", "priority": "normal"},
        ).status_code
        == 404
    )


def test_thread_structure_actions(client: TestClient, session_factory: SessionFactory) -> None:
    with session_factory() as session:
        ids = seed(session)
    c, root, other, prov = ids["child"], ids["root"], ids["other"], ids["prov"]

    assert client.post(f"/gui/threads/{c}/parent", data={"parent_id": ""}).status_code == 200
    assert client.post(f"/gui/threads/{root}/parent", data={"parent_id": str(c)}).status_code == 200
    assert (
        client.post(f"/gui/threads/{c}/parent", data={"parent_id": str(root)}).status_code == 400
    )  # cycle

    assert client.post(f"/gui/threads/{c}/link", data={"other_id": str(other)}).status_code == 200
    with session_factory() as session:
        assert [t.id for t in threads.get_thread(session, c).related] == [other]
    assert client.post(f"/gui/threads/{c}/unlink/{other}").status_code == 200
    with session_factory() as session:
        assert threads.get_thread(session, c).related == []

    r = client.post(f"/gui/threads/{prov}/confirm")
    assert r.status_code == 200 and "new (AI)" not in r.text

    r = client.post(f"/gui/threads/{c}/status", data={"status": "done"})
    assert r.status_code == 200
    with session_factory() as session:
        assert threads.get_thread(session, c).status == ThreadStatus.DONE

    r = client.post(f"/gui/threads/{c}/mentions/{ids['mention']}/detach")
    assert r.status_code == 200 and "No mentions yet" in r.text

    r = client.post(f"/gui/threads/{other}/merge", data={"from_id": str(prov)})
    assert r.status_code == 200
    with session_factory() as session:
        assert session.get(Thread, prov) is None

    # undo the most recent change on `other` (none recorded by merge; use the status change on c)
    with session_factory() as session:
        from insights.core import changes

        last = changes.list_changes(session, changes.ENTITY_THREAD, c, limit=1)[0]
        assert last.field == "status"
    r = client.post(f"/gui/changes/{last.id}/undo?thread_id={c}")
    assert r.status_code == 200
    with session_factory() as session:
        assert threads.get_thread(session, c).status == ThreadStatus.OPEN

    r = client.post(f"/gui/threads/{root}/delete")
    assert r.status_code == 200 and "Select a thread" in r.text
    assert r.headers["HX-Trigger"] == "threads-changed"
    with session_factory() as session:
        assert session.get(Thread, root) is None
    assert client.post("/gui/threads/999/delete").status_code == 404


def test_people_pages(client: TestClient, session_factory: SessionFactory) -> None:
    with session_factory() as session:
        ids = seed(session)
    text = client.get("/people").text
    assert "Robert" in text and "team/people/robert" in text
    text = client.get(f"/people/{ids['robert']}").text
    assert "Other agreement" in text and "Child work" in text and "To review" in text
    assert client.get("/people/999").status_code == 404

    r = client.post(
        "/gui/people",
        data={"name": "Sam", "notes_dir": "team/people/sam", "aliases": "S, Sammy"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with session_factory() as session:
        sam = session.query(Person).filter_by(name="Sam").one()
        assert sam.aliases == ["S", "Sammy"] and sam.notes_dir == "team/people/sam"
    assert client.post("/gui/people", data={"name": "Sam"}).status_code == 400

    r = client.post(
        f"/gui/people/{ids['robert']}",
        data={"name": "Rob", "is_self": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with session_factory() as session:
        rob = session.get(Person, ids["robert"])
        me = session.get(Person, ids["me"])
        assert (
            rob is not None
            and rob.name == "Rob"
            and rob.is_self
            and me is not None
            and not me.is_self
        )

    r = client.post(f"/gui/people/{ids['robert']}/delete", follow_redirects=False)
    assert r.status_code == 303
    with session_factory() as session:
        assert session.get(Person, ids["robert"]) is None


def test_label_pages(client: TestClient, session_factory: SessionFactory) -> None:
    with session_factory() as session:
        ids = seed(session)
    text = client.get("/labels").text
    assert "dashboard" in text and "DBOARD3" in text
    text = client.get(f"/labels/{ids['label']}").text
    assert "Root work" in text and "Alex" in text
    assert client.get("/labels/999").status_code == 404

    r = client.post(
        "/gui/labels",
        data={"name": "Platform", "note_path_prefixes": "team/platform", "jira_projects": "plat"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with session_factory() as session:
        lb = labels.find_label(session, "platform")
        assert (
            lb is not None
            and lb.jira_projects == ["PLAT"]
            and lb.note_path_prefixes == ["team/platform"]
        )
    assert client.post("/gui/labels", data={"name": "platform"}).status_code == 400

    r = client.post(
        f"/gui/labels/{ids['label']}",
        data={"name": "dash", "description": "d"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with session_factory() as session:
        lb = session.get(Label, ids["label"])
        assert lb is not None and lb.name == "dash" and lb.description == "d"

    r = client.post(f"/gui/labels/{ids['label']}/delete", follow_redirects=False)
    assert r.status_code == 303
    with session_factory() as session:
        assert session.get(Label, ids["label"]) is None
