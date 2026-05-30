import json
import time

from fastapi.testclient import TestClient

from pure import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from pure.db.models import ToolCall
from pure.integrations.mcp_client import MCPClient
from pure.server.main import app
from pure.server.state import runtime_service
from pure.tools.registry import runtime_tool_specs


def build_workspace(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return WorkspaceContext.build(tmp_path)


def build_agent(tmp_path, outputs=None, **kwargs):
    workspace = build_workspace(tmp_path)
    return MiniAgent(
        model_client=FakeModelClient(outputs or []),
        workspace=workspace,
        session_store=SessionStore(tmp_path / ".pure" / "sessions"),
        approval_policy=kwargs.pop("approval_policy", "auto"),
        **kwargs,
    )


def mcp_runtime_config(server_overrides=None):
    server = {"name": "demo", "transport": "fake"}
    server.update(server_overrides or {})
    return {"mcp": {"enabled": True, "servers": [server]}}


def trace_events(agent):
    return [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]


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


def test_fake_mcp_client_lists_tools():
    client = MCPClient().connect({"name": "demo", "transport": "fake"})

    tools = client.list_tools()

    assert [tool.name for tool in tools] == ["echo", "get_build_status"]
    assert client.call_tool("echo", {"message": "hello"})["content"][0]["text"] == "hello"


def test_mcp_tools_register_as_pure_tool_specs(tmp_path):
    agent = build_agent(tmp_path, runtime_config=mcp_runtime_config())

    specs = runtime_tool_specs(agent.tools)

    assert "mcp.demo.echo" in agent.tools
    assert "mcp.demo.get_build_status" in agent.tools
    assert specs["mcp.demo.echo"].description == "Echo a message through the fake MCP server."
    assert specs["mcp.demo.echo"].input_schema["type"] == "object"
    assert specs["mcp.demo.echo"].risk_level == "medium"
    assert specs["mcp.demo.echo"].requires_approval is False


def test_runtime_can_call_registered_mcp_tools_and_trace_them(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"mcp.demo.echo","args":{"message":"hello from mcp"}}</tool>',
            '<tool>{"name":"mcp.demo.get_build_status","args":{"branch":"main"}}</tool>',
            "<final>MCP tools completed.</final>",
        ],
        runtime_config=mcp_runtime_config(),
    )

    answer = agent.ask("Call external MCP tools")

    assert answer == "MCP tools completed."
    tool_items = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert [item["name"] for item in tool_items] == [
        "mcp.demo.echo",
        "mcp.demo.get_build_status",
    ]
    assert "hello from mcp" in tool_items[0]["content"]
    assert '"status": "passed"' in tool_items[1]["content"]

    events = trace_events(agent)
    event_names = [event["event"] for event in events]
    assert "mcp_server_connected" in event_names
    assert "mcp_tools_registered" in event_names
    mcp_call_events = [event for event in events if event["event"] == "mcp_tool_called"]
    assert [event["payload"]["mcp_tool_name"] for event in mcp_call_events] == ["echo", "get_build_status"]
    for event in mcp_call_events:
        assert event["payload"]["server_name"] == "demo"
        assert event["payload"]["tool_name"].startswith("mcp.demo.")
        assert event["payload"]["status"] == "ok"
        assert isinstance(event["payload"]["latency_ms"], int)


def test_mcp_tool_is_governed_by_tool_gateway_policy(tmp_path):
    agent = build_agent(
        tmp_path,
        runtime_config=mcp_runtime_config({"tool_risk_levels": {"echo": "high"}}),
        approval_mode="manual",
    )

    result = agent.run_tool("mcp.demo.echo", {"message": "blocked"})

    assert result == "waiting_approval: approval required for mcp.demo.echo"
    assert agent._last_tool_result_metadata["tool_status"] == "waiting_approval"
    assert agent._last_tool_result_metadata["risk_level"] == "high"
    assert agent._last_tool_result_metadata["approval_decision"] == "waiting_approval"


def test_mcp_tool_failure_returns_clear_error_and_trace(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"mcp.demo.echo","args":{"message":"will fail"}}</tool>',
            "<final>Recovered from MCP failure.</final>",
        ],
        runtime_config=mcp_runtime_config({"failures": {"echo": "boom"}}),
    )

    answer = agent.ask("Call a failing MCP tool")

    assert answer == "Recovered from MCP failure."
    tool_result = [item for item in agent.session["history"] if item["role"] == "tool"][-1]["content"]
    assert "MCP tool 'echo' on server 'demo' failed: boom" in tool_result

    events = trace_events(agent)
    failed = [event for event in events if event["event"] == "mcp_tool_failed"][-1]
    assert failed["payload"]["server_name"] == "demo"
    assert failed["payload"]["mcp_tool_name"] == "echo"
    assert failed["payload"]["status"] == "failed"
    assert failed["payload"]["error"] == "boom"
    tool_event = [event for event in events if event["event"] == "tool_executed"][-1]
    assert tool_event["payload"]["tool_status"] == "error"
    assert tool_event["payload"]["tool_error_code"] == "tool_failed"


def test_mcp_tool_call_is_indexed_by_existing_tool_audit(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    runtime_service.task_jobs.clear()
    runtime_service.configure_database(f"sqlite:///{tmp_path / 'pure.db'}")
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = TestClient(app)

    project = client.post("/projects", json={"name": "Demo", "root_path": str(tmp_path)}).json()
    runtime_config = {
        "mock_outputs": [
            '<tool>{"name":"mcp.demo.echo","args":{"message":"audit me"}}</tool>',
            "<final>Done.</final>",
        ],
        **mcp_runtime_config(),
    }
    task = client.post(
        "/tasks",
        json={
            "project_id": project["id"],
            "title": "Audit MCP tool",
            "prompt": "call mcp",
        },
    ).json()

    run = client.post(f"/tasks/{task['id']}/run", json={"runtime_config": runtime_config}).json()
    wait_for_task(client, task["id"])

    trace = client.get(f"/runs/{run['run_id']}/trace").json()
    tool_events = [event for event in trace["events"] if event["event"] == "tool_executed"]
    assert tool_events[-1]["payload"]["name"] == "mcp.demo.echo"
    assert tool_events[-1]["payload"]["risk_level"] == "medium"
    assert tool_events[-1]["payload"]["approval_decision"] == "approved"

    with runtime_service._db().session() as db:
        call = db.query(ToolCall).one()
        assert call.tool_name == "mcp.demo.echo"
        assert call.risk_level == "medium"
        assert call.approval_decision == "approved"
