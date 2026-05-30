import json

from pure.core.run_store import RunStore
from pure.core.session_store import SessionStore
from pure.core.task_state import TaskState
from pure.utils.migration import migrate_legacy_pico_artifacts


def test_migrates_legacy_pico_artifacts_to_pure(tmp_path, capsys):
    legacy_root = tmp_path / ".pico"
    (legacy_root / "runs").mkdir(parents=True)
    (legacy_root / "sessions").mkdir(parents=True)
    (legacy_root / "memory" / "topics").mkdir(parents=True)

    # Seed a legacy session.
    legacy_sessions = SessionStore(legacy_root / "sessions")
    session = {
        "id": "sess_legacy",
        "history": [],
        "memory": {"working": {"task_summary": "", "recent_files": []}, "episodic_notes": [], "file_summaries": {}},
        "checkpoints": {"schema_version": 1, "current_id": "", "items": {}},
        "runtime_identity": {},
        "resume_state": {},
    }
    legacy_sessions.save(session)

    # Seed a legacy run.
    legacy_runs = RunStore(legacy_root / "runs")
    state = TaskState.create(task_id="task_legacy", user_request="hello", run_id="run_legacy")
    legacy_runs.start_run(state)
    legacy_runs.append_trace(state, {"event": "run_started", "created_at": "t0"})
    legacy_runs.write_report(state, {"run_id": state.run_id, "status": state.status, "stop_reason": ""})

    # Seed durable memory.
    (legacy_root / "memory" / "MEMORY.md").write_text("# Legacy\n", encoding="utf-8")
    (legacy_root / "memory" / "topics" / "project-conventions.md").write_text("x\n", encoding="utf-8")

    result = migrate_legacy_pico_artifacts(tmp_path)
    out = capsys.readouterr().out
    assert result["migrated"] is True
    assert "[Pure] Migrated legacy pico artifacts to .pure/" in out

    # New roots exist and legacy content is readable.
    pure_root = tmp_path / ".pure"
    assert (pure_root / "sessions" / "sess_legacy.json").is_file()
    assert (pure_root / "runs" / "run_legacy" / "trace.jsonl").is_file()
    assert (pure_root / "runs" / "run_legacy" / "report.json").is_file()
    assert (pure_root / "memory" / "MEMORY.md").is_file()

    new_sessions = SessionStore(pure_root / "sessions")
    loaded = new_sessions.load("sess_legacy")
    assert loaded["id"] == "sess_legacy"

    new_runs = RunStore(pure_root / "runs")
    loaded_report = new_runs.load_report("run_legacy")
    assert loaded_report["run_id"] == "run_legacy"

    trace_lines = (pure_root / "runs" / "run_legacy" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert trace_lines
    json.loads(trace_lines[0])

