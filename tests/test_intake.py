from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from insights.config import Config
from insights.core import changes, labels, mentions, people, threads
from insights.core.intake import (
    ExtractedItem,
    IntakeResult,
    IntakeSummary,
    NewThreadSpec,
    apply_result,
    catalogue_text,
    roster_text,
    run_intake,
)
from insights.core.llm import FakeLLM
from insights.db.models import Actor, JobRun, MentionState, ThreadKind
from insights.db.session import SessionFactory
from insights.integrations.notes import NoteFile
from insights.jobs.builtin import intake as intake_job
from insights.jobs.registry import JobContext, registry


def write_notes(root: Path) -> None:
    (root / "team/people/robert").mkdir(parents=True)
    (root / "AGENTS.md").write_text("Manager: Alex.")
    (root / "team/people/robert/2026-10-10.md").write_text(
        "- Robert to finish the alarm cleanup DBOARD3-12\n"
        "- I should book the review meeting\n"
        "- lunch was nice\n"
        "- decide on GRMT-7 approach\n"
    )
    (root / "team/old.md").write_text("## 2000-01-01\n- nothing\n")


def llm_response(**overrides: object) -> str:
    payload: dict[str, object] = {
        "new_threads": [
            {
                "key": "review",
                "title": "Book the half-year review",
                "kind": "agreement",
                "owner": "Alex",
            },
        ],
        "items": [
            {
                "summary": "Finish the alarm cleanup",
                "line": 1,
                "kind": "action",
                "owner": "Robert",
                "jira": ["DBOARD3-12"],
                "set_aside": True,
                "reason": "model says aside, jira should win",
            },
            {
                "summary": "Book the review meeting",
                "line": 2,
                "owner": "Alex",
                "new_thread": "review",
                "due_date": "2026-11-01",
                "reason": "new agreement",
            },
            {"summary": "Lunch", "line": 3, "set_aside": True, "reason": "not actionable"},
            {
                "summary": "Decide the GRMT approach",
                "line": 4,
                "kind": "question",
                "thread_id": 999,
                "new_thread": "missing-key",
                "reason": "stale id",
            },
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


def seed(session: Session) -> int:
    people.create_person(session, "Alex", is_self=True)
    people.create_person(session, "Robert", notes_dir="team/people/robert")
    labels.create_label(
        session, "dashboard", note_path_prefixes=["team/people"], jira_projects=["DBOARD3"]
    )
    existing = threads.create_thread(
        session, "Dashboard alarm cleanup", labels=["dashboard"], jira_keys=["DBOARD3-12"]
    )
    session.commit()
    return existing.id


def test_prompt_helpers(session: Session) -> None:
    existing_id = seed(session)
    child = threads.create_thread(
        session, "Child", parent_id=existing_id, provisional=True, owner_id=1
    )
    roster = roster_text(people.list_people(session))
    assert "- Alex (the manager; use for first-person items)" in roster
    assert roster_text([]) == "- (no roster configured)"
    cat = catalogue_text(threads.list_threads(session))
    assert f"#{existing_id} [work] Dashboard alarm cleanup labels=dashboard jira=DBOARD3-12" in cat
    assert (
        f"#{child.id} [work] Child owner=Alex labels=dashboard parent=#{existing_id} (provisional)"
        in cat
    )
    assert catalogue_text([]) == "(no threads yet)"


def test_run_intake_places_mentions(session: Session, config: Config) -> None:
    write_notes(config.notes.root_path)
    existing_id = seed(session)
    llm = FakeLLM(responses=[llm_response()])
    run = JobRun(job_name="intake")
    session.add(run)
    session.commit()

    summary = run_intake(
        session, llm, config.notes, None, job_run_id=run.id, today=date(2026, 10, 19)
    )

    assert summary.files == 1
    assert summary.mentions_new == 4
    assert summary.placed_existing == 1
    assert summary.placed_new == 1
    assert summary.set_aside == 2
    assert summary.threads_created == 1
    assert summary.tokens == 10
    assert summary.as_text().startswith(
        "files=1 new=4 seen=0 existing=1 new_threads=1 aside=2 tokens=10"
    )
    assert llm.calls[0]["smart"] is True
    assert "Manager: Alex." in llm.calls[0]["system"]
    assert "1: - Robert to finish" in llm.calls[0]["prompt"]

    by_summary = {
        m.summary: m for m in mentions.list_mentions(session, mentions.MentionFilter(state="all"))
    }
    alarm = by_summary["Finish the alarm cleanup"]
    assert alarm.thread_id == existing_id
    assert alarm.state == MentionState.PROVISIONAL
    assert alarm.placement_reason == "Jira key DBOARD3-12 is linked to this thread"
    assert alarm.owner is not None and alarm.owner.name == "Robert"
    assert alarm.excerpt == "- Robert to finish the alarm cleanup DBOARD3-12"

    review = by_summary["Book the review meeting"]
    assert review.thread is not None and review.thread.provisional
    assert review.thread.kind == ThreadKind.AGREEMENT
    assert review.thread.owner is not None and review.thread.owner.name == "Alex"
    assert review.due_date == date(2026, 11, 1)
    assert [lb.name for lb in review.thread.labels] == ["dashboard"]

    lunch = by_summary["Lunch"]
    assert lunch.state == MentionState.SET_ASIDE
    assert lunch.placed_by == Actor.AI
    assert lunch.placement_reason == "not actionable"
    assert lunch.owner is not None and lunch.owner.name == "Robert"  # note is in Robert's folder

    grmt = by_summary["Decide the GRMT approach"]
    assert grmt.state == MentionState.SET_ASIDE
    assert grmt.jira_keys == ["GRMT-7"]

    # Re-running over the same note creates nothing new.
    again = run_intake(session, llm, config.notes, None, today=date(2026, 10, 19))
    assert again.mentions_new == 0 and again.mentions_seen == 4

    # The whole run can be reverted.
    reverted = changes.revert_run(session, run.id)
    session.commit()
    assert reverted > 0
    assert alarm.thread_id is None
    assert review.thread_id is None


def test_run_intake_handles_bad_llm_output(session: Session, config: Config) -> None:
    write_notes(config.notes.root_path)
    seed(session)
    llm = FakeLLM(responses=["not json"])
    summary = run_intake(session, llm, config.notes, None, today=date(2026, 10, 19))
    assert summary.files == 1
    assert len(summary.errors) == 1
    assert "errors=1" in summary.as_text()
    assert mentions.list_mentions(session, mentions.MentionFilter(state="all")) == []


def test_apply_result_edge_cases(session: Session) -> None:
    seed(session)
    note = NoteFile("misc/note.md", date(2026, 10, 1), date(2026, 10, 1))  # type: ignore[arg-type]
    result = IntakeResult(
        new_threads=[NewThreadSpec(key="k", title="Thing", parent_id=999, owner="Nobody")],
        items=[
            ExtractedItem(summary="Do the thing", line=1, new_thread="k", jira=[" ab-1 ", 7]),  # type: ignore[list-item]
            ExtractedItem(summary="Do the thing too", line=2, new_thread="k"),
            ExtractedItem(summary="Not placed", line=3),
        ],
    )
    summary = IntakeSummary()
    apply_result(session, note, {1: "x AB-2"}, result, None, summary)
    assert summary.threads_created == 1
    assert summary.placed_new == 2
    assert summary.set_aside == 1
    thing = next(t for t in threads.list_threads(session) if t.title == "Thing")
    assert thing.parent_id is None and thing.owner_id is None
    assert thing.jira_keys == ["AB-1", "AB-2"]
    assert ExtractedItem(summary="s", line=1, jira="nope").jira == []  # type: ignore[arg-type]


def test_intake_job(
    session: Session, session_factory: SessionFactory, config: Config, fake_llm: FakeLLM
) -> None:
    write_notes(config.notes.root_path)
    seed(session)
    fake_llm.responses = [llm_response()]
    run = registry.run("intake", session_factory, config, fake_llm, {"full": "true"})
    assert run.status.value == "success"
    assert run.result is not None and run.result.startswith("files=1")
    assert run.tokens_used == 10
    # second, incremental run: nothing changed since the first
    run2 = registry.run("intake", session_factory, config, fake_llm)
    assert run2.result is not None and run2.result.startswith("files=0")


def test_intake_job_requires_config(session: Session, config: Config, fake_llm: FakeLLM) -> None:
    run = JobRun(job_name="intake")
    session.add(run)
    session.commit()
    ctx = JobContext(
        session,
        config.model_copy(update={"notes": config.notes.model_copy(update={"enabled": False})}),
        fake_llm,
        run,
    )
    assert intake_job(ctx) == "notes disabled"
    ctx = JobContext(
        session,
        config.model_copy(update={"llm": config.llm.model_copy(update={"api_key": ""})}),
        fake_llm,
        run,
    )
    assert intake_job(ctx) == "llm not configured"
