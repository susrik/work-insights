"""Intake: extract mentions from notes and place them on threads in one pass.

For each note in scope the LLM gets the folder context chain, the roster,
the catalogue of open threads and the numbered note text. It returns the
mentions it found and, for each, an existing thread, a new thread (shared
by several mentions through a local key) or "set aside". The result is
applied immediately as provisional; the Review page is where the user
confirms, moves or undoes. Every mutation carries the job run id so the
whole run can be reverted.

Deterministic rules applied around the model:

- a mention that cites a Jira key already linked to exactly one open thread
  goes there, even if the model proposed a new thread or set-aside;
- labels whose hints match the note path or the Jira project are added to
  the thread the mention lands on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from insights.config import NotesConfig
from insights.core import labels as labels_svc
from insights.core import mentions as mentions_svc
from insights.core import threads as threads_svc
from insights.core.jira_keys import find_jira_keys, normalize_keys
from insights.core.llm import LLMClient, LLMJSONError, LLMResult, complete_json
from insights.core.people import list_people, resolve_person, seed_from_notes
from insights.db.models import (
    Actor,
    MentionKind,
    Person,
    Thread,
    ThreadKind,
)
from insights.integrations.notes import NoteFile, NotesReader, note_body
from insights.jobs.log import JobLog

MAX_CATALOGUE = 300

SYSTEM_PROMPT = """You are the planning assistant of an engineering manager. You read their private
meeting notes and keep their work planner up to date. The planner holds THREADS: units of
work, agreements with people, and goals. Each thread may have an owner, labels, Jira keys
and a parent thread.

Your job for one note: find every outstanding item in it (a MENTION) and decide where it
belongs.

Rules for finding mentions:
- Only report items still open as of the note. If the note or the context says something
  is done, submitted or superseded, do not report it.
- Cite the 1-based line number where the item appears (lines are prefixed "N: ").
- `kind`: action (something to do), question (open decision), deadline (a hard date),
  risk (a concern to watch).
- `owner`: the manager when the note says "I"/"me" or the action is clearly theirs; a team
  member's roster name when it is theirs; otherwise null. Never guess an ambiguous name.
- `jira`: Jira issue keys on or near the cited line, exactly as written; else [].
- Keep `summary` under 120 characters, imperative mood. Merge duplicates within the note.

Rules for placing mentions:
- Prefer an EXISTING thread from the catalogue when the mention is about the same piece of
  work, agreement or goal. Same Jira key or same people and topic are strong signals.
- Create a NEW thread only when nothing in the catalogue fits. Give it a short, specific
  title (not a category), a kind, and a parent from the catalogue when it is clearly part
  of a larger thread. Several mentions from this note may share one new thread via `key`.
- Mark `set_aside: true` for things that are not actionable, pure information, already
  done, or too vague to act on. Be generous with set_aside: noise is worse than a miss.
- `reason`: one short sentence explaining the placement.

Respond with JSON only:
{"new_threads": [{"key": str, "title": str, "kind": "work|agreement|goal",
                  "parent_id": int|null, "owner": str|null}],
 "items": [{"summary": str, "line": int, "kind": "action|question|deadline|risk",
            "owner": str|null, "due_date": "YYYY-MM-DD"|null, "jira": [str],
            "thread_id": int|null, "new_thread": str|null, "set_aside": bool,
            "reason": str}]}
