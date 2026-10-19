"""Pydantic models for every JSON body in and out of ``/api``."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from insights.db.models import (
    Actor,
    Change,
    Insight,
    InsightStatus,
    JobRun,
    Label,
    Mention,
    MentionKind,
    MentionState,
    Person,
    Thread,
    ThreadKind,
    ThreadPriority,
    ThreadStatus,
)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- people ---


class PersonIn(BaseModel):
    name: str
    is_self: bool = False
    notes_dir: str | None = None
    aliases: list[str] = Field(default_factory=list)
    jira_username: str | None = None


class PersonUpdate(BaseModel):
    name: str | None = None
    is_self: bool | None = None
    notes_dir: str | None = None
    aliases: list[str] | None = None
    jira_username: str | None = None


class PersonOut(_Out):
    id: int
    name: str
    is_self: bool
    notes_dir: str | None
    aliases: list[str]
    jira_username: str | None

    @classmethod
    def of(cls, p: Person) -> PersonOut:
        return cls.model_validate(p)


# --- labels ---


class LabelIn(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    note_path_prefixes: list[str] = Field(default_factory=list)
    jira_projects: list[str] = Field(default_factory=list)


class LabelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    note_path_prefixes: list[str] | None = None
    jira_projects: list[str] | None = None


class LabelOut(_Out):
    id: int
    name: str
    description: str | None
    color: str | None
    note_path_prefixes: list[str]
    jira_projects: list[str]

    @classmethod
    def of(cls, lb: Label) -> LabelOut:
        return cls.model_validate(lb)


# --- threads ---


class ThreadIn(BaseModel):
    title: str
    kind: ThreadKind = ThreadKind.WORK
    description: str | None = None
    priority: ThreadPriority = ThreadPriority.NORMAL
    owner_id: int | None = None
    parent_id: int | None = None
    due_date: date | None = None
    labels: list[str] = Field(default_factory=list)
    jira_keys: list[str] = Field(default_factory=list)


class ThreadUpdate(BaseModel):
    """``owner_id`` / ``due_date``: omit to leave unchanged, send null to clear."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    kind: ThreadKind | None = None
    status: ThreadStatus | None = None
    priority: ThreadPriority | None = None
    owner_id: int | None = None
    due_date: date | None = None


class ThreadParent(BaseModel):
    parent_id: int | None


class ThreadLabels(BaseModel):
    labels: list[str]


class ThreadJiraKeys(BaseModel):
    keys: list[str]


class ThreadLinkIn(BaseModel):
    other_id: int


class ThreadOut(_Out):
    id: int
    title: str
    description: str | None
    kind: ThreadKind
    status: ThreadStatus
    priority: ThreadPriority
    provisional: bool
    owner_id: int | None
    parent_id: int | None
    due_date: date | None
    completed_at: datetime | None
    last_mentioned_at: datetime | None
    created_by: Actor
    created_at: datetime
    updated_at: datetime
    labels: list[str]
    jira_keys: list[str]
    related_ids: list[int]
    mention_count: int

    @classmethod
    def of(cls, t: Thread) -> ThreadOut:
        return cls(
            id=t.id,
            title=t.title,
            description=t.description,
            kind=t.kind,
            status=t.status,
            priority=t.priority,
            provisional=t.provisional,
            owner_id=t.owner_id,
            parent_id=t.parent_id,
            due_date=t.due_date,
            completed_at=t.completed_at,
            last_mentioned_at=t.last_mentioned_at,
            created_by=t.created_by,
            created_at=t.created_at,
            updated_at=t.updated_at,
            labels=[lb.name for lb in t.labels],
            jira_keys=t.jira_keys,
            related_ids=[r.id for r in t.related],
            mention_count=len(t.mentions),
        )


class MergeIn(BaseModel):
    from_id: int


# --- mentions ---


class MentionOut(_Out):
    id: int
    path: str
    note_date: date | None
    summary: str
    excerpt: str | None
    kind: MentionKind
    state: MentionState
    owner_id: int | None
    owner_name: str | None
    due_date: date | None
    jira_keys: list[str]
    thread_id: int | None
    placed_by: Actor | None
    placement_reason: str | None
    job_run_id: int | None
    first_seen_at: datetime
    last_seen_at: datetime

    @classmethod
    def of(cls, m: Mention) -> MentionOut:
        return cls.model_validate(m)


class MentionPlace(BaseModel):
    thread_id: int | None


class NewThreadFromMentions(BaseModel):
    title: str
    kind: ThreadKind = ThreadKind.WORK
    mention_ids: list[int]
    parent_id: int | None = None
    owner_id: int | None = None


# --- changes ---


class ChangeOut(_Out):
    id: int
    actor: Actor
    job_run_id: int | None
    entity: str
    entity_id: int
    field: str
    before: object
    after: object
    reverted_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, c: Change) -> ChangeOut:
        return cls.model_validate(c)


class RevertOut(BaseModel):
    reverted: int


# --- insights ---


class InsightOut(_Out):
    id: int
    kind: str
    thread_id: int | None
    title: str
    reason: str | None
    payload: dict[str, object]
    status: InsightStatus
    job_run_id: int | None
    created_at: datetime
    decided_at: datetime | None

    @classmethod
    def of(cls, i: Insight) -> InsightOut:
        return cls.model_validate(i)


# --- jobs ---


class JobRunOut(_Out):
    id: int
    job_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    result: str | None
    error: str | None
    tokens_used: int

    @classmethod
    def of(cls, r: JobRun) -> JobRunOut:
        return cls.model_validate(r)


class JobStarted(BaseModel):
    run_id: int


class JobLogEntryOut(BaseModel):
    id: int
    kind: str
    message: str
    meta: dict[str, object]
    created_at: datetime | None


# --- misc ---


class StatusOut(BaseModel):
    ok: bool = True
    version: str
    threads_open: int
    mentions: dict[str, int]
    llm_enabled: bool
    notes_enabled: bool


class ErrorOut(BaseModel):
    detail: str
