import json
from pathlib import Path

from fastapi import HTTPException

from pure.core.run_store import RunStore
from pure.core.runtime import PureRuntime
from pure.core.session_store import SessionStore
from pure.core.workspace import WorkspaceContext


class SessionService:
    def __init__(self, db_getter, sessions: dict, model_client_factory):
        self._db = db_getter
        self.sessions = sessions
        self._model_client = model_client_factory

    def create_session(self, project_path: str, runtime_config: dict | None = None, dry_run: bool = False):
        config = dict(runtime_config or {})
        workspace = WorkspaceContext.build(project_path)
        session_store = SessionStore(Path(workspace.repo_root) / ".pure" / "sessions")
        run_store = RunStore(Path(workspace.repo_root) / ".pure" / "runs")
        model_client = self._model_client(config, dry_run=dry_run)
        agent = PureRuntime(
            model_client=model_client,
            workspace=workspace,
            session_store=session_store,
            run_store=run_store,
            approval_policy=str(config.get("approval_policy", "auto")),
            approval_mode=config.get("approval_mode"),
            max_steps=int(config.get("max_steps", 6)),
            max_new_tokens=int(config.get("max_new_tokens", 512)),
            read_only=bool(config.get("read_only", False) or config.get("approval_mode") == "readonly"),
            feature_flags=config.get("feature_flags"),
            tool_repetition_guard=config.get("tool_repetition_guard"),
            runtime_config=config,
        )
        session_id = agent.session["id"]
        from pure.server.state import SessionHandle

        self.sessions[session_id] = SessionHandle(agent=agent)
        return {"session_id": session_id, "status": "created"}

    def get_session(self, session_id: str):
        handle = self._session_handle(session_id)
        agent = handle.agent
        session = agent.session_store.load(session_id)
        memory = session.get("memory", {})
        checkpoints = session.get("checkpoints", {}).get("items", {})
        return {
            "session_id": session_id,
            "status": handle.status,
            "metadata": {
                "created_at": session.get("created_at", ""),
                "workspace_root": session.get("workspace_root", ""),
                "history_count": len(session.get("history", [])),
                "run_count": len(handle.runs),
            },
            "memory_summary": {
                "task_summary": memory.get("working", {}).get("task_summary", "") or memory.get("task", ""),
                "recent_files": memory.get("working", {}).get("recent_files", []) or memory.get("files", []),
                "note_count": len(memory.get("episodic_notes", [])) + len(memory.get("notes", [])),
            },
            "checkpoint_count": len(checkpoints),
        }

    def _session_handle(self, session_id: str):
        handle = self.sessions.get(session_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="session not found")
        return handle
