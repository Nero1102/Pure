import json
import os
from pathlib import Path

from fastapi import HTTPException

from pure.core.models import FakeModelClient
from pure.core.run_store import RunStore
from pure.core.runtime import PureRuntime
from pure.db.repositories import (
    CheckpointRepository,
    RunRepository,
    ToolCallRepository,
)
from pure.services.trace_service import TraceService


class RunService:
    def __init__(self, db_getter, sessions: dict, run_to_session: dict):
        self._db = db_getter
        self.sessions = sessions
        self.run_to_session = run_to_session

    def get_run(self, run_id: str):
        with self._db().session() as db:
            run = RunRepository(db).get(run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="run not found")
            return self._run_payload(run)

    def get_trace(self, run_id: str):
        self._ensure_cancel_trace(run_id)
        events = self._load_trace_events(run_id)
        return {"run_id": run_id, "events": events}

    def get_report(self, run_id: str):
        path = self._artifact_path_for_run(run_id, "report")
        if not path.exists():
            raise HTTPException(status_code=404, detail="report not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def run_task(self, session_id: str, prompt: str, dry_run: bool = False, task_id: str | None = None, run_id: str | None = None):
        handle = self._session_handle(session_id)
        agent = handle.agent
        if dry_run and (
            not isinstance(agent.model_client, FakeModelClient)
            or getattr(agent.model_client, "is_default_dry_run", False)
        ):
            agent.model_client = FakeModelClient(["<final>Dry run: no LLM API called.</final>"])
            agent.model_client.is_default_dry_run = True
        handle.status = "running"
        try:
            agent.ask(prompt, task_id=task_id, run_id=run_id)
        except Exception:
            handle.status = "failed"
            raise
        task_state = agent.current_task_state
        if task_state is None:
            handle.status = "failed"
            raise HTTPException(status_code=500, detail="runtime did not produce a task state")
        handle.status = task_state.status
        handle.runs.append(task_state.run_id)
        self.run_to_session[task_state.run_id] = session_id
        return {
            "run_id": task_state.run_id,
            "status": task_state.status,
            "report_path": str(agent.run_store.report_path(task_state.run_id)),
        }

    def index_run_artifacts(self, db, run_id: str, task_id: str, agent: PureRuntime, _token_usage_summary, _utc_now):
        report_path = agent.run_store.report_path(run_id)
        trace_path = agent.run_store.trace_path(run_id)
        report = {}
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        status = str(report.get("status") or agent.current_task_state.status)
        token_usage = _token_usage_summary(report.get("prompt_metadata", {}))
        RunRepository(db).update(
            run_id,
            status="completed" if status == "completed" else "failed",
            ended_at=_utc_now(),
            trace_path=str(trace_path),
            report_path=str(report_path),
            error="" if status == "completed" else str(report.get("stop_reason", "")),
            total_steps=int(report.get("tool_steps", 0) or 0),
            token_usage_summary=json.dumps(token_usage, sort_keys=True, ensure_ascii=True),
        )
        self._index_tool_calls(db, run_id, trace_path)
        checkpoint_id = str(report.get("checkpoint_id", "") or "")
        if checkpoint_id:
            checkpoint = agent.session.get("checkpoints", {}).get("items", {}).get(checkpoint_id, {})
            CheckpointRepository(db).create(
                run_id=run_id,
                task_id=task_id,
                checkpoint_id=checkpoint_id,
                checkpoint_path=str(agent.session_path),
                workspace_hash=str(
                    checkpoint.get("workspace_hash", "")
                    or checkpoint.get("runtime_identity", {}).get("workspace_fingerprint", "")
                    or getattr(agent.prefix_state, "workspace_fingerprint", "")
                ),
                step=int(checkpoint.get("step", report.get("tool_steps", 0)) or 0),
                memory_snapshot=checkpoint.get("memory_snapshot", {}),
                last_trace_event=checkpoint.get("last_trace_event", {}),
                runtime_metadata={
                    **dict(checkpoint.get("runtime_metadata", {}) or {}),
                    "runtime_identity": checkpoint.get("runtime_identity", {}),
                },
                schema_version=str(checkpoint.get("schema_version", "")),
            )

    def append_cancel_trace(self, run_id: str):
        try:
            path = self._artifact_path_for_run(run_id, "trace")
        except HTTPException:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                events = TraceService.load_events(path)
            except Exception:
                events = []
            if events and events[-1].get("event_type") == "run_cancelled":
                return
        task_state = type("TraceTaskState", (), {"run_id": run_id, "tool_steps": 0, "status": "cancelled"})()
        event = TraceService().format_event(task_state, "run_cancelled", {"status": "cancelled"})
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True))
            handle.write("\n")

    def _ensure_cancel_trace(self, run_id: str):
        with self._db().session() as db:
            run = RunRepository(db).get(run_id)
            status = run.status if run is not None else None
        if status == "cancelled":
            self.append_cancel_trace(run_id)

    def append_failure_trace(self, run_id: str, session_id: str, error: str):
        if session_id in self.sessions:
            task_state = self.sessions[session_id].agent.current_task_state
            if task_state is not None and task_state.run_id == run_id:
                task_state.status = "failed"
                self.sessions[session_id].agent.emit_trace(task_state, "run_failed", {"status": "failed", "error": error})
                return
        try:
            path = self._artifact_path_for_run(run_id, "trace")
        except HTTPException:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        task_state = type("TraceTaskState", (), {"run_id": run_id, "tool_steps": 0, "status": "failed"})()
        event = TraceService().format_event(task_state, "run_failed", {"status": "failed", "error": error})
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True))
            handle.write("\n")

    def _load_trace_events(self, run_id: str):
        path = self._artifact_path_for_run(run_id, "trace")
        if not path.exists():
            raise HTTPException(status_code=404, detail="trace not found")
        return TraceService.load_events(path)

    def _artifact_path_for_run(self, run_id: str, kind: str):
        session_id = self.run_to_session.get(run_id)
        if session_id is not None:
            run_store = self.sessions[session_id].agent.run_store
            return run_store.trace_path(run_id) if kind == "trace" else run_store.report_path(run_id)
        with self._db().session() as db:
            run = RunRepository(db).get(run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="run not found")
            raw_path = run.trace_path if kind == "trace" else run.report_path
            if not raw_path:
                raise HTTPException(status_code=404, detail=f"{kind} not found")
            path = Path(raw_path)
            project_root = Path(run.task.project.root_path).resolve()
            resolved = path.resolve()
            if os.path.commonpath([str(project_root), str(resolved)]) != str(project_root):
                raise HTTPException(status_code=500, detail=f"{kind} path escapes project root")
            return resolved

    def _run_store_for_run(self, run_id: str):
        session_id = self.run_to_session.get(run_id)
        if session_id is None:
            raise HTTPException(status_code=404, detail="run not found")
        return self.sessions[session_id].agent.run_store

    def _session_handle(self, session_id: str):
        handle = self.sessions.get(session_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="session not found")
        return handle

    @staticmethod
    def _index_tool_calls(db, run_id: str, trace_path: Path):
        if not trace_path.exists():
            return
        repo = ToolCallRepository(db)
        step = 0
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("event") != "tool_executed":
                continue
            payload = event.get("payload", event)
            step += 1
            repo.create(
                run_id=run_id,
                step=step,
                tool_name=payload.get("name", ""),
                args=payload.get("args", {}),
                result_summary=payload.get("result", ""),
                status=payload.get("tool_status", "ok"),
                latency_ms=int(event.get("latency_ms", payload.get("duration_ms", 0)) or 0),
                risk_level=payload.get("risk_level", ""),
                approval_decision=payload.get("approval_decision", ""),
            )

    @staticmethod
    def _run_payload(run):
        try:
            token_usage = json.loads(run.token_usage_summary or "{}")
        except json.JSONDecodeError:
            token_usage = {}
        return {
            "id": run.id,
            "task_id": run.task_id,
            "session_id": run.session_id,
            "status": run.status,
            "started_at": _dt(run.started_at),
            "ended_at": _dt(run.ended_at),
            "trace_path": run.trace_path,
            "report_path": run.report_path,
            "error": run.error,
            "total_steps": run.total_steps,
            "token_usage_summary": token_usage,
        }


def _dt(value):
    return value.isoformat() if value else None
