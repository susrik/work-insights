from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from insights.core import labels, people


def test_people_crud_and_self(session: Session) -> None:
    me = people.create_person(session, "Alex", is_self=True)
    bob = people.create_person(session, "Robert", notes_dir="/team/people/robert/", aliases=["Bob"])
    assert people.get_self(session) is me
    assert bob.notes_dir == "team/people/robert"
    assert [p.name for p in people.list_people(session)] == ["Alex", "Robert"]

    people.update_person(session, bob.id, is_self=True, aliases=["Rob", " "], jira_username="rob")
    assert people.get_self(session) is bob
    assert bob.aliases == ["Rob"]
    assert me.is_self is False

    people.update_person(session, bob.id, name="Robert S", notes_dir="", jira_username="")
    assert bob.name == "Robert S"
    assert bob.notes_dir is None
    assert bob.jira_username is None

    people.delete_person(session, bob.id)
    assert len(people.list_people(session)) == 1
    with pytest.raises(ValueError):
        people.get_person(session, bob.id)


def test_person_validation(session: Session) -> None:
    people.create_person(session, "Alex")
    with pytest.raises(ValueError):
        people.create_person(session, "alex ".title())
    with pytest.raises(ValueError):
        people.create_person(session, "  ")


def test_resolve_person(session: Session) -> None:
    bob = people.create_person(session, "Robert", notes_dir="team/people/robert", aliases=["Bob"])
    sam = people.create_person(session, "Sam")
    people.create_person(session, "Samuel", aliases=["sam"])
    assert people.resolve_person(session, None, "team/people/robert/2026-01-01.md") is bob
    assert people.resolve_person(session, "bob", "other/note.md") is bob
    assert people.resolve_person(session, "nobody") is None
    assert people.resolve_person(session, None) is None
    # "sam" matches two people (name + alias) -> ambiguous
    assert people.resolve_person(session, "sam") is None
    assert people.resolve_person(session, "Samuel") is not sam


def test_seed_from_notes(session: Session) -> None:
    people.create_person(session, "Alex", is_self=True)
    created = people.seed_from_notes(
        session,
        ["team/people/jamie_q", "team/people/pat/", "team/people/jamie_q", "", "team/people/alex"],
    )
    assert [p.name for p in created] == ["Jamie Q", "Pat"]
    assert created[0].notes_dir == "team/people/jamie_q"
    # second seed is a no-op for existing dirs; existing names are skipped
    assert people.seed_from_notes(session, ["team/people/pat", "x/alex"]) == []


def test_labels_crud(session: Session) -> None:
    lb = labels.create_label(
        session,
        "  Dashboard  ",
        description="NOC dashboard",
        note_path_prefixes=["/swd/dashboard/", "swd/dashboard"],
        jira_projects=["dboard3", "DBOARD3"],
    )
    assert lb.name == "dashboard"
    assert lb.note_path_prefixes == ["swd/dashboard"]
    assert lb.jira_projects == ["DBOARD3"]
    assert labels.find_label(session, "DASHBOARD") is lb
    with pytest.raises(ValueError):
        labels.create_label(session, "Dashboard")
    with pytest.raises(ValueError):
        labels.create_label(session, " ")

    churn = labels.create_label(session, "churn")
    with pytest.raises(ValueError):
        labels.update_label(session, lb.id, name="Churn")
    labels.update_label(
        session,
        lb.id,
        name="dash",
        description="",
        color="#112233",
        jira_projects=[],
        note_path_prefixes=["a"],
    )
    assert lb.name == "dash"
    assert lb.description is None
    assert lb.color == "#112233"
    assert lb.jira_projects == []
    assert [x.name for x in labels.list_labels(session)] == ["churn", "dash"]

    got = labels.get_or_create_labels(session, ["dash", "New One", "", "new one"])
    assert [x.name for x in got] == ["dash", "new one"]

    labels.delete_label(session, churn.id)
    with pytest.raises(ValueError):
        labels.get_label(session, churn.id)


def test_labels_for_hints(session: Session) -> None:
    dash = labels.create_label(
        session, "dashboard", note_path_prefixes=["swd/dashboard"], jira_projects=["DBOARD3"]
    )
    churn = labels.create_label(session, "churn", jira_projects=["CHRN"])
    labels.create_label(session, "misc")
    found = labels.labels_for_hints(session, ["swd/dashboard/x.md"], ["CHRN-12"])
    assert found == [churn, dash]
    assert labels.labels_for_hints(session, ["swd/dashboards/x.md"], []) == []
    assert labels.labels_for_hints(session, ["swd/dashboard"], ["nokey"]) == [dash]
