import json
from pathlib import Path

import pytest

from pure import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from pure.core.run_store import RunStore
from pure.core.task_state import TaskState


def build_workspace(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return WorkspaceContext.build(tmp_path)


def build_agent(tmp_path, outputs, **kwargs):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".pure" / "sessions")
    approval_policy = kwargs.pop("approval_policy", "auto")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy=approval_policy,
        **kwargs,
    )


def test_tool_registry_keeps_public_tool_names_stable(tmp_path):
    agent = build_agent(tmp_path, [])

    assert set(agent.tools) == {
        "list_files",
        "read_file",
        "search",
        "run_shell",
        "write_file",
        "patch_file",
        "delegate",
    }


def test_every_public_tool_exposes_schema_description_and_risky_fields(tmp_path):
    agent = build_agent(tmp_path, [])

    for name in (
        "list_files",
        "read_file",
        "search",
        "run_shell",
        "write_file",
        "patch_file",
        "delegate",
    ):
        tool = agent.tools[name]
        assert isinstance(tool["schema"], dict)
        assert tool["schema"]
        assert isinstance(tool["description"], str)
        assert tool["description"].strip()
        assert isinstance(tool["risky"], bool)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("list_files", {"path": "../outside"}),
        ("read_file", {"path": "../outside.txt", "start": 1, "end": 1}),
        ("search", {"pattern": "alpha", "path": "../outside"}),
        ("write_file", {"path": "../outside.txt", "content": "x"}),
        ("patch_file", {"path": "../outside.txt", "old_text": "a", "new_text": "b"}),
    ],
)
def test_file_tools_enforce_workspace_boundary_validation(tmp_path, name, args):
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(name, args)

    assert "path escapes workspace" in result


def test_run_artifacts_contract_generates_task_state_trace_and_report(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])

    answer = agent.ask("Complete the task")

    assert answer == "Done."
    run_dir = agent.run_store.run_dir(agent.current_task_state.run_id)
    task_state_path = run_dir / "task_state.json"
    trace_path = run_dir / "trace.jsonl"
    report_path = run_dir / "report.json"

    assert task_state_path.exists()
    assert trace_path.exists()
    assert report_path.exists()

    trace_lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert trace_lines
    for line in trace_lines:
        payload = json.loads(line)
        assert isinstance(payload, dict)
        assert "event" in payload

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == agent.current_task_state.run_id
    assert report["status"] == "completed"
    assert report["stop_reason"] == "final_answer_returned"
    assert "tool_steps" in report


def test_session_file_keeps_backward_compatible_top_level_fields(tmp_path):
    agent = build_agent(tmp_path, [])

    session = json.loads(Path(agent.session_path).read_text(encoding="utf-8"))

    assert "history" in session
    assert "memory" in session
    assert "checkpoints" in session
    assert "runtime_identity" in session
    assert "resume_state" in session


def test_legacy_session_shape_is_auto_repaired_on_load(tmp_path):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".pure" / "sessions")
    session_id = "legacy-session"
    store.path(session_id).write_text(
        json.dumps(
            {
                "id": session_id,
                "created_at": "2026-01-01T00:00:00+00:00",
                "workspace_root": str(tmp_path),
                "history": [],
                "memory": {},
                "checkpoints": [],
                "runtime_identity": [],
                "resume_state": [],
            }
        ),
        encoding="utf-8",
    )

    agent = MiniAgent.from_session(
        model_client=FakeModelClient([]),
        workspace=workspace,
        session_store=store,
        session_id=session_id,
        approval_policy="auto",
    )

    assert isinstance(agent.session["checkpoints"], dict)
    assert agent.session["checkpoints"]["current_id"] == ""
    assert agent.session["checkpoints"]["items"] == {}
    assert isinstance(agent.session["runtime_identity"], dict)
    assert isinstance(agent.session["resume_state"], dict)
    assert "working" in agent.session["memory"]
    assert "episodic_notes" in agent.session["memory"]


def test_run_store_atomic_write_replaces_corrupted_json_fully(tmp_path):
    store = RunStore(tmp_path / ".pure" / "runs")
    state = TaskState.create(run_id="run_atomic", task_id="task_atomic", user_request="Write state")
    store.start_run(state)

    task_state_path = store.task_state_path(state.run_id)
    task_state_path.write_text('{"broken": ', encoding="utf-8")
    state.finish_success("Done.")

    store.write_task_state(state)

    payload = json.loads(task_state_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run_atomic"
    assert payload["final_answer"] == "Done."


def test_run_store_uses_stable_artifact_paths(tmp_path):
    store = RunStore(tmp_path / ".pure" / "runs")
    state = TaskState.create(run_id="run_paths", task_id="task_paths", user_request="Paths")

    assert store.task_state_path(state.run_id) == tmp_path / ".pure" / "runs" / "run_paths" / "task_state.json"
    assert store.trace_path(state.run_id) == tmp_path / ".pure" / "runs" / "run_paths" / "trace.jsonl"
    assert store.report_path(state.run_id) == tmp_path / ".pure" / "runs" / "run_paths" / "report.json"


def test_missing_run_and_session_reads_raise_clear_file_errors(tmp_path):
    run_store = RunStore(tmp_path / ".pure" / "runs")
    session_store = SessionStore(tmp_path / ".pure" / "sessions")

    with pytest.raises(FileNotFoundError):
        run_store.load_task_state("run_missing")

    with pytest.raises(FileNotFoundError):
        run_store.load_report("run_missing")

    with pytest.raises(FileNotFoundError):
        session_store.load("session_missing")
