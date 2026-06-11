from datetime import datetime, timezone


class TaskScheduler:
    def __init__(self, db_getter, sessions: dict, run_to_session: dict, task_jobs: dict, lock, executor):
        self._db = db_getter
        self.sessions = sessions
        self.run_to_session = run_to_session
        self.task_jobs = task_jobs
        self._lock = lock
        self._executor = executor
        self._run_service = None
        self._task_repo = None
        self._run_repo = None

    def set_run_service(self, run_service):
        self._run_service = run_service

    def submit_job(self, task_id, session_id, prompt, dry_run, run_id, dispatch):
        from pure.server.state import TaskJob

        job = TaskJob(run_id=run_id, session_id=session_id, prompt=prompt, dry_run=dry_run)
        with self._lock:
            self.task_jobs[task_id] = job
        if dispatch:
            job.future = self._executor.submit(self._run_task_job, task_id, session_id, prompt, dry_run, run_id)
        return job

    def dispatch_task_asyncio(self, task_id: str, loop):
        with self._lock:
            job = self.task_jobs.get(task_id)
        if job is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="task job not found")
        job.future = loop.run_in_executor(
            self._executor,
            self._run_task_job,
            task_id,
            job.session_id,
            job.prompt,
            job.dry_run,
            job.run_id,
        )
        return job.future

    def _run_task_job(self, task_id: str, session_id: str, prompt: str, dry_run: bool, run_id: str):
        from pure.db.repositories import TaskRepository, RunRepository

        with self._lock:
            job = self.task_jobs.get(task_id)
            if job and job.cancel_requested:
                self._mark_cancelled(task_id, run_id, session_id)
                return
        with self._db().session() as db:
            TaskRepository(db).set_status(task_id, "running")
            RunRepository(db).update(run_id, status="running", started_at=_utc_now())
        with self._lock:
            job = self.task_jobs.get(task_id)
            if job and job.cancel_requested:
                self._mark_cancelled(task_id, run_id, session_id)
                return
        try:
            result = self._run_service.run_task(session_id=session_id, prompt=prompt, dry_run=dry_run, task_id=task_id, run_id=run_id)
        except Exception as exc:
            with self._lock:
                job = self.task_jobs.get(task_id)
                cancelled = bool(job and job.cancel_requested)
            if cancelled:
                self._mark_cancelled(task_id, run_id, session_id)
                return
            self._run_service.append_failure_trace(run_id, session_id, str(exc))
            with self._db().session() as db:
                TaskRepository(db).set_status(task_id, "failed")
                RunRepository(db).update(run_id, status="failed", ended_at=_utc_now(), error=str(exc))
            raise

        with self._lock:
            job = self.task_jobs.get(task_id)
            cancelled = bool(job and job.cancel_requested)
        if cancelled:
            self._mark_cancelled(task_id, run_id, session_id)
            return

        status = "completed" if result["status"] == "completed" else "failed"
        with self._db().session() as db:
            TaskRepository(db).set_status(task_id, status)
            self._run_service.index_run_artifacts(
                db, run_id, task_id, self.sessions[session_id].agent,
                _token_usage_summary, _utc_now,
            )

    def _mark_cancelled(self, task_id: str, run_id: str, session_id: str | None):
        from pure.db.repositories import TaskRepository, RunRepository

        trace_written = False
        if session_id and session_id in self.sessions:
            handle = self.sessions[session_id]
            handle.status = "cancelled"
            task_state = handle.agent.current_task_state
            if task_state is not None and task_state.run_id == run_id:
                task_state.status = "cancelled"
                handle.agent.run_store.write_task_state(task_state)
                handle.agent.emit_trace(task_state, "run_cancelled", {"status": "cancelled"})
                trace_written = True
        if not trace_written:
            self._run_service.append_cancel_trace(run_id)
        with self._db().session() as db:
            TaskRepository(db).set_status(task_id, "cancelled")
            RunRepository(db).update(run_id, status="cancelled", ended_at=_utc_now(), error="cancelled")


def _utc_now():
    return datetime.now(timezone.utc)


def _token_usage_summary(metadata: dict):
    return {
        key: value
        for key, value in dict(metadata or {}).items()
        if "token" in str(key).lower() or "usage" in str(key).lower() or "cache" in str(key).lower()
    }
