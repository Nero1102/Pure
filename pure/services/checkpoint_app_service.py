import json

from fastapi import HTTPException

from pure.core.runtime import PureRuntime
from pure.core.workspace import WorkspaceContext
from pure.db.repositories import CheckpointRepository, TaskRepository


class CheckpointAppService:
    def __init__(self, db_getter, model_client_factory):
        self._db = db_getter
        self._model_client = model_client_factory

    def list_task_checkpoints(self, task_id: str):
        with self._db().session() as db:
            task = TaskRepository(db).get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            checkpoints = CheckpointRepository(db).list_for_task(task_id)
            return {
                "task_id": task_id,
                "checkpoints": [self._checkpoint_payload(checkpoint) for checkpoint in checkpoints],
            }

    def validate_for_resume(self, task, checkpoint, project_root_path, runtime_config=None, dry_run=False):
        return _validate_checkpoint_for_resume(
            task, checkpoint, project_root_path, self._model_client, runtime_config, dry_run,
        )

    def get_resume_checkpoint(self, task_id: str, checkpoint_id: str | None = None):
        with self._db().session() as db:
            task = TaskRepository(db).get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            project_root_path = task.project.root_path
            checkpoints = CheckpointRepository(db).list_for_task(task_id)
            if not checkpoints:
                raise HTTPException(status_code=400, detail="no checkpoint available for task")
            checkpoint = None
            if checkpoint_id:
                checkpoint = CheckpointRepository(db).get(checkpoint_id)
                if checkpoint is None or checkpoint.task_id != task_id:
                    raise HTTPException(status_code=404, detail="checkpoint not found")
            else:
                checkpoint = checkpoints[-1]
            return task, checkpoint, project_root_path

    @staticmethod
    def _checkpoint_payload(checkpoint):
        try:
            runtime_metadata = json.loads(checkpoint.runtime_metadata or "{}")
        except json.JSONDecodeError:
            runtime_metadata = {}
        try:
            last_trace_event = json.loads(checkpoint.last_trace_event or "{}")
        except json.JSONDecodeError:
            last_trace_event = {}
        return {
            "id": checkpoint.id,
            "run_id": checkpoint.run_id,
            "task_id": checkpoint.task_id,
            "checkpoint_path": checkpoint.checkpoint_path,
            "workspace_hash": checkpoint.workspace_hash,
            "step": checkpoint.step,
            "schema_version": checkpoint.schema_version,
            "last_trace_event": last_trace_event,
            "runtime_metadata": runtime_metadata,
            "created_at": _dt(checkpoint.created_at),
        }


def _validate_checkpoint_for_resume(task, checkpoint, project_root_path, model_client_factory, runtime_config=None, dry_run=False):
    errors = []
    if checkpoint.schema_version != PureRuntime.CHECKPOINT_SCHEMA_VERSION:
        errors.append("checkpoint schema mismatch")
    current_workspace_hash = WorkspaceContext.build(project_root_path).fingerprint()
    if checkpoint.workspace_hash and checkpoint.workspace_hash != current_workspace_hash:
        errors.append("workspace hash mismatch")
    runtime_metadata = {}
    try:
        runtime_metadata = json.loads(checkpoint.runtime_metadata or "{}")
    except json.JSONDecodeError:
        errors.append("checkpoint runtime metadata is invalid")
    runtime_identity = dict(runtime_metadata.get("runtime_identity", {}) or {})
    current_identity = _runtime_identity_for_resume(model_client_factory, dict(runtime_config or {}), dry_run=dry_run)
    for key in ("model_client", "model"):
        saved_value = runtime_identity.get(key)
        if saved_value and saved_value != current_identity.get(key):
            errors.append(f"runtime identity mismatch: {key}")
    return {"valid": not errors, "errors": errors}


def _runtime_identity_for_resume(model_client_factory, config: dict, dry_run: bool):
    model_client = model_client_factory(config, dry_run=dry_run)
    return {
        "model_client": model_client.__class__.__name__,
        "model": str(getattr(model_client, "model", "")),
    }


def _dt(value):
    return value.isoformat() if value else None
