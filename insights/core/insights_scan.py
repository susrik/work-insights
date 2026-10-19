"""Structure insights: the smart-tier model looks at the open threads and
proposes splits, merges, reparents, and threads that look done or stale.

Proposals are stored as ``Insight`` rows and shown on the thread they
concern. Applying one runs the matching ``core.threads`` operations with
``actor=Actor.AI`` and the scan's run id, so it can be undone like any
other change. Dismissing records the decision so the same proposal is not
raised again until the thread changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from insights.core import mentions as mentions_svc
from insights.core import threads as threads_svc
from insights.core.llm import LLMClient, LLMJSONError, LLMResult, complete_json
from insights.db.models import (
    Actor,
    Insight,
    InsightStatus,
    Mention,
    MentionState,
    Thread,
    ThreadKind,
    ThreadStatus,
    utcnow,
)
from insights.jobs.log import JobLog

KIND_SPLIT = "split"
KIND_MERGE = "merge"
KIND_REPARENT = "reparent"
KIND_DONE = "done"
KIND_STALE = "stale"
KINDS = (KIND_SPLIT, KIND_MERGE, KIND_REPARENT, KIND_DONE, KIND_STALE)

MAX_THREADS = 200
MAX_MENTIONS_PER_THREAD = 8

SYSTEM_PROMPT = """You are the planning assistant of an engineering manager. You are given their
work planner: a list of open THREADS (units of work, agreements, goals) with their
mentions from meeting notes. Your job is to propose improvements to the STRUCTURE only.
Do not invent work. Only propose what the evidence in the mentions supports.

Kinds of proposal:
- split: one thread mixes several distinct pieces of work. Give the new child threads
  (title, kind) and, for each, the ids of the mentions that belong to it. Mentions not
  listed stay on the original thread.
- merge: two threads are the same piece of work. `thread_id` is the one to keep,
  `other_id` the one to fold into it.
- reparent: a thread is clearly part of another. `thread_id` moves under `parent_id`
  (null to make it a root).
- done: the mentions say the work was finished, delivered or superseded.
- stale: nothing has been said about it for a long time and it looks abandoned; ask the
  user whether to drop it.

Rules:
- Be conservative. A bad proposal costs the user more than a missed one. At most one
  proposal per thread, and at most 15 in total; prefer the most valuable.
- Never propose for a thread marked (provisional) except `done`.
- `reason`: one sentence citing the evidence (mention summaries, dates, Jira keys).

Respond with JSON only:
{"proposals": [{"kind": "split|merge|reparent|done|stale", "thread_id": int,
                "reason": str, "other_id": int|null, "parent_id": int|null,
                "children": [{"title": str, "kind": "work|agreement|goal",
                              "mention_ids": [int]}]}]}
