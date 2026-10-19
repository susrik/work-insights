from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from insights.config import Config
from insights.core import changes, insights_scan, mentions, people, threads
from insights.core.llm import FakeLLM
from insights.db.models import (
    Actor,
    Insight,
    InsightStatus,
    JobRun,
    Thread,
    ThreadKind,
    ThreadStatus,
)
from insights.db.session import SessionFactory
from insights.jobs.registry import registry


def seed(session: Session) -> dict[str, int]:
    robert = people.create_person(session, "Robert")
    mixed = threads.create_thread(session, "Mixed bag", owner_id=robert.id, jira_keys=["AB-1"])
    dup = threads.create_thread(session, "Mixed bag (dup)")
    parent = threads.create_thread(session, "Big goal", kind=ThreadKind.GOAL)
    child = threads.create_thread(session, "Orphan work")
    finished = threads.create_thread(session, "Finished thing")
    prov = threads.create_thread(session, "AI thread", provisional=True, actor=Actor.AI)
    ids: dict[str, int] = {
        "mixed": mixed.id,
        "dup": dup.id,
        "parent": parent.id,
        "child": child.id,
        "finished": finished.id,
        "prov": prov.id,
    }
    for i, text in enumerate(["Fix the alarm", "Write the report", "Plan the migration"]):
        m, _ = mentions.upsert_mention(
            session,
            mentions.MentionInput(
                path="n.md", summary=text, note_date=date(2026, 10, 1 + i), owner_id=robert.id
            ),
        )
        mentions.place_mention(session, m.id, mixed.id)
        ids[f"m{i}"] = m.id
    other, _ = mentions.upsert_mention(
        session, mentions.MentionInput(path="o.md", summary="Elsewhere")
    )
    mentions.place_mention(session, other.id, dup.id)
    ids["other"] = other.id
    session.commit()
    return ids


def scan_reply(ids: dict[str, int]) -> str:
    return json.dumps(
        {
            "proposals": [
                {
                    "kind": "split",
                    "thread_id": ids["mixed"],
                    "reason": "three topics",
                    "children": [
                        {
                            "title": "Alarm fix",
                            "kind": "work",
                            "mention_ids": [ids["m0"], ids["other"], 999],
                        },
                        {"title": "Report", "kind": "work", "mention_ids": [ids["m1"]]},
                    ],
                },
                {
                    "kind": "merge",
                    "thread_id": ids["mixed"],
                    "reason": "same title",
                    "other_id": ids["dup"],
                },
                {
                    "kind": "reparent",
                    "thread_id": ids["child"],
                    "parent_id": ids["parent"],
                    "reason": "part of the goal",
                },
                {"kind": "done", "thread_id": ids["finished"], "reason": "delivered"},
                {"kind": "stale", "thread_id": ids["dup"], "reason": "silent"},
                {"kind": "done", "thread_id": ids["prov"], "reason": "ok on provisional"},
                # invalid ones
                {
                    "kind": "reparent",
                    "thread_id": ids["prov"],
                    "parent_id": ids["parent"],
                    "reason": "provisional",
                },
                {
                    "kind": "merge",
                    "thread_id": ids["dup"],
                    "other_id": ids["dup"],
                    "reason": "self",
                },
                {"kind": "merge", "thread_id": ids["dup"], "other_id": None, "reason": "none"},
                {
                    "kind": "reparent",
                    "thread_id": ids["parent"],
                    "parent_id": 999,
                    "reason": "unknown",
                },
                {
                    "kind": "reparent",
                    "thread_id": ids["parent"],
                    "parent_id": None,
                    "reason": "already root",
                },
                {
                    "kind": "reparent",
                    "thread_id": ids["parent"],
                    "parent_id": ids["parent"],
                    "reason": "cycle",
                },
                {"kind": "split", "thread_id": ids["mixed"], "children": [], "reason": "empty"},
                {"kind": "done", "thread_id": 999, "reason": "unknown"},
            ]
        }
    )


