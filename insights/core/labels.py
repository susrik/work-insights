from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from insights.db.models import Label


def normalize_name(name: str) -> str:
    return " ".join(name.split()).strip().lower()


def list_labels(session: Session) -> list[Label]:
    return list(session.scalars(select(Label).order_by(Label.name)))


def get_label(session: Session, label_id: int) -> Label:
    label = session.get(Label, label_id)
    if label is None:
        raise ValueError(f"label {label_id} not found")
    return label


def find_label(session: Session, name: str) -> Label | None:
    return session.scalar(select(Label).where(Label.name == normalize_name(name)))


def create_label(
    session: Session,
    name: str,
    description: str | None = None,
    color: str | None = None,
    note_path_prefixes: list[str] | None = None,
    jira_projects: list[str] | None = None,
) -> Label:
    name = normalize_name(name)
    if not name:
        raise ValueError("name is required")
    if find_label(session, name):
        raise ValueError(f"label {name!r} already exists")
    label = Label(
        name=name,
        description=description or None,
        color=color or None,
        note_path_prefixes=_norm_prefixes(note_path_prefixes),
        jira_projects=_norm_projects(jira_projects),
    )
    session.add(label)
    session.flush()
    return label


def get_or_create_labels(session: Session, names: list[str]) -> list[Label]:
    labels: list[Label] = []
    for raw in names:
        name = normalize_name(raw)
        if not name:
            continue
        label = find_label(session, name) or create_label(session, name)
        if label not in labels:
            labels.append(label)
    return labels


def update_label(
    session: Session,
    label_id: int,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    note_path_prefixes: list[str] | None = None,
    jira_projects: list[str] | None = None,
) -> Label:
    label = get_label(session, label_id)
    if name is not None:
        new_name = normalize_name(name)
        if new_name and new_name != label.name:
            if find_label(session, new_name):
                raise ValueError(f"label {new_name!r} already exists")
            label.name = new_name
    if description is not None:
        label.description = description or None
    if color is not None:
        label.color = color or None
    if note_path_prefixes is not None:
        label.note_path_prefixes = _norm_prefixes(note_path_prefixes)
    if jira_projects is not None:
        label.jira_projects = _norm_projects(jira_projects)
    session.flush()
    return label


def delete_label(session: Session, label_id: int) -> None:
    session.delete(get_label(session, label_id))
    session.flush()


def labels_for_hints(session: Session, note_paths: list[str], jira_keys: list[str]) -> list[Label]:
    """Labels whose hints match any of the note paths or Jira keys."""
    projects = {k.rsplit("-", 1)[0].upper() for k in jira_keys if "-" in k}
    matched: list[Label] = []
    for label in list_labels(session):
        by_path = any(
            path == prefix or path.startswith(prefix + "/")
            for prefix in label.note_path_prefixes
            for path in note_paths
        )
        by_jira = bool(projects & set(label.jira_projects))
        if by_path or by_jira:
            matched.append(label)
    return matched


def _norm_prefixes(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        v = v.strip().strip("/")
        if v and v not in out:
            out.append(v)
    return out


def _norm_projects(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        v = v.strip().upper()
        if v and v not in out:
            out.append(v)
    return out
