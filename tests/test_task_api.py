import time

from fastapi.testclient import TestClient

from pure.db.models import Checkpoint, Run, Task
from pure.server.main import app
from pure.server.state import runtime_service


def wait_for_task(client, task_id, terminal=("completed", "failed", "cancelled"), timeout=5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/tasks/{task_id}/status")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in terminal:
            return last
        time.sleep(0.05)
    raise AssertionError(f"task did not reach {terminal}: {last}")


def test_project_task_run_api_and_dry_run_database_records(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    runtime_service.configure_database(f"sqlite:///{tmp_path / 'pure.db'}")
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        json={"name": "Demo", "root_path": str(tmp_path), "description": "API test"},
    )
    assert project_response.status_code == 200
    project = project_response.json()
    assert project["root_path"] == str(tmp_path)

    fetched_project = client.get(f"/projects/{project['id']}")
    assert fetched_project.status_code == 200
    assert fetched_project.json()["name"] == "Demo"

    task_response = client.post(
        "/tasks",
        json={
            "project_id": project["id"],
            "title": "Dry run inspect",
            "prompt": "Inspect without a real model.",
            "priority": 2,
            "runtime_config": {"max_steps": 2, "approval_policy": "auto"},
            "dry_run": True,
        },
    )
    assert task_response.status_code == 200
    task = task_response.json()
    assert task["status"] == "created"
    assert task["run_id"] is None

    run_response = client.post(
        f"/tasks/{task['id']}/run",
        json={"runtime_config": {"max_steps": 2, "approval_policy": "auto"}, "dry_run": True},
    )
    assert run_response.status_code == 200
    run_started = run_response.json()
    assert run_started["status"] == "queued"
    assert run_started["run_id"].startswith("run_")

    status = wait_for_task(client, task["id"])
    assert status["status"] == "completed"
    assert status["current_run"]["run_id"] == run_started["run_id"]
    assert status["checkpoint_count"] == 1
    assert status["last_trace_event"]["event_type"] == "run_completed"

    fetched_task = client.get(f"/tasks/{task['id']}")
    assert fetched_task.status_code == 200
    assert fetched_task.json()["status"] == "completed"

    fetched_run_response = client.get(f"/runs/{run_started['run_id']}")
    assert fetched_run_response.status_code == 200
    run = fetched_run_response.json()
    assert run["status"] == "completed"
    assert run["task_id"] == task["id"]
    assert run["trace_path"].endswith("trace.jsonl")
    assert run["report_path"].endswith("report.json")

    trace_response = client.get(f"/runs/{run_started['run_id']}/trace")
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["events"][-1]["event"] == "run_finished"
    for event in trace["events"]:
        assert {"run_id", "step", "event_type", "timestamp", "payload", "latency_ms", "status"} <= set(event)
    event_types = [event["event_type"] for event in trace["events"]]
    assert "run_started" in event_types
    assert "context_built" in event_types
    assert "model_called" in event_types
    assert "checkpoint_created" in event_types
    assert "run_completed" in event_types

    report_response = client.get(f"/runs/{run_started['run_id']}/report")
    assert report_response.status_code == 200
    assert report_response.json()["final_answer"] == "Dry run: no LLM API called."

    with runtime_service._db().session() as session:
        assert session.query(Task).count() == 1
        assert session.query(Run).count() == 1
        assert session.query(Checkpoint).count() == 1


def test_task_cancel_marks_task_run_and_trace(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    runtime_service.configure_database(f"sqlite:///{tmp_path / 'pure.db'}")
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = TestClient(app)

    project = client.post("/projects", json={"name": "Demo", "root_path": str(tmp_path)}).json()
    task = client.post(
        "/tasks",
        json={"project_id": project["id"], "title": "Cancel me", "prompt": "Please run.", "dry_run": True},
    ).json()
    run = client.post(f"/tasks/{task['id']}/run", json={"dry_run": True}).json()

    cancel_response = client.post(f"/tasks/{task['id']}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    status = wait_for_task(client, task["id"], terminal=("cancelled",))
    assert status["status"] == "cancelled"

    trace = client.get(f"/runs/{run['run_id']}/trace").json()
    assert trace["events"][-1]["event_type"] == "run_cancelled"


def test_cancel_requested_run_trace_is_terminal_even_before_db_run_status_updates(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    runtime_service.configure_database(f"sqlite:///{tmp_path / 'pure.db'}")
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = TestClient(app)

    project = client.post("/projects", json={"name": "Demo", "root_path": str(tmp_path)}).json()
    task = client.post(
        "/tasks",
        json={"project_id": project["id"], "title": "Cancel race", "prompt": "Please run.", "dry_run": True},
    ).json()
    run = client.post(f"/tasks/{task['id']}/run", json={"dry_run": True}).json()

    with runtime_service._lock:
        runtime_service.task_jobs[task["id"]].cancel_requested = True

    trace = client.get(f"/runs/{run['run_id']}/trace").json()
    assert trace["events"][-1]["event_type"] == "run_cancelled"