def test_run_scan_and_apply(session: Session) -> None:
    ids = seed(session)
    run = JobRun(job_name="insights_scan")
    session.add(run)
    session.commit()
    llm = FakeLLM(responses=[scan_reply(ids)])
    summary = insights_scan.run_scan(session, llm, job_run_id=run.id, today=date(2026, 10, 19))
    session.commit()
    assert summary.threads == 6
    assert summary.proposed == 6
    assert summary.skipped == 8
    assert summary.as_text() == "threads=6 proposed=6 skipped=8 tokens=10"
    prompt = llm.calls[0]["prompt"]
    assert f"#{ids['mixed']} [work] (open) Mixed bag owner=Robert jira=AB-1" in prompt
    assert f"m{ids['m2']} 2026-10-03 action [Robert]: Plan the migration" in prompt
    assert "(provisional)" in prompt

    new = insights_scan.list_insights(session)
    titles = {i.kind: i.title for i in new}
    assert titles["split"] == "Split into: Alarm fix, Report"
    assert titles["merge"] == "Merge “Mixed bag (dup)” into this"
    assert titles["reparent"] == "Move under “Big goal”"
    assert titles["done"] == "Looks done"
    assert titles["stale"] == "Looks abandoned: “Mixed bag (dup)”"

    # a second scan proposes nothing new (fingerprints)
    again = insights_scan.run_scan(session, llm, today=date(2026, 10, 19))
    assert again.proposed == 0 and again.skipped == 14

    by_kind = {i.kind: i for i in new if i.thread_id != ids["prov"]}

    insights_scan.apply(session, by_kind["split"].id)
    mixed = threads.get_thread(session, ids["mixed"])
    kids = {c.title: c for c in mixed.children}
    assert set(kids) == {"Alarm fix", "Report"}
    assert kids["Alarm fix"].owner_id == mixed.owner_id
    assert [m.summary for m in kids["Alarm fix"].mentions] == [
        "Fix the alarm"
    ]  # foreign + unknown ids dropped
    assert [m.summary for m in mixed.mentions] == ["Plan the migration"]
    assert by_kind["split"].status == InsightStatus.APPLIED

    insights_scan.apply(session, by_kind["reparent"].id)
    assert threads.get_thread(session, ids["child"]).parent_id == ids["parent"]
    insights_scan.apply(session, by_kind["done"].id)
    assert threads.get_thread(session, ids["finished"]).status == ThreadStatus.DONE
    insights_scan.apply(session, by_kind["stale"].id)
    assert threads.get_thread(session, ids["dup"]).status == ThreadStatus.DROPPED
    insights_scan.apply(session, by_kind["merge"].id)
    assert session.get(Thread, ids["dup"]) is None
    assert "Elsewhere" in [m.summary for m in threads.get_thread(session, ids["mixed"]).mentions]
    # applying twice is a no-op
    assert insights_scan.apply(session, by_kind["merge"].id).status == InsightStatus.APPLIED
    session.commit()

    # everything the scan applied is attributed to its run and revertible
    assert changes.revert_run(session, run.id) > 0
    session.commit()
    assert threads.get_thread(session, ids["child"]).parent_id is None
    assert threads.get_thread(session, ids["finished"]).status == ThreadStatus.OPEN
    assert all(c.title != "Report" for c in threads.list_threads(session))


