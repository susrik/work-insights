from __future__ import annotations

from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from insights.db.models import Person


def list_people(session: Session) -> list[Person]:
    return list(session.scalars(select(Person).order_by(Person.is_self.desc(), Person.name)))


def get_person(session: Session, person_id: int) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise ValueError(f"person {person_id} not found")
    return person


def get_self(session: Session) -> Person | None:
    return session.scalar(select(Person).where(Person.is_self.is_(True)))


def create_person(
    session: Session,
    name: str,
    is_self: bool = False,
    notes_dir: str | None = None,
    aliases: list[str] | None = None,
    jira_username: str | None = None,
) -> Person:
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    if session.scalar(select(Person).where(Person.name == name)):
        raise ValueError(f"person {name!r} already exists")
    if is_self:
        _clear_self(session)
    person = Person(
        name=name,
        is_self=is_self,
        notes_dir=_norm_dir(notes_dir),
        aliases=[a.strip() for a in (aliases or []) if a.strip()],
        jira_username=jira_username or None,
    )
    session.add(person)
    session.flush()
    return person


def update_person(
    session: Session,
    person_id: int,
    name: str | None = None,
    is_self: bool | None = None,
    notes_dir: str | None = None,
    aliases: list[str] | None = None,
    jira_username: str | None = None,
) -> Person:
    person = get_person(session, person_id)
    if name is not None and name.strip():
        person.name = name.strip()
    if is_self is not None:
        if is_self:
            _clear_self(session)
        person.is_self = is_self
    if notes_dir is not None:
        person.notes_dir = _norm_dir(notes_dir)
    if aliases is not None:
        person.aliases = [a.strip() for a in aliases if a.strip()]
    if jira_username is not None:
        person.jira_username = jira_username or None
    session.flush()
    return person


def delete_person(session: Session, person_id: int) -> None:
    session.delete(get_person(session, person_id))
    session.flush()


def resolve_person(
    session: Session, name: str | None, note_path: str | None = None
) -> Person | None:
    """Attribute by note folder first (a note inside a person's ``notes_dir``
    is about them), then by case-insensitive name/alias. Ambiguous -> None."""
    people = list_people(session)
    if note_path:
        for person in people:
            if person.notes_dir and _under(note_path, person.notes_dir):
                return person
    if not name:
        return None
    needle = name.strip().lower()
    matches = [
        p for p in people if p.name.lower() == needle or needle in (a.lower() for a in p.aliases)
    ]
    return matches[0] if len(matches) == 1 else None


def seed_from_notes(session: Session, person_dirs: list[str]) -> list[Person]:
    """Create one person per folder (``team/people/robert`` -> ``Robert``)."""
    created: list[Person] = []
    existing_dirs = {p.notes_dir for p in list_people(session) if p.notes_dir}
    for rel in person_dirs:
        rel = _norm_dir(rel) or ""
        if not rel or rel in existing_dirs:
            continue
        name = PurePosixPath(rel).name.replace("_", " ").replace("-", " ").title()
        if session.scalar(select(Person).where(Person.name == name)):
            continue
        created.append(create_person(session, name, notes_dir=rel))
    return created


def _clear_self(session: Session) -> None:
    for person in session.scalars(select(Person).where(Person.is_self.is_(True))):
        person.is_self = False


def _norm_dir(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().strip("/")
    return value or None


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")
