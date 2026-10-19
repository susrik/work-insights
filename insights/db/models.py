"""SQLAlchemy 2.0 declarative models.

Vocabulary (see docs/reference/concepts.md):

- **Thread** - a unit of work, an agreement, or a goal; single-parent tree.
- **Mention** - a place in a note where a thread was mentioned; attached to a
  thread provisionally by intake, confirmed by the user.
- **Label** - a grouping; optional hints auto-apply it from note paths and
  Jira project keys.
- **Change** - append-only record of every mutation, used for undo.
- **Insight** - an AI proposal about structure, applied or dismissed.

Keep everything dialect-portable; schema changes require an Alembic migration.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _enum(cls: type[enum.StrEnum]) -> Enum:
    return Enum(cls, native_enum=False, length=20)


class ThreadKind(enum.StrEnum):
    WORK = "work"
    AGREEMENT = "agreement"
    GOAL = "goal"


class ThreadStatus(enum.StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    DROPPED = "dropped"


OPEN_STATUSES = (ThreadStatus.OPEN, ThreadStatus.IN_PROGRESS, ThreadStatus.BLOCKED)


class ThreadPriority(enum.StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class MentionKind(enum.StrEnum):
    ACTION = "action"
    QUESTION = "question"
    RISK = "risk"
    DEADLINE = "deadline"


class MentionState(enum.StrEnum):
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    SET_ASIDE = "set_aside"


class Actor(enum.StrEnum):
    USER = "user"
    AI = "ai"


thread_labels = Table(
    "thread_labels",
    Base.metadata,
    Column("thread_id", ForeignKey("threads.id", ondelete="CASCADE"), primary_key=True),
    Column("label_id", ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True),
)


class Person(Base):
    """A team member, or the owner (``is_self``). Not a login account."""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    is_self: Mapped[bool] = mapped_column(default=False)
    notes_dir: Mapped[str | None] = mapped_column(String(500))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    jira_username: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    threads: Mapped[list[Thread]] = relationship(back_populates="owner")


class Label(Base):
    """A grouping of threads (project, topic, team, ...).

    ``note_path_prefixes`` and ``jira_projects`` are hints: a thread whose
    mentions come from a matching note folder, or that links a matching Jira
    project, gets the label automatically during intake.
    """

    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(7))
    note_path_prefixes: Mapped[list[str]] = mapped_column(JSON, default=list)
    jira_projects: Mapped[list[str]] = mapped_column(JSON, default=list)

    threads: Mapped[list[Thread]] = relationship(secondary=thread_labels, back_populates="labels")


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[ThreadKind] = mapped_column(_enum(ThreadKind), default=ThreadKind.WORK)
    status: Mapped[ThreadStatus] = mapped_column(_enum(ThreadStatus), default=ThreadStatus.OPEN)
    priority: Mapped[ThreadPriority] = mapped_column(
        _enum(ThreadPriority), default=ThreadPriority.NORMAL
    )
    provisional: Mapped[bool] = mapped_column(default=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id", ondelete="SET NULL"))
    due_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_mentioned_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[Actor] = mapped_column(_enum(Actor), default=Actor.USER)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    owner: Mapped[Person | None] = relationship(back_populates="threads")
    parent: Mapped[Thread | None] = relationship(
        remote_side="Thread.id", back_populates="children", foreign_keys=[parent_id]
    )
    children: Mapped[list[Thread]] = relationship(back_populates="parent", foreign_keys=[parent_id])
    labels: Mapped[list[Label]] = relationship(secondary=thread_labels, back_populates="threads")
    jira_links: Mapped[list[JiraLink]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )
    mentions: Mapped[list[Mention]] = relationship(back_populates="thread")
    links_out: Mapped[list[ThreadLink]] = relationship(
        back_populates="source",
        foreign_keys="ThreadLink.source_id",
        cascade="all, delete-orphan",
    )
    links_in: Mapped[list[ThreadLink]] = relationship(
        back_populates="target",
        foreign_keys="ThreadLink.target_id",
        cascade="all, delete-orphan",
    )

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def jira_keys(self) -> list[str]:
        return [link.key for link in self.jira_links]

    @property
    def related(self) -> list[Thread]:
        return [link.target for link in self.links_out] + [link.source for link in self.links_in]


class ThreadLink(Base):
    """A non-dependent "related" link between two threads (undirected in the UI)."""

    __tablename__ = "thread_links"
    __table_args__ = (UniqueConstraint("source_id", "target_id", name="uq_thread_links_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    target_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    source: Mapped[Thread] = relationship(foreign_keys=[source_id], back_populates="links_out")
    target: Mapped[Thread] = relationship(foreign_keys=[target_id], back_populates="links_in")


class JiraLink(Base):
    """Thread <-> Jira issue key. ``summary``/``status`` are reserved for the
    future read-only fetch and stay empty in v1."""

    __tablename__ = "jira_links"
    __table_args__ = (UniqueConstraint("thread_id", "key", name="uq_jira_links_thread_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(50), index=True)
    summary: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str | None] = mapped_column(String(100))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)

    thread: Mapped[Thread] = relationship(back_populates="jira_links")

    @property
    def project(self) -> str:
        return self.key.rsplit("-", 1)[0]


class Mention(Base):
    """A place in a note where something actionable was mentioned.

    Mentions are deduplicated by ``fingerprint`` (note path + normalized
    summary); re-scans bump ``last_seen_at``. Intake places each mention on a
    thread as ``provisional``; the user confirms, moves, or sets it aside. A
    set-aside mention keeps its ``thread_id`` = NULL and is never re-surfaced.
    """

    __tablename__ = "mentions"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    path: Mapped[str] = mapped_column(String(1000), index=True)
    note_date: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(String(500))
    excerpt: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[MentionKind] = mapped_column(_enum(MentionKind), default=MentionKind.ACTION)
    state: Mapped[MentionState] = mapped_column(
        _enum(MentionState), default=MentionState.PROVISIONAL
    )
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date)
    jira_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"), index=True
    )
    placed_by: Mapped[Actor | None] = mapped_column(_enum(Actor))
    placement_reason: Mapped[str | None] = mapped_column(Text)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    owner: Mapped[Person | None] = relationship()
    thread: Mapped[Thread | None] = relationship(back_populates="mentions")


class Change(Base):
    """Append-only log of mutations, for display and undo.

    ``entity`` is ``thread`` or ``mention``; ``field`` names what changed
    (``thread_id``, ``parent_id``, ``title``, ``created``, ``deleted``, ...).
    ``before``/``after`` hold JSON-serializable values.
    """

    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[Actor] = mapped_column(_enum(Actor), default=Actor.USER)
    job_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), index=True
    )
    entity: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(50))
    before: Mapped[object] = mapped_column(JSON, nullable=True)
    after: Mapped[object] = mapped_column(JSON, nullable=True)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class InsightStatus(enum.StrEnum):
    NEW = "new"
    APPLIED = "applied"
    DISMISSED = "dismissed"


class Insight(Base):
    """An AI proposal about structure: split, merge, reparent, rename, ...

    ``payload`` carries the concrete proposal; ``thread_id`` is the thread it
    is shown on. Applying or dismissing records the decision.
    """

    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[InsightStatus] = mapped_column(_enum(InsightStatus), default=InsightStatus.NEW)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)


class JobRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[JobRunStatus] = mapped_column(_enum(JobRunStatus), default=JobRunStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSON)

    log_entries: Mapped[list[JobLogEntry]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="JobLogEntry.id"
    )


class JobLogKind(enum.StrEnum):
    INFO = "info"
    STEP = "step"
    WAIT = "wait"
    LLM = "llm"
    ERROR = "error"


class JobLogEntry(Base):
    __tablename__ = "job_log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    run: Mapped[JobRun] = relationship(back_populates="log_entries")


class UIPreference(Base):
    __tablename__ = "ui_preferences"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
