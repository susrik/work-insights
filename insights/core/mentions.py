"""Mentions: dedupe, placement on threads, confirm / move / set aside.

A mention's ``state`` says what the user thinks of its placement; its
``thread_id`` says where it is. Setting aside clears the thread. All
placement changes go through ``core.changes`` for undo.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from insights.core import changes
from insights.core.jira_keys import normalize_keys
from insights.db.models import Actor, Mention, MentionKind, MentionState, Thread, utcnow

_WORDS = re.compile(r"[a-z0-9]+")


def fingerprint(path: str, summary: str) -> str:
    words = " ".join(_WORDS.findall(summary.lower()))
    return hashlib.sha256(f"{path}\n{words}".encode()).hexdigest()[:40]


@dataclass
class MentionFilter:
    state: str = "provisional"  # provisional | confirmed | set_aside | all | unplaced
    thread_id: int | None = None
    owner_id: int | None = None
    job_run_id: int | None = None
    path_prefix: str | None = None
    search: str | None = None
    limit: int = 500


def get_mention(session: Session, mention_id: int) -> Mention:
    mention = session.get(Mention, mention_id)
    if mention is None:
        raise ValueError(f"mention {mention_id} not found")
    return mention


def list_mentions(session: Session, flt: MentionFilter | None = None) -> list[Mention]:
    flt = flt or MentionFilter()
    stmt = (
        select(Mention)
        .options(selectinload(Mention.thread), selectinload(Mention.owner))
        .order_by(Mention.last_seen_at.desc(), Mention.id.desc())
        .limit(flt.limit)
    )
    if flt.state == "unplaced":
        stmt = stmt.where(Mention.thread_id.is_(None), Mention.state != MentionState.SET_ASIDE)
    elif flt.state != "all":
        stmt = stmt.where(Mention.state == MentionState(flt.state))
    if flt.thread_id is not None:
        stmt = stmt.where(Mention.thread_id == flt.thread_id)
    if flt.owner_id is not None:
        stmt = stmt.where(Mention.owner_id == flt.owner_id)
    if flt.job_run_id is not None:
        stmt = stmt.where(Mention.job_run_id == flt.job_run_id)
    if flt.path_prefix:
        stmt = stmt.where(Mention.path.startswith(flt.path_prefix))
    if flt.search:
        stmt = stmt.where(Mention.summary.ilike(f"%{flt.search.strip()}%"))
    return list(session.scalars(stmt))


def find_by_fingerprint(session: Session, fp: str) -> Mention | None:
    return session.scalar(select(Mention).where(Mention.fingerprint == fp))


@dataclass
class MentionInput:
    path: str
    summary: str
    kind: MentionKind = MentionKind.ACTION
    excerpt: str | None = None
    note_date: date | None = None
    owner_id: int | None = None
    owner_name: str | None = None
    due_date: date | None = None
    jira_keys: list[str] = field(default_factory=list)


def upsert_mention(session: Session, data: MentionInput) -> tuple[Mention, bool]:
    """Create the mention or refresh an existing one. Returns (mention, created).

    Re-seeing a mention never changes its state or placement; it only bumps
    ``last_seen_at`` and unions the Jira keys.
    """
    fp = fingerprint(data.path, data.summary)
    existing = find_by_fingerprint(session, fp)
    keys = normalize_keys(data.jira_keys)
    if existing:
        existing.last_seen_at = utcnow()
        if data.excerpt:
            existing.excerpt = data.excerpt
        merged = list(existing.jira_keys)
        for k in keys:
            if k not in merged:
                merged.append(k)
        existing.jira_keys = merged
        session.flush()
        return existing, False
    mention = Mention(
        fingerprint=fp,
        path=data.path,
        summary=data.summary[:500],
        kind=data.kind,
        excerpt=data.excerpt,
        note_date=data.note_date,
        owner_id=data.owner_id,
        owner_name=data.owner_name,
        due_date=data.due_date,
        jira_keys=keys,
    )
    session.add(mention)
    session.flush()
    return mention, True


def place_mention(
    session: Session,
    mention_id: int,
    thread_id: int | None,
    actor: Actor = Actor.USER,
    job_run_id: int | None = None,
    reason: str | None = None,
    confirmed: bool | None = None,
) -> Mention:
    """Put a mention on a thread (or detach it with ``thread_id=None``).

    A user placement is confirmed; an AI placement is provisional unless
    ``confirmed`` says otherwise.
    """
    mention = get_mention(session, mention_id)
    thread: Thread | None = None
    if thread_id is not None:
        thread = session.get(Thread, thread_id)
        if thread is None:
            raise ValueError(f"thread {thread_id} not found")
    if mention.thread_id != thread_id:
        changes.record(
            session,
            changes.ENTITY_MENTION,
            mention.id,
            "thread_id",
            mention.thread_id,
            thread_id,
            actor,
            job_run_id,
        )
        mention.thread = thread
    if confirmed is None:
        confirmed = actor == Actor.USER
    new_state = MentionState.CONFIRMED if confirmed else MentionState.PROVISIONAL
    _set_state(session, mention, new_state, actor, job_run_id)
    mention.placed_by = actor
    mention.placement_reason = reason
    if actor == Actor.AI:
        mention.job_run_id = job_run_id
    if thread is not None:
        thread.last_mentioned_at = utcnow()
    session.flush()
    return mention


def confirm_mention(session: Session, mention_id: int) -> Mention:
    mention = get_mention(session, mention_id)
    _set_state(session, mention, MentionState.CONFIRMED, Actor.USER, None)
    session.flush()
    return mention


def confirm_mentions_on_thread(session: Session, thread_id: int) -> int:
    count = 0
    for mention in session.scalars(
        select(Mention).where(
            Mention.thread_id == thread_id, Mention.state == MentionState.PROVISIONAL
        )
    ):
        _set_state(session, mention, MentionState.CONFIRMED, Actor.USER, None)
        count += 1
    session.flush()
    return count


def set_aside(
    session: Session,
    mention_id: int,
    actor: Actor = Actor.USER,
    job_run_id: int | None = None,
    reason: str | None = None,
) -> Mention:
    """Drop a mention from triage: noise, already done, or not actionable.
    It stays in the database so re-scans never resurface it."""
    mention = get_mention(session, mention_id)
    if mention.thread_id is not None:
        changes.record(
            session,
            changes.ENTITY_MENTION,
            mention.id,
            "thread_id",
            mention.thread_id,
            None,
            actor,
            job_run_id,
        )
        mention.thread = None
    _set_state(session, mention, MentionState.SET_ASIDE, actor, job_run_id)
    if actor == Actor.AI:
        mention.placed_by = actor
        mention.placement_reason = reason
        mention.job_run_id = job_run_id
    session.flush()
    return mention


def _set_state(
    session: Session,
    mention: Mention,
    state: MentionState,
    actor: Actor,
    job_run_id: int | None,
) -> None:
    if mention.state == state:
        return
    changes.record(
        session,
        changes.ENTITY_MENTION,
        mention.id,
        "state",
        mention.state,
        state,
        actor,
        job_run_id,
    )
    mention.state = state
    mention.reviewed_at = utcnow() if actor == Actor.USER else None


def counts_by_state(session: Session) -> dict[str, int]:
    counts = {s.value: 0 for s in MentionState}
    for mention in session.scalars(select(Mention.state)):
        counts[mention.value] += 1
    return counts


@dataclass
class ReviewGroup:
    """Provisional mentions for the Review page, grouped by target thread.
    ``thread`` is None for unplaced mentions and for the set-aside group."""

    thread: Thread | None
    mentions: list[Mention]
    set_aside: bool = False


def review_groups(session: Session, job_run_id: int | None = None) -> list[ReviewGroup]:
    """Provisional mentions grouped by thread, provisional threads first,
    then by most recent sighting. Set-aside mentions from the same run (or
    all AI set-asides when no run is given) form a final group."""
    flt = MentionFilter(state="provisional", job_run_id=job_run_id, limit=2000)
    by_thread: dict[int | None, list[Mention]] = {}
    for mention in list_mentions(session, flt):
        by_thread.setdefault(mention.thread_id, []).append(mention)
    groups = [
        ReviewGroup(thread=session.get(Thread, tid) if tid is not None else None, mentions=ms)
        for tid, ms in by_thread.items()
    ]
    groups.sort(
        key=lambda g: (
            g.thread is None,
            not (g.thread.provisional if g.thread else False),
            -max(m.last_seen_at.timestamp() for m in g.mentions),
        )
    )
    aside_flt = MentionFilter(state="set_aside", job_run_id=job_run_id, limit=200)
    aside = [m for m in list_mentions(session, aside_flt) if m.placed_by == Actor.AI]
    if aside:
        groups.append(ReviewGroup(thread=None, mentions=aside, set_aside=True))
    return groups