def test_dismiss_and_edge_cases(session: Session) -> None:
    ids = seed(session)
    llm = FakeLLM(responses=[scan_reply(ids)])
    insights_scan.run_scan(session, llm, today=date(2026, 10, 19))
    done = next(
        i
        for i in insights_scan.list_insights(session)
        if i.kind == "done" and i.thread_id == ids["finished"]
    )
    assert insights_scan.dismiss(session, done.id).status == InsightStatus.DISMISSED
    assert insights_scan.dismiss(session, done.id).status == InsightStatus.DISMISSED
    assert insights_scan.apply(session, done.id).status == InsightStatus.DISMISSED
    assert threads.get_thread(session, ids["finished"]).status == ThreadStatus.OPEN
    assert len(insights_scan.list_insights(session, status="dismissed")) == 1
    assert len(insights_scan.list_insights(session, status="all")) == 6
    assert len(insights_scan.list_insights(session, thread_id=ids["mixed"])) == 2

    import pytest

    with pytest.raises(ValueError):
        insights_scan.get_insight(session, 999)
    orphan = Insight(kind="done", thread_id=None, title="x")
    session.add(orphan)
    session.flush()
    with pytest.raises(ValueError):
        insights_scan.apply(session, orphan.id)
    broken = Insight(kind="merge", thread_id=ids["mixed"], title="x", payload={"other_id": None})
    session.add(broken)
    session.flush()
    with pytest.raises(ValueError):
        insights_scan.apply(session, broken.id)
    with pytest.raises(ValueError):
        insights_scan.Proposal(kind="rename", thread_id=1)


def test_scan_bad_output_and_empty(session: Session) -> None:
    assert (
        insights_scan.run_scan(session, FakeLLM(responses=["{}"]), today=date(2026, 10, 19)).threads
        == 0
    )
    seed(session)
    summary = insights_scan.run_scan(session, FakeLLM(responses=["nope"]), today=date(2026, 10, 19))
    assert summary.errors and "errors=1" in summary.as_text()


def test_scan_job(
    session: Session, session_factory: SessionFactory, config: Config, fake_llm: FakeLLM
) -> None:
    ids = seed(session)
    fake_llm.responses = [scan_reply(ids)]
    run = registry.run("insights_scan", session_factory, config, fake_llm)
    assert (
        run.status.value == "success"
        and run.result is not None
        and run.result.startswith("threads=6 proposed=6")
    )
    assert run.tokens_used == 10
    config.llm.api_key = ""
    run = registry.run("insights_scan", session_factory, config, fake_llm)
    assert run.result == "llm not configured"


def test_insights_api_and_gui(
    client: TestClient, session_factory: SessionFactory, fake_llm: FakeLLM
) -> None:
    with session_factory() as session:
        ids = seed(session)
    fake_llm.responses = [scan_reply(ids)]
    assert client.post("/api/jobs/insights_scan/run").json()["status"] == "success"

    rows = client.get("/api/insights", params={"thread_id": ids["mixed"]}).json()
    assert {r["kind"] for r in rows} == {"split", "merge"}
    assert client.get("/api/insights", params={"status": "bogus"}).status_code == 400
    assert client.post("/api/insights/999/apply").status_code == 404
    assert client.post("/api/insights/999/dismiss").status_code == 404

    text = client.get("/").text
    assert "Proposals" in text and "Looks done" in text
    text = client.get(f"/threads?selected={ids['mixed']}").text
    assert "Split into: Alarm fix, Report" in text and "Alarm fix" in text and "Apply" in text

    split = next(r for r in rows if r["kind"] == "split")
    merge = next(r for r in rows if r["kind"] == "merge")
    r = client.post(f"/gui/insights/{merge['id']}/dismiss?thread_id={ids['mixed']}")
    assert r.status_code == 200 and "Merge “" not in r.text
    r = client.post(f"/gui/insights/{split['id']}/apply?thread_id={ids['mixed']}")
    assert r.status_code == 200 and "Split into" not in r.text and "Alarm fix" in r.text
    assert client.post("/gui/insights/999/apply?thread_id=1").status_code == 404

    done = next(
        r
        for r in client.get("/api/insights").json()
        if r["kind"] == "done" and r["thread_id"] == ids["finished"]
    )
    assert client.post(f"/api/insights/{done['id']}/apply").json()["status"] == "applied"
    assert client.post(f"/api/insights/{done['id']}/dismiss").json()["status"] == "applied"
