from __future__ import annotations

from fastapi.testclient import TestClient

from insights.config import Config
from insights.core.llm import FakeLLM
from insights.db.models import JobLogEntry, JobRun, JobRunStatus
from insights.db.session import SessionFactory
from insights.server.app import create_app


def test_pages_render(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "Today" in r.text
    assert "No LLM API key" not in r.text
    r = client.get("/jobs")
    assert r.status_code == 200 and "housekeeping" in r.text
    assert client.get("/static/theme.css").status_code == 200


def test_today_warnings(config: Config, session_factory: SessionFactory, fake_llm: FakeLLM) -> None:
    config.llm.api_key = ""
    config.notes.enabled = False
    app = create_app(config, llm=fake_llm)
    app.state.session_factory = session_factory
    with TestClient(app) as c:
        text = c.get("/").text
        assert "No LLM API key" in text and "Notes are disabled" in text


def test_job_fragments_and_sse(client: TestClient, session_factory: SessionFactory) -> None:
    r = client.post("/gui/jobs/housekeeping/run")
    assert r.status_code == 200 and "data-job-log-stream" in r.text
    assert client.post("/gui/jobs/nope/run").status_code == 404

    with session_factory() as session:
        run = JobRun(job_name="demo", status=JobRunStatus.SUCCESS)
        session.add(run)
        session.flush()
        first = JobLogEntry(run_id=run.id, kind="step", message="one")
        session.add(first)
        session.add(JobLogEntry(run_id=run.id, kind="llm", message="two", meta={"tier": "smart"}))
        session.commit()
        run_id, first_id = run.id, first.id

    r = client.get(f"/gui/jobs/runs/{run_id}/log")
    assert "one" in r.text and "smart" in r.text
    assert client.get("/gui/jobs/runs/999/log").status_code == 404
    assert "demo" in client.get("/gui/jobs/runs").text

    with client.stream("GET", f"/gui/jobs/runs/{run_id}/events") as r:
        body = "".join(r.iter_text())
    assert body.count("event: log") == 2
    assert body.endswith('event: done\ndata: {"html": ""}\n\n')

    with client.stream("GET", f"/gui/jobs/runs/{run_id}/events", params={"after": first_id}) as r:
        body = "".join(r.iter_text())
    assert body.count("event: log") == 1

    with client.stream("GET", "/gui/jobs/runs/999/events") as r:
        body = "".join(r.iter_text())
    assert "event: error" in body


def test_sse_live_tail(client: TestClient, session_factory: SessionFactory) -> None:
    """A running job's stream replays history then tails until closed."""
    with session_factory() as session:
        run = JobRun(job_name="live")
        session.add(run)
        session.commit()
        run_id = run.id
    hub = client.app.state.broadcast_hub  # type: ignore[attr-defined]

    def publish_then_close() -> None:
        hub.publish(run_id, {"id": 1, "kind": "info", "message": "tail", "meta": {}})
        hub.close(run_id)

    import threading

    threading.Timer(0.2, publish_then_close).start()
    with client.stream("GET", f"/gui/jobs/runs/{run_id}/events") as r:
        body = "".join(r.iter_text())
    assert "tail" in body and "event: done" in body
