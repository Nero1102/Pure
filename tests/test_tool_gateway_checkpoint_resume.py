import time

from fastapi.testclient import TestClient

from pure import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from pure.db.models import ToolCall
from pure.server.main import app
from pure.server.state import runtime_service


def build_agent(tmp_path, outputs=None, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    return MiniAgent(
        model_client=FakeModelClient(outputs or []),
        workspace=workspace,
        session_store=SessionStore(tmp_path / ".pure" / "sessions"),
        approval_policy=kwargs.pop("approval_policy", "auto"),
        **kwargs,
    )


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


def test_tool_gateway_manual_approval_returns_waiting_approval(tmp_path):
    agent = build_agent(tmp_path, approval_mode="manual")

    result = agent.run_tool("write_file", {"path": "notes.txt", "content": "hello\n"})

    assert result == "waiting_approval: approval required for write_file"
    assert not (tmp_path / "notes.txt").exists()
    assert agent._last_tool_result_metadata["tool_status"] == "waiting_approval"
    assert agent._last_tool_result_metadata["approval_decision"] == "waiting_approval"


def test_tool_gateway_readonly_blocks_write_and_shell(tmp_path):
    agent = build_agent(tmp_path, approval_mode="readonly")

    write_result = agent.run_tool("write_file", {"path": "notes.txt", "content": "hello\n"})
    shell_result = agent.run_tool("run_shell", {"command": "echo hi", "timeout": 20})

    assert "readonly mode blocks tool" in write_result
    assert "readonly mode blocks tool" in shell_result
    assert not (tmp_path / "notes.txt").exists()


def test_tool_gateway_rejects_shell_parent_path_escape(tmp_path):
    agent = build_agent(tmp_path)

    result = agent.run_tool("run_shell", {"command": "type ..\\outside.txt", "timeout": 20})

    assert "escapes workspace" in result
    assert agent._last_tool_result_metadata["security_event_type"] == "path_escape"


def test_tool_audit_records_approval_decision_in_trace_and_database(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    runtime_service.configure_database(f"sqlite:///{tmp_path / 'pure.db'}")
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = TestClient(app)

    project = client.post("/projects", json={"name": "Demo", "root_path": str(tmp_path)}).json()
    runtime_config = {
        "mock_outputs": [
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
    }
    task = client.post(
        "/tasks",
        json={
            "project_id": project["id"],
            "title": "Audit tool",
            "prompt": "read",
            "runtime_config": runtime_config,
        },
    ).json()
    run = client.post(f"/tasks/{task['id']}/run", json={"runtime_config": runtime_config}).json()
    wait_for_task(client, task["id"])

    trace = client.get(f"/runs/{run['run_id']}/trace").json()
    tool_events = [event for event in trace["events"] if event["event_type"] == "tool_executed"]
    assert tool_events[-1]["payload"]["approval_decision"] == "approved"
    assert tool_events[-1]["payload"]["risk_level"] == "safe"

    with runtime_service._db().session() as db:
        call = db.query(ToolCall).one()
        assert call.tool_name == "read_file"
        assert call.risk_level == "safe"
        assert call.approval_decision == "approved"


def test_checkpoint_list_resume_and_invalid_checkpoint_validation(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    runtime_service.configure_database(f"sqlite:///{tmp_path / 'pure.db'}")
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = TestClient(app)

    project = client.post("/projects", json={"name": "Demo", "root_path": str(tmp_path)}).json()
    task = client.post(
        "/tasks",
        json={"project_id": project["id"], "title": "Checkpoint", "prompt": "dry", "dry_run": True},
    ).json()
    client.post(f"/tasks/{task['id']}/run", json={"dry_run": True})
    wait_for_task(client, task["id"])

    checkpoints_response = client.get(f"/tasks/{task['id']}/checkpoints")
    assert checkpoints_response.status_code == 200
    checkpoints = checkpoints_response.json()["checkpoints"]
    assert len(checkpoints) == 1
    assert checkpoints[0]["step"] == 0
    assert checkpoints[0]["schema_version"] == "phase1-v1"
    assert checkpoints[0]["workspace_hash"]

    resume_response = client.post(f"/tasks/{task['id']}/resume", json={"dry_run": True})
    assert resume_response.status_code == 200
    wait_for_task(client, task["id"])

    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    invalid_response = client.post(f"/tasks/{task['id']}/resume", json={"dry_run": True})
    assert invalid_response.status_code == 409
    assert "workspace hash mismatch" in invalid_response.json()["detail"]["reasons"]