"""


class NewThreadSpec(BaseModel):
    key: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    kind: ThreadKind = ThreadKind.WORK
    parent_id: int | None = None
    owner: str | None = None


class ExtractedItem(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=1)
    kind: MentionKind = MentionKind.ACTION
    owner: str | None = None
    due_date: date | None = None
    jira: list[str] = Field(default_factory=list)
    thread_id: int | None = None
    new_thread: str | None = None
    set_aside: bool = False
    reason: str = ""

    @field_validator("jira", mode="before")
    @classmethod
    def _normalize_jira(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return normalize_keys([str(v) for v in value])


class IntakeResult(BaseModel):
    new_threads: list[NewThreadSpec] = Field(default_factory=list)
    items: list[ExtractedItem] = Field(default_factory=list)


@dataclass
class IntakeSummary:
    files: int = 0
    mentions_new: int = 0
    mentions_seen: int = 0
    placed_existing: int = 0
    placed_new: int = 0
    set_aside: int = 0
    threads_created: int = 0
    tokens: int = 0
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        text = (
            f"files={self.files} new={self.mentions_new} seen={self.mentions_seen} "
            f"existing={self.placed_existing} new_threads={self.threads_created} "
            f"aside={self.set_aside} tokens={self.tokens}"
        )
        if self.errors:
            text += f" errors={len(self.errors)}"
        return text


def roster_text(people: list[Person]) -> str:
    lines = []
    for person in people:
        role = " (the manager; use for first-person items)" if person.is_self else ""
        aliases = f"; also written as: {', '.join(person.aliases)}" if person.aliases else ""
        lines.append(f"- {person.name}{role}{aliases}")
    return "\n".join(lines) or "- (no roster configured)"


def catalogue_text(threads: list[Thread]) -> str:
    if not threads:
        return "(no threads yet)"
    lines = []
    for t in threads:
        bits = [f"#{t.id}", f"[{t.kind.value}]", t.title]
        if t.owner:
            bits.append(f"owner={t.owner.name}")
        if t.labels:
            bits.append("labels=" + ",".join(lb.name for lb in t.labels))
        if t.jira_keys:
            bits.append("jira=" + ",".join(t.jira_keys))
        if t.parent_id is not None:
            bits.append(f"parent=#{t.parent_id}")
        if t.provisional:
            bits.append("(provisional)")
        lines.append("- " + " ".join(bits))
    return "\n".join(lines)


def build_prompt(path: str, body: str, roster: str, catalogue: str, today: date) -> str:
    return (
        f"Today is {today.isoformat()}.\n\n"
        f"Team roster:\n{roster}\n\n"
        f"Open threads (catalogue):\n{catalogue}\n\n"
        f"Note `{path}`:\n\n{body}"
    )


def extract_and_place(
    llm: LLMClient,
    context: str,
    prompt: str,
    log: JobLog | None = None,
) -> tuple[IntakeResult, int]:
    system = SYSTEM_PROMPT
    if context:
        system += f"\n\nContext for this note's folder (general to specific):\n\n{context}"
    if log is not None:
        log.llm_call(True, prompt, system=system)

    def on_call(result: LLMResult) -> None:
        if log is not None:
            log.llm_reply(True, result)

    return complete_json(llm, prompt, IntakeResult, system=system, smart=True, on_call=on_call)


def apply_result(
    session: Session,
    note: NoteFile,
    lines: dict[int, str],
    result: IntakeResult,
    job_run_id: int | None,
    summary: IntakeSummary,
    log: JobLog | None = None,
) -> None:
    """Create mentions and threads from one note's result, all provisional."""
    created_threads: dict[str, Thread] = {}

    def thread_for_key(key: str) -> Thread | None:
        if key in created_threads:
            return created_threads[key]
        spec = next((s for s in result.new_threads if s.key == key), None)
        if spec is None:
            return None
        parent_id = spec.parent_id if _open_thread(session, spec.parent_id) else None
        owner = _owner(session, spec.owner, note.path)
        thread = threads_svc.create_thread(
            session,
            spec.title,
            kind=spec.kind,
            parent_id=parent_id,
            owner_id=owner.id if owner else None,
            provisional=True,
            actor=Actor.AI,
            job_run_id=job_run_id,
        )
        created_threads[key] = thread
        summary.threads_created += 1
        return thread

    for item in result.items:
        owner = _owner(session, item.owner, note.path)
        excerpt = lines.get(item.line)
        jira_keys = list(dict.fromkeys([*item.jira, *find_jira_keys(excerpt)]))
        mention, created = mentions_svc.upsert_mention(
            session,
            mentions_svc.MentionInput(
                path=note.path,
                summary=item.summary,
                kind=item.kind,
                excerpt=excerpt,
                note_date=note.date,
                owner_id=owner.id if owner else None,
                owner_name=item.owner,
                due_date=item.due_date,
                jira_keys=jira_keys,
            ),
        )
        if not created:
            summary.mentions_seen += 1
            continue
        summary.mentions_new += 1

        target: Thread | None = None
        reason = item.reason or None
        by_jira = threads_svc.threads_for_jira_keys(session, jira_keys)
        if item.thread_id is not None:
            target = _open_thread(session, item.thread_id)
        if target is None and len(by_jira) == 1:
            target = by_jira[0]
            reason = f"Jira key {', '.join(jira_keys)} is linked to this thread"
        if target is None and not item.set_aside and item.new_thread:
            target = thread_for_key(item.new_thread)
            if target is not None:
                summary.placed_new += 1
        elif target is not None:
            summary.placed_existing += 1

        if target is None:
            mentions_svc.set_aside(
                session, mention.id, actor=Actor.AI, job_run_id=job_run_id, reason=reason
            )
            summary.set_aside += 1
            continue

        mentions_svc.place_mention(
            session, mention.id, target.id, actor=Actor.AI, job_run_id=job_run_id, reason=reason
        )
        _apply_hints(session, target, note.path, jira_keys)
        if jira_keys and target.provisional:
            threads_svc.add_jira_keys(session, target.id, jira_keys)

    if log is not None and created_threads:
        titles = ", ".join(t.title for t in created_threads.values())
        log.info(f"{note.path}: new threads: {titles}")


