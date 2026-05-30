from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    project_path: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class SessionCreateResponse(BaseModel):
    session_id: str
    status: str


class SessionResponse(BaseModel):
    session_id: str
    status: str
    metadata: dict[str, Any]
    memory_summary: dict[str, Any]
    checkpoint_count: int


class AskRequest(BaseModel):
    prompt: str
    dry_run: bool = False


class AskResponse(BaseModel):
    run_id: str
    status: str
    report_path: str


class ProjectCreateRequest(BaseModel):
    name: str
    root_path: str
    description: str = ""


class ProjectResponse(BaseModel):
    id: str
    name: str
    root_path: str
    description: str
    created_at: str
    updated_at: str


class TaskCreateRequest(BaseModel):
    project_id: str
    title: str
    prompt: str
    priority: int = 0
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class TaskResponse(BaseModel):
    id: str
    project_id: str
    title: str
    prompt: str
    status: str
    priority: int
    created_at: str
    updated_at: str
    run_id: str | None = None


class TaskRunRequest(BaseModel):
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class TaskResumeRequest(BaseModel):
    checkpoint_id: str | None = None
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class TaskRunResponse(BaseModel):
    run_id: str
    status: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    current_run: dict[str, Any] | None
    current_step: int
    last_trace_event: dict[str, Any] | None
    checkpoint_count: int


class TaskCancelResponse(BaseModel):
    task_id: str
    run_id: str | None
    status: str


class TraceEventResponse(BaseModel):
    run_id: str
    step: int
    event_type: str
    timestamp: str
    payload: dict[str, Any]
    latency_ms: int
    status: str
    event: str | None = None
    created_at: str | None = None


class TraceResponse(BaseModel):
    run_id: str
    events: list[TraceEventResponse]


class RunResponse(BaseModel):
    id: str
    task_id: str
    session_id: str
    status: str
    started_at: str | None
    ended_at: str | None
    trace_path: str
    report_path: str
    error: str
    total_steps: int
    token_usage_summary: dict[str, Any]


class RunReportResponse(BaseModel):
    run_id: str = ""
    status: str = ""
    final_answer: str = ""
    tool_steps: int = 0
    stop_reason: str = ""
    error: str = ""
    checkpoint_id: str = ""
    knowledge_sources: list[dict[str, Any]] = Field(default_factory=list)
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResponse(BaseModel):
    name: str
    description: str = ""
    tool_schema: dict[str, str] = Field(alias="schema")
    risk_level: str
    requires_approval: bool = False


class CheckpointResponse(BaseModel):
    id: str
    run_id: str
    task_id: str
    checkpoint_path: str
    workspace_hash: str
    step: int
    schema_version: str
    last_trace_event: dict[str, Any]
    runtime_metadata: dict[str, Any]
    created_at: str


class CheckpointListResponse(BaseModel):
    task_id: str
    checkpoints: list[CheckpointResponse]


class KnowledgeDocumentRequest(BaseModel):
    project_id: str
    paths: list[str] = Field(default_factory=list)


class KnowledgeIndexRequest(BaseModel):
    project_id: str
    paths: list[str] = Field(default_factory=list)
    reset: bool = True


class KnowledgeSearchRequest(BaseModel):
    project_id: str
    query: str
    top_k: int = 5
    budget_chars: int = 1400


class KnowledgeIndexResponse(BaseModel):
    project_id: str
    document_count: int
    chunk_count: int


class KnowledgeSearchResult(BaseModel):
    content: str
    source: str
    score: float
    metadata: dict[str, Any]


class KnowledgeSearchResponse(BaseModel):
    project_id: str
    results: list[KnowledgeSearchResult]


class EvalRunRequest(BaseModel):
    project_path: str = "."
    cases_path: str = "eval_cases.json"
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


class EvalRunResponse(BaseModel):
    eval_id: str
    status: str
    report_path: str
    summary: dict[str, Any]


class EvalReportResponse(BaseModel):
    schema_version: int
    eval_id: str
    created_at: str
    project_path: str
    cases_path: str
    dry_run: bool
    summary: dict[str, Any]
    rows: list[dict[str, Any]]