"""


class ChildSpec(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    kind: ThreadKind = ThreadKind.WORK
    mention_ids: list[int] = Field(default_factory=list)


class Proposal(BaseModel):
    kind: str
    thread_id: int
    reason: str = ""
    other_id: int | None = None
    parent_id: int | None = None
    children: list[ChildSpec] = Field(default_factory=list)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in KINDS:
            raise ValueError(f"unknown insight kind {value!r}")
        return value


class ScanResult(BaseModel):
    proposals: list[Proposal] = Field(default_factory=list)


@dataclass
class ScanSummary:
    threads: int = 0
    proposed: int = 0
    skipped: int = 0
    tokens: int = 0
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        text = (
            f"threads={self.threads} proposed={self.proposed} "
            f"skipped={self.skipped} tokens={self.tokens}"
        )
        if self.errors:
            text += f" errors={len(self.errors)}"
        return text


def planner_text(session: Session, threads: list[Thread], today: date) -> str:
    lines = [f"Today is {today.isoformat()}.", "", "Open threads:"]
    for t in threads:
        bits = [f"#{t.id}", f"[{t.kind.value}]", f"({t.status.value})", t.title]
        if t.owner:
            bits.append(f"owner={t.owner.name}")
        if t.parent_id is not None:
            bits.append(f"parent=#{t.parent_id}")
        if t.jira_keys:
            bits.append("jira=" + ",".join(t.jira_keys))
        if t.due_date:
            bits.append(f"due={t.due_date}")
        if t.provisional:
            bits.append("(provisional)")
        bits.append(f"created={t.created_at:%Y-%m-%d}")
        if t.last_mentioned_at:
            bits.append(f"last_mentioned={t.last_mentioned_at:%Y-%m-%d}")
        lines.append("- " + " ".join(bits))
        for m in _recent_mentions(session, t.id):
            when = m.note_date.isoformat() if m.note_date else "?"
            owner = f" [{m.owner.name}]" if m.owner else ""
            lines.append(f"    m{m.id} {when} {m.kind.value}{owner}: {m.summary}")
    return "\n".join(lines)


def _recent_mentions(session: Session, thread_id: int) -> list[Mention]:
    return list(
        session.scalars(
            select(Mention)
            .options(selectinload(Mention.owner))
            .where(Mention.thread_id == thread_id, Mention.state != MentionState.SET_ASIDE)
            .order_by(Mention.note_date.desc().nulls_last(), Mention.id.desc())
            .limit(MAX_MENTIONS_PER_THREAD)
        )
    )


def _open_threads(session: Session) -> list[Thread]:
    rows = threads_svc.list_threads(session, threads_svc.ThreadFilter(status="open"))
    rows.sort(key=lambda t: t.id)
    return rows[:MAX_THREADS]


def _fingerprint(proposal: Proposal) -> str:
    return (
        f"{proposal.kind}:{proposal.thread_id}:{proposal.other_id}:{proposal.parent_id}:"
        f"{len(proposal.children)}"
    )


def _existing_fingerprints(session: Session) -> set[str]:
    """Open or dismissed proposals we should not raise again."""
    seen: set[str] = set()
    for ins in session.scalars(
        select(Insight).where(Insight.status.in_([InsightStatus.NEW, InsightStatus.DISMISSED]))
    ):
        seen.add(str(ins.payload.get("fingerprint", "")))
    return seen


def _title_for(proposal: Proposal, by_id: dict[int, Thread]) -> str:
    thread = by_id[proposal.thread_id]
    if proposal.kind == KIND_SPLIT:
        names = ", ".join(c.title for c in proposal.children)
        return f"Split into: {names}"
    if proposal.kind == KIND_MERGE and proposal.other_id in by_id:
        return f"Merge “{by_id[proposal.other_id].title}” into this"
    if proposal.kind == KIND_REPARENT:
        if proposal.parent_id is None:
            return "Make this a top-level thread"
        return f"Move under “{by_id[proposal.parent_id].title}”"
    if proposal.kind == KIND_DONE:
        return "Looks done"
    return f"Looks abandoned: “{thread.title}”"


def _valid(proposal: Proposal, by_id: dict[int, Thread], session: Session) -> str | None:
    """Reason the proposal is unusable, or None."""
    thread = by_id.get(proposal.thread_id)
    if thread is None:
        return "unknown thread"
    if proposal.kind == KIND_MERGE:
        if proposal.other_id is None or proposal.other_id not in by_id:
            return "merge without a known other thread"
        if proposal.other_id == proposal.thread_id:
            return "merge with itself"
    elif proposal.kind == KIND_REPARENT:
        if proposal.parent_id is not None and proposal.parent_id not in by_id:
            return "reparent to unknown thread"
        if proposal.parent_id == thread.parent_id:
            return "already has that parent"
        if proposal.parent_id is not None and (
            proposal.parent_id == thread.id
            or proposal.parent_id in threads_svc.descendant_ids(session, thread.id)
        ):
            return "would create a cycle"
    elif proposal.kind == KIND_SPLIT:
        if not proposal.children:
            return "split without children"
        own = {m.id for m in thread.mentions}
        for child in proposal.children:
            child.mention_ids = [m for m in child.mention_ids if m in own]
    if thread.provisional and proposal.kind != KIND_DONE:
        return "provisional thread"
    return None


def run_scan(
    session: Session,
    llm: LLMClient,
    job_run_id: int | None = None,
    today: date | None = None,
    log: JobLog | None = None,
) -> ScanSummary:
    today = today or date.today()
    summary = ScanSummary()
    threads = _open_threads(session)
    summary.threads = len(threads)
    if not threads:
        if log is not None:
            log.info("no open threads")
        return summary
    by_id = {t.id: t for t in threads}
    prompt = planner_text(session, threads, today)
    if log is not None:
        log.step(f"asking about {len(threads)} threads")
        log.llm_call(True, prompt, system=SYSTEM_PROMPT)

    def on_call(result: LLMResult) -> None:
        if log is not None:
            log.llm_reply(True, result)

    try:
        result, tokens = complete_json(
            llm, prompt, ScanResult, system=SYSTEM_PROMPT, smart=True, on_call=on_call
        )
    except LLMJSONError as exc:
        summary.errors.append(str(exc))
        if log is not None:
            log.error(str(exc))
        return summary
    summary.tokens = tokens

    seen = _existing_fingerprints(session)
    for proposal in result.proposals:
        problem = _valid(proposal, by_id, session)
        fp = _fingerprint(proposal)
        if problem is None and fp in seen:
            problem = "already proposed"
        if problem is not None:
            summary.skipped += 1
            if log is not None:
                log.info(f"skipped {proposal.kind} for #{proposal.thread_id}: {problem}")
            continue
        seen.add(fp)
        payload: dict[str, object] = {
            "fingerprint": fp,
            "other_id": proposal.other_id,
            "parent_id": proposal.parent_id,
            "children": [c.model_dump() for c in proposal.children],
        }
        insight = Insight(
            kind=proposal.kind,
            thread_id=proposal.thread_id,
            title=_title_for(proposal, by_id),
            reason=proposal.reason or None,
            payload=payload,
            job_run_id=job_run_id,
        )
        session.add(insight)
        summary.proposed += 1
        if log is not None:
            log.info(f"#{proposal.thread_id}: {insight.title}")
    session.flush()
    return summary


# --- decisions ---


def list_insights(
    session: Session, thread_id: int | None = None, status: str = "new", limit: int = 200
) -> list[Insight]:
    stmt = select(Insight).order_by(Insight.id.desc()).limit(limit)
    if status != "all":
        stmt = stmt.where(Insight.status == InsightStatus(status))
    if thread_id is not None:
        stmt = stmt.where(Insight.thread_id == thread_id)
    return list(session.scalars(stmt))


def get_insight(session: Session, insight_id: int) -> Insight:
    insight = session.get(Insight, insight_id)
    if insight is None:
        raise ValueError(f"insight {insight_id} not found")
    return insight


def dismiss(session: Session, insight_id: int) -> Insight:
    insight = get_insight(session, insight_id)
    if insight.status == InsightStatus.NEW:
        insight.status = InsightStatus.DISMISSED
        insight.decided_at = utcnow()
        session.flush()
    return insight


def apply(session: Session, insight_id: int) -> Insight:
    """Carry out the proposal. Changes are attributed to the AI and the
    scan's run, so ``changes.undo`` / ``revert_run`` work on them."""
    insight = get_insight(session, insight_id)
    if insight.status != InsightStatus.NEW:
        return insight
    if insight.thread_id is None:
        raise ValueError("insight has no thread")
    thread = threads_svc.get_thread(session, insight.thread_id)
    actor, run = Actor.AI, insight.job_run_id
    payload = insight.payload

    if insight.kind == KIND_SPLIT:
        children = payload.get("children")
        for raw in children if isinstance(children, list) else []:
            child = ChildSpec.model_validate(raw)
            new = threads_svc.create_thread(
                session,
                child.title,
                kind=child.kind,
                parent_id=thread.id,
                owner_id=thread.owner_id,
                actor=actor,
                job_run_id=run,
            )
            for mention_id in child.mention_ids:
                mention = session.get(Mention, mention_id)
                if mention is not None and mention.thread_id == thread.id:
                    mentions_svc.place_mention(
                        session, mention.id, new.id, actor=actor, job_run_id=run, confirmed=True
                    )
    elif insight.kind == KIND_MERGE:
        other = payload.get("other_id")
        if not isinstance(other, int):
            raise ValueError("merge insight without other_id")
        threads_svc.merge_threads(session, thread.id, other, actor, run)
    elif insight.kind == KIND_REPARENT:
        parent = payload.get("parent_id")
        threads_svc.set_parent(
            session, thread.id, parent if isinstance(parent, int) else None, actor, run
        )
    elif insight.kind == KIND_DONE:
        threads_svc.update_thread(
            session, thread.id, status=ThreadStatus.DONE, actor=actor, job_run_id=run
        )
    elif insight.kind == KIND_STALE:
        threads_svc.update_thread(
            session, thread.id, status=ThreadStatus.DROPPED, actor=actor, job_run_id=run
        )
    insight.status = InsightStatus.APPLIED
    insight.decided_at = utcnow()
    session.flush()
    return insight