def _owner(session: Session, name: str | None, note_path: str) -> Person | None:
    """A name the model gave wins; a note inside a person's folder is about
    them only when no name was given."""
    if name:
        return resolve_person(session, name)
    return resolve_person(session, None, note_path)


def _open_thread(session: Session, thread_id: int | None) -> Thread | None:
    if thread_id is None:
        return None
    thread = session.get(Thread, thread_id)
    return thread if thread is not None and thread.is_open else None


def _apply_hints(session: Session, thread: Thread, path: str, jira_keys: list[str]) -> None:
    matched = labels_svc.labels_for_hints(session, [path], jira_keys)
    new = [lb for lb in matched if lb not in thread.labels]
    if new:
        threads_svc.add_labels(session, thread.id, new)


def run_intake(
    session: Session,
    llm: LLMClient,
    config: NotesConfig,
    since: datetime | None,
    job_run_id: int | None = None,
    today: date | None = None,
    log: JobLog | None = None,
) -> IntakeSummary:
    """Process notes changed since ``since`` (the whole window when None)."""
    today = today or date.today()
    window_start = today - timedelta(days=config.window_days)
    reader = NotesReader(config.root_path, exclude=tuple(config.exclude))
    summary = IntakeSummary()

    seeded = seed_from_notes(session, reader.people_dirs(config.people_dir))
    if seeded and log is not None:
        log.info(f"added {len(seeded)} people from the notes tree")
    session.commit()

    notes = reader.changed_since(since, window_start)
    if log is not None:
        scope = f"since {since:%Y-%m-%d %H:%M}" if since else f"window from {window_start}"
        log.step(f"{len(notes)} notes in scope ({scope})")

    roster = roster_text(list_people(session))
    for note in notes:
        body = note_body(reader, note, window_start, config.max_file_chars)
        if not body:
            continue
        summary.files += 1
        if log is not None:
            log.step(f"reading {note.path}")
        catalogue = catalogue_text(_catalogue(session))
        prompt = build_prompt(note.path, body, roster, catalogue, today)
        try:
            result, tokens = extract_and_place(llm, reader.context_chain(note.path), prompt, log)
        except LLMJSONError as exc:
            summary.errors.append(f"{note.path}: {exc}")
            if log is not None:
                log.error(f"{note.path}: {exc}")
            continue
        summary.tokens += tokens
        lines = dict(enumerate(reader.read(note.path).splitlines(), start=1))
        apply_result(session, note, lines, result, job_run_id, summary, log)
        if log is not None:
            log.info(f"{note.path}: {len(result.items)} mentions")
        session.commit()
    return summary


def _catalogue(session: Session) -> list[Thread]:
    flt = threads_svc.ThreadFilter(status="open")
    return threads_svc.list_threads(session, flt)[:MAX_CATALOGUE]
