import json

from sqlalchemy import text

from pure.db.init_db import init_database
from pure.db.models import Checkpoint, Project, Run, Task, ToolCall
from pure.db.repositories import (
    CheckpointRepository,
    ProjectRepository,
    RunRepository,
    TaskRepository,
    ToolCallRepository,
)


def test_sqlite_initialization(tmp_path):
    db = init_database(f"sqlite:///{tmp_path / 'pure.db'}")

    with db.session() as session:
        table_names = {row[0] for row in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}

    assert {"projects", "tasks", "runs", "tool_calls", "checkpoints"} <= table_names


def test_project_task_run_tool_call_and_checkpoint_repositories(tmp_path):
    db = init_database(f"sqlite:///{tmp_path / 'pure.db'}")

    with db.session() as session:
        projects = ProjectRepository(session)
        tasks = TaskRepository(session)
        runs = RunRepository(session)
        tool_calls = ToolCallRepository(session)
        checkpoints = CheckpointRepository(session)

        project = projects.create("Demo", tmp_path, "Repository test")
        fetched_project = projects.get(project.id)
        assert fetched_project.name == "Demo"
        assert fetched_project.root_path == str(tmp_path.resolve())

        task = tasks.create(project.id, "Inspect", "Inspect the repo.", priority=3)
        assert task.status == "created"
        task = tasks.set_status(task.id, "queued")
        assert task.status == "queued"

        run = runs.create(task.id, "session_001", run_id="run_001")
        assert run.status == "created"
        run = runs.update(
            run.id,
            status="completed",
            trace_path=str(tmp_path / ".pure" / "runs" / run.id / "trace.jsonl"),
            report_path=str(tmp_path / ".pure" / "runs" / run.id / "report.json"),
            total_steps=1,
            token_usage_summary=json.dumps({"input_tokens": 10}),
        )
        assert run.status == "completed"
        assert run.total_steps == 1

        call = tool_calls.create(
            run_id=run.id,
            step=1,
            tool_name="list_files",
            args={"path": "."},
            result_summary="README.md",
            status="ok",
            latency_ms=7,
            risk_level="low",
        )
        assert call.tool_name == "list_files"

        checkpoint = checkpoints.create(
            run_id=run.id,
            task_id=task.id,
            checkpoint_id="ckpt_001",
            checkpoint_path=str(tmp_path / ".pure" / "sessions" / "session_001.json"),
            workspace_hash="abc123",
        )
        assert checkpoint.id == "ckpt_001"

    with db.session() as session:
        assert session.query(Project).count() == 1
        assert session.query(Task).count() == 1
        assert session.query(Run).count() == 1
        assert session.query(ToolCall).count() == 1
        assert session.query(Checkpoint).count() == 1
