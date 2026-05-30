from fastapi.testclient import TestClient

from pure.server.main import app
from pure.server.state import runtime_service


def test_health_endpoint():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_session_ask_trace_report_and_tools(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = TestClient(app)

    created = client.post(
        "/sessions",
        json={
            "project_path": str(tmp_path),
            "runtime_config": {"max_steps": 2, "approval_policy": "auto"},
            "dry_run": True,
        },
    )

    assert created.status_code == 200
    session_payload = created.json()
    assert session_payload["status"] == "created"
    session_id = session_payload["session_id"]

    session_response = client.get(f"/sessions/{session_id}")
    assert session_response.status_code == 200
    session = session_response.json()
    assert session["session_id"] == session_id
    assert session["status"] == "idle"
    assert session["checkpoint_count"] == 0
    assert session["metadata"]["workspace_root"] == str(tmp_path)

    asked = client.post(
        f"/sessions/{session_id}/ask",
        json={"prompt": "Inspect the repo without calling a real model.", "dry_run": True},
    )

    assert asked.status_code == 200
    run_payload = asked.json()
    assert run_payload["status"] == "completed"
    assert run_payload["run_id"].startswith("run_")
    assert run_payload["report_path"].endswith("report.json")

    trace_response = client.get(f"/runs/{run_payload['run_id']}/trace")
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["run_id"] == run_payload["run_id"]
    assert [event["event"] for event in trace["events"]][-1] == "run_finished"

    report_response = client.get(f"/runs/{run_payload['run_id']}/report")
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["run_id"] == run_payload["run_id"]
    assert report["status"] == "completed"
    assert report["final_answer"] == "Dry run: no LLM API called."

    tools_response = client.get("/tools")
    assert tools_response.status_code == 200
    tools = tools_response.json()
    tool_names = {tool["name"] for tool in tools}
    assert {"list_files", "read_file", "search", "run_shell", "write_file", "patch_file", "delegate"} <= tool_names
    assert any(tool["name"] == "run_shell" and tool["risk_level"] == "high" for tool in tools)
