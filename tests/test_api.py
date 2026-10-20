from __future__ import annotations

from fastapi.testclient import TestClient


def _person(client: TestClient, name: str, **kw: object) -> int:
    r = client.post("/api/people", json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _thread(client: TestClient, title: str, **kw: object) -> int:
    r = client.post("/api/threads", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def test_status(client: TestClient) -> None:
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["threads_open"] == 0
    assert body["llm_enabled"] is True


def test_people_endpoints(client: TestClient) -> None:
    me = _person(client, "Alex", is_self=True)
    assert client.post("/api/people", json={"name": "Alex"}).status_code == 400
    r = client.patch(f"/api/people/{me}", json={"aliases": ["E"]})
    assert r.json()["aliases"] == ["E"]
    assert client.patch("/api/people/999", json={}).status_code == 404
    assert len(client.get("/api/people").json()) == 1
    assert client.delete(f"/api/people/{me}").status_code == 204
    assert client.delete(f"/api/people/{me}").status_code == 404


def test_label_endpoints(client: TestClient) -> None:
    r = client.post("/api/labels", json={"name": "Dash", "jira_projects": ["dboard3"]})
    assert r.status_code == 201
    lid = r.json()["id"]
    assert r.json()["jira_projects"] == ["DBOARD3"]
    assert client.post("/api/labels", json={"name": "dash"}).status_code == 400
    r = client.patch(f"/api/labels/{lid}", json={"description": "x"})
    assert r.json()["description"] == "x"
    assert client.patch("/api/labels/999", json={}).status_code == 400
    assert len(client.get("/api/labels").json()) == 1
    assert client.delete(f"/api/labels/{lid}").status_code == 204
    assert client.delete(f"/api/labels/{lid}").status_code == 404


def test_thread_lifecycle(client: TestClient) -> None:
    me = _person(client, "Alex", is_self=True)
    root = _thread(client, "Root", labels=["dash"], jira_keys=["AB-1"])
    child = _thread(client, "Child", parent_id=root, kind="agreement", owner_id=me)
    assert client.post("/api/threads", json={"title": " "}).status_code == 400

    r = client.get("/api/threads")
    assert {t["id"] for t in r.json()} == {root, child}
    assert client.get("/api/threads", params={"roots_only": "true"}).json()[0]["id"] == root
    assert client.get("/api/threads", params={"kind": "agreement"}).json()[0]["id"] == child
    assert client.get("/api/threads", params={"status": "bogus"}).status_code == 400
    assert client.get("/api/threads", params={"jira": "ab-"}).json()[0]["id"] == root
    lid = client.get("/api/labels").json()[0]["id"]
    assert len(client.get("/api/threads", params={"label_id": [lid]}).json()) == 2

    r = client.get(f"/api/threads/{child}")
    assert r.json()["labels"] == ["dash"]
    assert client.get("/api/threads/999").status_code == 404

    r = client.patch(f"/api/threads/{child}", json={"status": "done", "owner_id": None})
    assert r.json()["status"] == "done" and r.json()["owner_id"] is None
    assert client.patch("/api/threads/999", json={}).status_code == 404

    other = _thread(client, "Other")
    r = client.put(f"/api/threads/{child}/parent", json={"parent_id": other})
    assert r.json()["parent_id"] == other
    assert client.put(f"/api/threads/{root}/parent", json={"parent_id": root}).status_code == 400

    r = client.put(f"/api/threads/{root}/labels", json={"labels": ["x", "y"]})
    assert r.json()["labels"] == ["x", "y"]
    assert client.put("/api/threads/999/labels", json={"labels": []}).status_code == 404

    r = client.post(f"/api/threads/{root}/jira", json={"keys": ["ab-2"]})
    assert r.json()["jira_keys"] == ["AB-1", "AB-2"]
    assert client.post("/api/threads/999/jira", json={"keys": []}).status_code == 404
    r = client.delete(f"/api/threads/{root}/jira/AB-1")
    assert r.json()["jira_keys"] == ["AB-2"]
    assert client.delete("/api/threads/999/jira/AB-1").status_code == 404

    r = client.post(f"/api/threads/{root}/links", json={"other_id": other})
    assert r.json()["related_ids"] == [other]
    assert client.post(f"/api/threads/{root}/links", json={"other_id": root}).status_code == 400
    r = client.delete(f"/api/threads/{root}/links/{other}")
    assert r.json()["related_ids"] == []
    assert client.delete(f"/api/threads/999/links/{other}").status_code == 404

    r = client.post(f"/api/threads/{root}/merge", json={"from_id": other})
    assert r.status_code == 200
    assert client.get(f"/api/threads/{other}").status_code == 404
    assert client.post(f"/api/threads/{root}/merge", json={"from_id": root}).status_code == 400

    assert client.post(f"/api/threads/{root}/confirm").json()["provisional"] is False
    assert client.post("/api/threads/999/confirm").status_code == 404

    assert client.get(f"/api/threads/{root}/mentions").json() == []
    assert client.get("/api/threads/999/mentions").status_code == 404

    assert client.delete(f"/api/threads/{root}").status_code == 204
    assert client.delete(f"/api/threads/{root}").status_code == 404


def test_mentions_and_changes(client: TestClient, session_factory: object) -> None:
    from insights.core import mentions
    from insights.core.mentions import MentionInput

    with session_factory() as session:  # type: ignore[operator]
        a, _ = mentions.upsert_mention(session, MentionInput(path="a.md", summary="alpha"))
        b, _ = mentions.upsert_mention(session, MentionInput(path="b.md", summary="beta"))
        session.commit()
        a_id, b_id = a.id, b.id

    t = _thread(client, "T")
    assert len(client.get("/api/mentions").json()) == 2
    assert client.get("/api/mentions", params={"state": "bogus"}).status_code == 400

    r = client.put(f"/api/mentions/{a_id}/thread", json={"thread_id": t})
    assert r.json()["thread_id"] == t and r.json()["state"] == "confirmed"
    assert client.put(f"/api/mentions/{a_id}/thread", json={"thread_id": 999}).status_code == 400
    assert client.post(f"/api/mentions/{b_id}/confirm").json()["state"] == "confirmed"
    assert client.post("/api/mentions/999/confirm").status_code == 404
    assert client.post(f"/api/mentions/{b_id}/set-aside").json()["state"] == "set_aside"
    assert client.post("/api/mentions/999/set-aside").status_code == 404

    r = client.post(
        "/api/mentions/new-thread", json={"title": "From mentions", "mention_ids": [a_id, b_id]}
    )
    assert r.status_code == 201
    assert r.json()["mention_count"] == 2
    assert (
        client.post(
            "/api/mentions/new-thread", json={"title": "x", "mention_ids": [999]}
        ).status_code
        == 400
    )

    changes = client.get("/api/changes", params={"entity": "mention", "entity_id": a_id}).json()
    assert changes
    latest = changes[0]
    r = client.post(f"/api/changes/{latest['id']}/undo")
    assert r.json()["reverted_at"] is not None
    assert client.post("/api/changes/999/undo").status_code == 404
    # the undone change was the move from t to the new thread, so a is back on t
    assert [m["id"] for m in client.get(f"/api/threads/{t}/mentions").json()] == [a_id]


def test_jobs_endpoints(client: TestClient) -> None:
    assert "housekeeping" in client.get("/api/jobs").json()
    r = client.post("/api/jobs/housekeeping/run")
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "success"
    assert client.post("/api/jobs/nope/run").status_code == 404
    assert client.post("/api/jobs/nope/run", params={"wait": "false"}).status_code == 404

    r = client.post("/api/jobs/housekeeping/run", params={"wait": "false", "full": "true"})
    assert "run_id" in r.json()

    runs = client.get("/api/jobs/runs", params={"job_name": "housekeeping"}).json()
    assert len(runs) >= 1
    assert client.get(f"/api/jobs/runs/{run['id']}").json()["job_name"] == "housekeeping"
    assert client.get("/api/jobs/runs/999").status_code == 404
    log = client.get(f"/api/jobs/runs/{run['id']}/log").json()
    assert log[0]["kind"] == "step"
    assert client.get("/api/jobs/runs/999/log").status_code == 404

    assert client.post(f"/api/jobs/runs/{run['id']}/revert").json() == {"reverted": 0}
    assert client.post("/api/jobs/runs/999/revert").status_code == 404
