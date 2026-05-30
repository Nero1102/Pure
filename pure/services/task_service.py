import json
from pathlib import Path

from fastapi import HTTPException

from pure.core.runtime import PureRuntime
from pure.db.repositories import ProjectRepository, RunRepository, TaskRepository


class TaskService:
    def __init__(self, db_getter, sessions: dict, run_to_session: dict, task_jobs: dict, lock, scheduler, checkpoint_service, session_factory):
        self._db = db_getter
        self.sessions = sessions
        self.run_to_session = run_to_session
        self.task_jobs = task_jobs
        self._lock = lock
        self._scheduler = scheduler
        self._checkpoint_service = checkpoint_service
        self._session_factory = session_factory

    def create_task(
        self,
        project_id: str,
        title: str,
        prompt: str,
        priority: int = 0,
        runtime_config: dict | None = None,
        dry_run: bool = False,
    ):
        with self._db().session() as db:
            projects = ProjectRepository(db)
            tasks = TaskRepository(db)
            project = projects.get(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            task = tasks.create(project_id=project.id, title=title, prompt=prompt, priority=priority)
            return {**self._task_payload(task), "run_id": None}

    def get_task(self, task_id: str):
        with self._db().session() as db:
            task = TaskRepository(db).get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            run_id = task.runs[-1].id if task.runs else None
            return {**self._task_payload(task), "run_id": run_id}

    def get_task_status(self, task_id: str):
        with self._db().session() as db:
            task = TaskRepository(db).get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            current_run = task.runs[-1] if task.runs else None
            run_id = current_run.id if current_run else None
            checkpoint_count = len(task.checkpoints)
            run_status = current_run.status if current_run else None
        last_event = None
        current_step = 0
        if run_id:
            from pure.services.run_service import RunService
            try:
                run_service = RunService(self._db, self.sessions, self.run_to_session)
                events = run_service._load_trace_events(run_id)
            except HTTPException:
                events = []
            if events:
                last_event = events[-1]
                current_step = int(last_event.get("step", 0) or 0)
                checkpoint_count = max(
                    checkpoint_count,
                    sum(1 for event in events if event.get("event_type") == "checkpoint_created"),
                )
        return {
            "task_id": task_id,
            "status": task.status,
            "current_run": {"run_id": run_id, "status": run_status} if run_id else None,
            "current_step": current_step,
            "last_trace_event": last_event,
            "checkpoint_count": checkpoint_count,
        }

    def start_task(
        self,
        task_id: str,
        runtime_config: dict | None = None,
        dry_run: bool = False,
        dispatch: bool = True,
    ):
        from pure.server.state import SessionHandle

        config = dict(runtime_config or {})
        with self._db().session() as db:
            task = TaskRepository(db).get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            if task.status in {"queued", "running"}:
                raise HTTPException(status_code=409, detail="task is already running")
            project_root = task.project.root_path
            prompt = task.prompt
            TaskRepository(db).set_status(task_id, "queued")

        session = self._session_factory(project_root, runtime_config=config, dry_run=dry_run)
        session_id = session["session_id"]
        run_id = PureRuntime.new_run_id()
        trace_path = Path(project_root) / ".pure" / "runs" / run_id / "trace.jsonl"
        report_path = Path(project_root) / ".pure" / "runs" / run_id / "report.json"

        with self._db().session() as db:
            RunRepository(db).create(task_id=task_id, session_id=session_id, run_id=run_id, status="queued")
            RunRepository(db).update(
                run_id,
                trace_path=str(trace_path),
                report_path=str(report_path),
            )

        self._scheduler.submit_job(task_id, session_id, prompt, dry_run, run_id, dispatch)
        return {"run_id": run_id, "status": "queued"}

    def cancel_task(self, task_id: str):
        with self._lock:
            job = self.task_jobs.get(task_id)
            if job:
                job.cancel_requested = True
                if job.future is not None:
                    try:
                        job.future.cancel()
                    except RuntimeError:
                        pass
                run_id = job.run_id
                session_id = job.session_id
            else:
                run_id = None
                session_id = None
        with self._db().session() as db:
            task = TaskRepository(db).get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            if run_id is None and task.runs:
                run_id = task.runs[-1].id
                session_id = task.runs[-1].session_id
        if run_id and session_id:
            self._scheduler._mark_cancelled(task_id, run_id, session_id)
        else:
            with self._db().session() as db:
                TaskRepository(db).set_status(task_id, "cancelled")
        return {"task_id": task_id, "run_id": run_id, "status": "cancelled"}

    def resume_task(
        self,
        task_id: str,
        checkpoint_id: str | None = None,
        runtime_config: dict | None = None,
        dry_run: bool = False,
        dispatch: bool = True,
    ):
        task, checkpoint, project_root_path = self._checkpoint_service.get_resume_checkpoint(task_id, checkpoint_id)
        validation = self._checkpoint_service.validate_for_resume(
            task, checkpoint, project_root_path=project_root_path,
            runtime_config=runtime_config, dry_run=dry_run,
        )
        if not validation["valid"]:
            raise HTTPException(
                status_code=409,
                detail={"error": "invalid checkpoint", "reasons": validation["errors"]},
            )
        return self.start_task(task_id, runtime_config=runtime_config, dry_run=dry_run, dispatch=dispatch)

    @staticmethod
    def _task_payload(task):
        return {
            "id": task.id,
            "project_id": task.project_id,
            "title": task.title,
            "prompt": task.prompt,
            "status": task.status,
            "priority": task.priority,
            "created_at": _dt(task.created_at),
            "updated_at": _dt(task.updated_at),
        }


def _dt(value):
    return value.isoformat() if value else None
