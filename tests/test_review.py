from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from insights.core import mentions, people, threads
from insights.db.models import Actor, JobRun, JobRunStatus, Mention, MentionState
from insights.db.session import SessionFactory


def seed_review(session: Session) -> dict[str, int]:
    """One intake run that placed two mentions on an existing thread, one on
    a new provisional thread, set one aside and left one unplaced."""
    run = JobRun(job_name="intake", status=JobRunStatus.SUCCESS)
    session.add(run)
    session.flush()
    robert = people.create_person(session, "Robert")
    existing = threads.create_thread(session, "Existing work", jira_keys=["AB-1"])
    new = threads.create_thread(
        session, "New provisional", provisional=True, actor=Actor.AI, job_run_id=run.id
    )
    ids: dict[str, int] = {"run": run.id, "existing": existing.id, "new": new.id}
    specs = [
        ("a", "notes/a.md", existing.id, "AB-1 says so"),
        ("b", "notes/a.md", existing.id, None),
        ("c", "notes/b.md", new.id, "new thread"),
        ("d", "notes/b.md", None, "unplaced"),
    ]
    for name, path, thread_id, reason in specs:
        m, _ = mentions.upsert_mention(
            session,
            mentions.MentionInput(
                path=path,
                summary=f"Mention {name}",
                excerpt=f"line {name}",
                owner_id=robert.id,
                jira_keys=["AB-1"] if name == "a" else [],
            ),
        )
        mentions.place_mention(
            session, m.id, thread_id, actor=Actor.AI, job_run_id=run.id, reason=reason
        )
        ids[name] = m.id
    aside, _ = mentions.upsert_mention(
        session, mentions.MentionInput(path="notes/b.md", summary="Mention e")
    )
    mentions.set_aside(session, aside.id, actor=Actor.AI, job_run_id=run.id, reason="noise")
    ids["e"] = aside.id
    session.commit()
    return ids


def test_review_groups(session: Session) -> None:
    ids = seed_review(session)
    groups = mentions.review_groups(session)
    titles = [
        g.thread.title if g.thread else ("aside" if g.set_aside else "unplaced") for g in groups
    ]
    assert titles == ["New provisional", "Existing work", "unplaced", "aside"]
    assert [m.id for m in groups[3].mentions] == [ids["e"]]
    assert len(groups[1].mentions) == 2
    # filtering by another run shows nothing
    assert mentions.review_groups(session, job_run_id=ids["run"] + 1) == []
    assert len(mentions.review_groups(session, job_run_id=ids["run"])) == 4


def test_review_page_and_actions(client: TestClient, session_factory: SessionFactory) -> None:
    with session_factory() as session:
        ids = seed_review(session)
    run = ids["run"]

    r = client.get("/review")
    assert r.status_code == 200
    assert "New provisional" in r.text and "Set aside by intake" in r.text and "Unplaced" in r.text
    assert "https://example.org/notes/blob/main/notes/a.md" in r.text
    assert "https://jira.example.org/browse/AB-1" in r.text
    assert f"#{run} " in client.get(f"/review?run={run}").text

    def state(mention_id: int) -> tuple[MentionState, int | None]:
        with session_factory() as session:
            m = session.get(Mention, mention_id)
            assert m is not None
            return m.state, m.thread_id

    r = client.post(f"/gui/review/mentions/{ids['a']}/confirm?run={run}")
    assert r.status_code == 200 and "Mention a" not in r.text
    assert state(ids["a"]) == (MentionState.CONFIRMED, ids["existing"])

    r = client.post(f"/gui/review/mentions/{ids['b']}/set-aside")
    assert r.status_code == 200 and "Mention b" in r.text  # now in the set-aside group
    assert state(ids["b"]) == (MentionState.SET_ASIDE, None)

    r = client.post(f"/gui/review/mentions/{ids['b']}/restore")
    assert r.status_code == 200
    assert state(ids["b"]) == (MentionState.PROVISIONAL, None)

    r = client.post(f"/gui/review/mentions/{ids['d']}/move", data={"thread_id": ids["existing"]})
    assert r.status_code == 200
    assert state(ids["d"]) == (MentionState.CONFIRMED, ids["existing"])

    r = client.post(
        f"/gui/review/mentions/{ids['b']}/new-thread", data={"title": "Brand new", "kind": "goal"}
    )
    assert r.status_code == 200
    s, tid = state(ids["b"])
    assert s == MentionState.CONFIRMED and tid is not None
    with session_factory() as session:
        created = threads.get_thread(session, tid)
        assert (
            created.title == "Brand new"
            and created.kind.value == "goal"
            and not created.provisional
        )

    r = client.post(f"/gui/review/threads/{ids['new']}/confirm")
    assert r.status_code == 200
    assert state(ids["c"]) == (MentionState.CONFIRMED, ids["new"])
    with session_factory() as session:
        assert not threads.get_thread(session, ids["new"]).provisional

    assert client.post("/gui/review/mentions/99999/confirm").status_code == 404
    assert client.post("/gui/review/threads/99999/confirm").status_code == 404


def test_review_dismiss_thread_and_revert_run(
    client: TestClient, session_factory: SessionFactory
) -> None:
    with session_factory() as session:
        ids = seed_review(session)

    r = client.post(f"/gui/review/threads/{ids['new']}/dismiss")
    assert r.status_code == 200 and "New provisional" not in r.text
    with session_factory() as session:
        m = session.get(Mention, ids["c"])
        assert m is not None and m.thread_id is None and m.state == MentionState.PROVISIONAL

    r = client.post(f"/gui/review/runs/{ids['run']}/revert")
    assert r.status_code == 200
    # placements are undone; the mentions themselves remain, now unplaced
    assert "AB-1 says so" not in r.text and "Unplaced" in r.text
    with session_factory() as session:
        for key in ("a", "b", "c", "d", "e"):
            m = session.get(Mention, ids[key])
            assert m is not None and m.thread_id is None and m.state == MentionState.PROVISIONAL
        groups = mentions.review_groups(session)
        assert len(groups) == 1 and groups[0].thread is None and not groups[0].set_aside
