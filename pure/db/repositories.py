import json
from pathlib import Path
from uuid import uuid4

from . import models


TASK_STATUSES = {"created", "queued", "running", "completed", "failed", "cancelled"}
RUN_STATUSES = {"created", "queued", "running", "completed", "failed", "cancelled"}


def _id(prefix):
    return f"{prefix}_{uuid4().hex[:12]}"


def _require_status(status, allowed):
    if status not in allowed:
        raise ValueError(f"invalid status: {status}")
    return status


class ProjectRepository:
    def __init__(self, db):
        self.db = db

    def create(self, name, root_path, description="", project_id=None):
        project = models.Project(
            id=project_id or _id("project"),
            name=str(name),
            root_path=str(Path(root_path).resolve()),
            description=str(description or ""),
        )
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        return project

    def get(self, project_id):
        return self.db.get(models.Project, project_id)


class TaskRepository:
    def __init__(self, db):
        self.db = db

    def create(self, project_id, title, prompt, priority=0, task_id=None):
        task = models.Task(
            id=task_id or _id("task"),
            project_id=str(project_id),
            title=str(title),
            prompt=str(prompt),
            status="created",
            priority=int(priority),
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get(self, task_id):
        return self.db.get(models.Task, task_id)

    def set_status(self, task_id, status):
        task = self.get(task_id)
        if task is None:
            return None
        task.status = _require_status(status, TASK_STATUSES)
        task.updated_at = models.utc_now()
        self.db.commit()
        self.db.refresh(task)
        return task


class RunRepository:
    def __init__(self, db):
        self.db = db

    def create(self, task_id, session_id, run_id=None, status="created"):
        run = models.Run(
            id=run_id or _id("run"),
            task_id=str(task_id),
            session_id=str(session_id),
            status=_require_status(status, RUN_STATUSES),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get(self, run_id):
        return self.db.get(models.Run, run_id)

    def update(self, run_id, **fields):
        run = self.get(run_id)
        if run is None:
            return None
        if "status" in fields:
            fields["status"] = _require_status(fields["status"], RUN_STATUSES)
        for key, value in fields.items():
            setattr(run, key, value)
        self.db.commit()
        self.db.refresh(run)
        return run


class ToolCallRepository:
    def __init__(self, db):
        self.db = db

    def create(
        self,
        run_id,
        step,
        tool_name,
        args,
        result_summary="",
        status="ok",
        latency_ms=0,
        risk_level="",
        approval_decision="",
        tool_call_id=None,
    ):
        call = models.ToolCall(
            id=tool_call_id or _id("toolcall"),
            run_id=str(run_id),
            step=int(step),
            tool_name=str(tool_name),
            args_json=json.dumps(args or {}, sort_keys=True, ensure_ascii=True),
            result_summary=str(result_summary or ""),
            status=str(status or "ok"),
            latency_ms=int(latency_ms or 0),
            risk_level=str(risk_level or ""),
            approval_decision=str(approval_decision or ""),
        )
        self.db.add(call)
        self.db.commit()
        self.db.refresh(call)
        return call


class CheckpointRepository:
    def __init__(self, db):
        self.db = db

    def create(
        self,
        run_id,
        task_id,
        checkpoint_path,
        workspace_hash="",
        checkpoint_id=None,
        step=0,
        memory_snapshot=None,
        last_trace_event=None,
        runtime_metadata=None,
        schema_version="",
    ):
        checkpoint = models.Checkpoint(
            id=checkpoint_id or _id("ckpt"),
            run_id=str(run_id),
            task_id=str(task_id),
            checkpoint_path=str(checkpoint_path),
            workspace_hash=str(workspace_hash or ""),
            step=int(step or 0),
            memory_snapshot=json.dumps(memory_snapshot or {}, sort_keys=True, ensure_ascii=True),
            last_trace_event=json.dumps(last_trace_event or {}, sort_keys=True, ensure_ascii=True),
            runtime_metadata=json.dumps(runtime_metadata or {}, sort_keys=True, ensure_ascii=True),
            schema_version=str(schema_version or ""),
        )
        self.db.add(checkpoint)
        self.db.commit()
        self.db.refresh(checkpoint)
        return checkpoint

    def list_for_task(self, task_id):
        return (
            self.db.query(models.Checkpoint)
            .filter(models.Checkpoint.task_id == str(task_id))
            .order_by(models.Checkpoint.created_at.asc())
            .all()
        )

    def get(self, checkpoint_id):
        return self.db.get(models.Checkpoint, checkpoint_id)
