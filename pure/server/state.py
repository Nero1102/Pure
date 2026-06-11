import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from pure.core.models import (
    AnthropicCompatibleModelClient,
    FakeModelClient,
    OllamaModelClient,
    OpenAICompatibleModelClient,
)
from pure.db.init_db import default_database_url, init_database

from pure.services.checkpoint_app_service import CheckpointAppService
from pure.services.evaluator_app_service import EvaluatorAppService
from pure.services.knowledge_app_service import KnowledgeAppService
from pure.services.project_service import ProjectService
from pure.services.run_service import RunService
from pure.services.scheduler import TaskScheduler
from pure.services.session_service import SessionService
from pure.services.task_service import TaskService


DRY_RUN_FINAL = "<final>Dry run: no LLM API called.</final>"


@dataclass
class SessionHandle:
    agent: Any
    status: str = "idle"
    runs: list[str] = field(default_factory=list)


@dataclass
class TaskJob:
    run_id: str
    session_id: str
    prompt: str
    dry_run: bool
    future: Any | None = None
    cancel_requested: bool = False


class RuntimeService:
    """Thin facade that delegates to focused service classes.

    Public methods are the same as before the split for API compatibility.
    Each method delegates to the appropriate service.
    """

    def __init__(self, database_url: str | None = None):
        # Shared state
        self.sessions: dict[str, SessionHandle] = {}
        self.run_to_session: dict[str, str] = {}
        self.task_jobs: dict[str, TaskJob] = {}
        self.database_url = database_url
        self._database = None
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._lock = threading.Lock()
        self._db_lock = threading.Lock()
        self.eval_reports: dict[str, str] = {}

        # Instantiate scheduler first (needed by task service)
        self._scheduler = TaskScheduler(
            db_getter=self._db,
            sessions=self.sessions,
            run_to_session=self.run_to_session,
            task_jobs=self.task_jobs,
            lock=self._lock,
            executor=self._executor,
        )

        # Run service
        self._run_service = RunService(
            db_getter=self._db,
            sessions=self.sessions,
            run_to_session=self.run_to_session,
            task_jobs=self.task_jobs,
            lock=self._lock,
        )

        # Wire scheduler -> run service
        self._scheduler.set_run_service(self._run_service)

        # Session service
        self._session_service = SessionService(
            db_getter=self._db,
            sessions=self.sessions,
            model_client_factory=self._model_client,
        )

        # Checkpoint service
        self._checkpoint_service = CheckpointAppService(
            db_getter=self._db,
            model_client_factory=self._model_client,
        )

        # Task service
        self._task_service = TaskService(
            db_getter=self._db,
            sessions=self.sessions,
            run_to_session=self.run_to_session,
            task_jobs=self.task_jobs,
            lock=self._lock,
            scheduler=self._scheduler,
            checkpoint_service=self._checkpoint_service,
            session_factory=self.create_session,
        )

        # Project service
        self._project_service = ProjectService(db_getter=self._db)

        # Knowledge service
        self._knowledge_service = KnowledgeAppService(db_getter=self._db)

        # Evaluator service
        self._evaluator_service = EvaluatorAppService(
            runtime_service=self,
            eval_reports=self.eval_reports,
        )

    # ---- configuration ----

    def configure_database(self, database_url: str | None):
        self.database_url = database_url
        self._database = None
        self.task_jobs.clear()
        self.eval_reports.clear()

    # ---- project ----

    def create_project(self, name: str, root_path: str, description: str = ""):
        return self._project_service.create_project(name, root_path, description)

    def get_project(self, project_id: str):
        return self._project_service.get_project(project_id)

    # ---- task ----

    def create_task(self, project_id, title, prompt, priority=0, runtime_config=None, dry_run=False):
        return self._task_service.create_task(project_id, title, prompt, priority, runtime_config, dry_run)

    def get_task(self, task_id: str):
        return self._task_service.get_task(task_id)

    def get_task_status(self, task_id: str):
        return self._task_service.get_task_status(task_id)

    def start_task(self, task_id, runtime_config=None, dry_run=False, dispatch=True):
        return self._task_service.start_task(task_id, runtime_config, dry_run, dispatch)

    def cancel_task(self, task_id: str):
        return self._task_service.cancel_task(task_id)

    def dispatch_task_asyncio(self, task_id: str, loop):
        return self._scheduler.dispatch_task_asyncio(task_id, loop)

    # ---- session ----

    def create_session(self, project_path, runtime_config=None, dry_run=False):
        return self._session_service.create_session(project_path, runtime_config, dry_run)

    def get_session(self, session_id: str):
        return self._session_service.get_session(session_id)

    # ---- run ----

    def run_task(self, session_id, prompt, dry_run=False, task_id=None, run_id=None):
        return self._run_service.run_task(session_id, prompt, dry_run, task_id, run_id)

    def get_run(self, run_id: str):
        return self._run_service.get_run(run_id)

    def get_trace(self, run_id: str):
        return self._run_service.get_trace(run_id)

    def get_report(self, run_id: str):
        return self._run_service.get_report(run_id)

    # ---- checkpoint ----

    def list_task_checkpoints(self, task_id: str):
        return self._checkpoint_service.list_task_checkpoints(task_id)

    def resume_task(self, task_id, checkpoint_id=None, runtime_config=None, dry_run=False, dispatch=True):
        return self._task_service.resume_task(task_id, checkpoint_id, runtime_config, dry_run, dispatch)

    def _validate_checkpoint_for_resume(self, task, checkpoint, runtime_config=None, dry_run=False):
        return self._checkpoint_service.validate_for_resume(
            task, checkpoint,
            project_root_path=task.project.root_path,
            runtime_config=runtime_config,
            dry_run=dry_run,
        )

    def _run_task_job(self, task_id, session_id, prompt, dry_run, run_id):
        return self._scheduler._run_task_job(task_id, session_id, prompt, dry_run, run_id)

    # ---- knowledge ----

    def add_knowledge_documents(self, project_id: str, paths: list[str]):
        return self._knowledge_service.add_knowledge_documents(project_id, paths)

    def index_knowledge(self, project_id: str, paths: list[str] | None = None, reset: bool = True):
        return self._knowledge_service.index_knowledge(project_id, paths, reset)

    def search_knowledge(self, project_id: str, query: str, top_k: int = 5, budget_chars: int = 1400):
        return self._knowledge_service.search_knowledge(project_id, query, top_k, budget_chars)

    # ---- evaluator ----

    def run_evaluation(self, project_path, cases_path="eval_cases.json", runtime_config=None, dry_run=True):
        return self._evaluator_service.run_evaluation(project_path, cases_path, runtime_config, dry_run)

    def get_eval_report(self, eval_id: str):
        return self._evaluator_service.get_eval_report(eval_id)

    # ---- tools (simple enough to stay) ----

    def list_tools(self):
        from pure.tools.registry import base_tool_specs

        specs = base_tool_specs()
        return [
            {
                "name": s.name,
                "description": s.description,
                "schema": dict(s.input_schema),
                "risk_level": s.risk_level,
                "requires_approval": s.requires_approval,
            }
            for s in specs.values()
        ]

    # ---- model client factory ----

    def _model_client(self, config: dict[str, Any], dry_run: bool):
        outputs = config.get("mock_outputs")
        if outputs is not None:
            return FakeModelClient(list(outputs))
        if dry_run:
            client = FakeModelClient([DRY_RUN_FINAL])
            client.is_default_dry_run = True
            return client

        provider = self._required_model_config(
            config,
            keys=("provider", "model_provider"),
            env_names=("PURE_MODEL_PROVIDER",),
            label="PURE_MODEL_PROVIDER",
        ).lower().replace("_", "-")
        model = self._required_model_config(
            config,
            keys=("model", "model_name"),
            env_names=self._model_env_names(provider),
            label="PURE_MODEL_NAME",
        )
        temperature = float(self._model_config(config, ("temperature",), ("PURE_MODEL_TEMPERATURE",), 0.2))
        timeout = int(self._model_config(config, ("timeout", "openai_timeout", "ollama_timeout"), ("PURE_MODEL_TIMEOUT",), 300))

        if provider in {"openai", "openai-compatible"}:
            base_url = self._required_model_config(
                config,
                keys=("base_url",),
                env_names=("PURE_BASE_URL", "PURE_OPENAI_BASE_URL", "PURE_OPENAI_API_BASE", "OPENAI_BASE_URL", "OPENAI_API_BASE"),
                label="PURE_BASE_URL",
            )
            api_key = self._required_model_config(
                config,
                keys=("api_key",),
                env_names=("PURE_API_KEY", "PURE_OPENAI_API_KEY", "OPENAI_API_KEY"),
                label="PURE_API_KEY",
            )
            return OpenAICompatibleModelClient(
                model=model, base_url=base_url, api_key=api_key,
                temperature=temperature, timeout=timeout,
            )

        if provider in {"anthropic", "anthropic-compatible"}:
            base_url = self._required_model_config(
                config,
                keys=("base_url",),
                env_names=(
                    "PURE_BASE_URL", "PURE_ANTHROPIC_BASE_URL", "PURE_ANTHROPIC_API_BASE",
                    "ANTHROPIC_BASE_URL", "ANTHROPIC_API_BASE",
                ),
                label="PURE_BASE_URL",
            )
            api_key = self._required_model_config(
                config,
                keys=("api_key",),
                env_names=(
                    "PURE_API_KEY", "PURE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
                ),
                label="PURE_API_KEY",
            )
            return AnthropicCompatibleModelClient(
                model=model, base_url=base_url, api_key=api_key,
                temperature=temperature, timeout=timeout,
            )

        if provider == "deepseek":
            base_url = self._required_model_config(
                config,
                keys=("base_url",),
                env_names=(
                    "PURE_BASE_URL",
                    "PURE_DEEPSEEK_BASE_URL", "PURE_DEEPSEEK_API_BASE",
                    "DEEPSEEK_BASE_URL", "DEEPSEEK_API_BASE",
                    "PURE_ANTHROPIC_BASE_URL", "PURE_ANTHROPIC_API_BASE",
                    "ANTHROPIC_BASE_URL", "ANTHROPIC_API_BASE",
                ),
                label="PURE_BASE_URL",
            )
            api_key = self._required_model_config(
                config,
                keys=("api_key",),
                env_names=(
                    "PURE_API_KEY",
                    "PURE_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY",
                    "PURE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
                ),
                label="PURE_API_KEY",
            )
            return AnthropicCompatibleModelClient(
                model=model, base_url=base_url, api_key=api_key,
                temperature=temperature, timeout=timeout,
            )

        if provider == "ollama":
            host = self._required_model_config(
                config,
                keys=("base_url", "host"),
                env_names=("PURE_BASE_URL", "PURE_OLLAMA_HOST", "OLLAMA_HOST"),
                label="PURE_BASE_URL",
            )
            top_p = float(self._model_config(config, ("top_p",), ("PURE_MODEL_TOP_P",), 0.9))
            return OllamaModelClient(
                model=model, host=host,
                temperature=temperature, top_p=top_p, timeout=timeout,
            )

        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported PURE_MODEL_PROVIDER. Expected one of: "
                "openai-compatible, anthropic-compatible, deepseek, ollama."
            ),
        )

    @staticmethod
    def _model_config(config, keys, env_names, default=""):
        for key in keys:
            value = config.get(key)
            if value not in (None, ""):
                return value
        for env_name in env_names:
            value = os.environ.get(env_name)
            if value not in (None, ""):
                return value
        return default

    def _required_model_config(self, config, keys, env_names, label):
        value = self._model_config(config, keys, env_names)
        if value not in (None, ""):
            return str(value)
        accepted = ", ".join(env_names)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Real model execution requires {label}. "
                f"Accepted environment variables: {accepted}. "
                "Set dry_run=true to use FakeModelClient without an API call."
            ),
        )

    @staticmethod
    def _model_env_names(provider):
        if provider in {"openai", "openai-compatible"}:
            return ("PURE_MODEL_NAME", "PURE_OPENAI_MODEL", "OPENAI_MODEL")
        if provider in {"anthropic", "anthropic-compatible"}:
            return ("PURE_MODEL_NAME", "PURE_ANTHROPIC_MODEL", "ANTHROPIC_MODEL")
        if provider == "deepseek":
            return ("PURE_MODEL_NAME", "PURE_DEEPSEEK_MODEL", "DEEPSEEK_MODEL")
        if provider == "ollama":
            return ("PURE_MODEL_NAME", "PURE_OLLAMA_MODEL", "OLLAMA_MODEL")
        return ("PURE_MODEL_NAME",)

    # ---- database ----

    def _db(self):
        if self._database is None:
            with self._db_lock:
                if self._database is None:
                    self._database = init_database(
                        self.database_url
                        or os.environ.get("PURE_DATABASE_URL")
                        or default_database_url()
                    )
        return self._database


runtime_service = RuntimeService()
