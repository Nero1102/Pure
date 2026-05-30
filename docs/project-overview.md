# Pure Phase Summary (Current)

> 说明：本文档基于仓库 `D:\project\pure` 的当前真实代码生成（截至本地扫描时间）。不包含任何虚构实现或未落地的规划。

## 1. 项目定位

`pure` 是一个面向“代码仓库内的长时技术任务”的本地 Agent Runtime。

- 运行形态：终端 CLI（交互 REPL / one-shot）。
- 核心能力：在受约束的工具集内读取文件、搜索、执行 shell、写文件/打补丁，并将会话与审计工件持久化到本地 `.pure/`。
- 模型后端：Ollama、OpenAI-compatible（`/responses`）、Anthropic-compatible（`/messages`）、DeepSeek（Anthropic-compatible base）。

## 2. 当前项目架构（概览）

主链路（入口到工件落盘）：

1. CLI：`pure`（`pure.cli.cli:main`）解析参数、选择 provider、构建 `PureRuntime` runtime。
2. Workspace：`WorkspaceContext` 采集轻量 repo 快照（git 状态、最近提交、少量 project docs）。
3. Knowledge：`KnowledgeService` 基于 task prompt 检索项目知识，生成 `knowledge_context` section，并把 sources 写入 trace/report。
4. Prompt：`ContextManager` 组装 prefix + memory + knowledge_context + relevant_memory + history + current_request，并做字符预算控制。
5. Model：`core.models` 统一成 `complete(prompt, max_new_tokens, ...) -> text`。
6. Parse：`PureRuntime.parse()` 将模型输出解析为 tool call / final / retry。
7. Tools：`tools.toolkit` 提供受限工具集；`ToolExecutionService` 负责校验/去重/审批/执行/变更检测。
8. Persistence：数据库保存 Project/Task/Run/ToolCall/Checkpoint 元数据；`SessionStore` 保存会话；`RunStore` 写入 `task_state.json`、`trace.jsonl`、`report.json`；`DurableMemoryStore` 写入 `.pure/memory/*.md`；Knowledge index 默认写入 `.pure/knowledge/index.json`。

## 3. 当前目录结构（高层）

```
.
├─ pure/                  # runtime 源码（CLI / core / db / server / services / tools / utils）
├─ docs/                  # 项目文档（本次新增的阶段总结也在此）
├─ tests/                 # pytest 测试
├─ scripts/               # 指标收集与实验脚本
├─ benchmarks/            # 基准任务定义与相关工件（脚本消费）
├─ assets/                # 截图等静态资源
├─ .env.example           # 示例环境变量（注意：当前示例前缀与代码存在不一致，见技术债）
├─ pyproject.toml         # Python packaging / dev 依赖
└─ .pure/                 # 本地数据库与运行时工件（pure.db / sessions / runs / memory / knowledge），通常不提交
```

## 4. 当前技术栈

- 语言/运行时：Python `>= 3.10`（见 `pyproject.toml`）。
- 依赖：运行时依赖包括 `fastapi`、`sqlalchemy`、`uvicorn`，同时继续大量使用 Python 标准库。
- 开发依赖（`dependency-groups.dev`）：`pytest`、`ruff`。
- 工程：提供 `pure` console script；推荐使用 `uv` 运行（见 README）。

## 5. 当前数据库设计（概览）

项目当前采用“元数据数据库 + 文件工件”双层持久化：

- `.pure/pure.db`：默认 SQLite 元数据数据库，保存 Project、Task、Run、ToolCall、Checkpoint 等平台索引与摘要。
- `.pure/sessions/<session_id>.json`：可恢复会话状态（history、memory、checkpoints 等）。
- `.pure/runs/<run_id>/task_state.json`：单次 ask 的状态快照。
- `.pure/runs/<run_id>/trace.jsonl`：事件时间线（每行一个 JSON event）。
- `.pure/runs/<run_id>/report.json`：单次运行结果摘要（聚合关键指标/元数据）。
- `.pure/memory/MEMORY.md` + `.pure/memory/topics/*.md`：持久化“长期记忆”主题索引与条目。
- `.pure/knowledge/index.json`：项目 Knowledge 向量索引（chunk + embedding + metadata）。

详见 `docs/current-db-schema.md`。

## 6. 当前 API 列表（概览）

Pure 当前同时提供 HTTP、CLI、工具调用、模型后端调用四类契约：

- HTTP API：`/health`、`/projects`、`/tasks`、`/sessions`、`/runs`、`/tools`、`/knowledge/*`。
- CLI 参数与行为契约（`pure ...`）。
- 工具调用契约（tool registry：`list_files/read_file/search/run_shell/write_file/patch_file/delegate`）。
- 模型后端 HTTP 调用契约（Ollama `/api/generate`、OpenAI-compatible `/responses`、Anthropic-compatible `/messages`）。

详见 `docs/api-contract.md`。

## 7. 当前完成的模块（按代码目录）

- `pure/cli/`：CLI 入口、provider 选择与 session resume。
- `pure/core/`：
  - `runtime.py`：`PureRuntime` 主循环（prompt->model->parse->tool->persist）。
  - `models.py`：各 provider 的 HTTP 适配层（统一 `complete()` 接口）。
  - `context_manager.py`：prompt 组装与预算控制。
  - `memory.py`：工作记忆 + durable memory（Markdown 落盘）。
  - `workspace.py`：轻量工作区快照与 fingerprint。
  - `run_store.py` / `session_store.py` / `task_state.py`：工件与会话持久化。
- `pure/services/`：将 runtime 内部能力拆成服务（Prompt/ToolExecution/Memory/Checkpoint/Workspace）。
- `pure/db/`：SQLAlchemy 2.x metadata models、session factory、repositories、init_db。
- `pure/server/`：FastAPI app、Pydantic schemas、HTTP routers、RuntimeService。
- `pure/tools/`：工具 registry、参数校验、执行器。
- `pure/utils/`：配置读取、legacy 工件迁移、benchmark evaluator 与 metrics 聚合。

## 8. 当前核心技术决策（摘要）

- 以“受限工具 + 审批策略”作为安全边界（工具校验、路径逃逸防护、重复调用防护、只读 child delegate）。
- 工件落盘采用可审计的 JSON/JSONL（trace/report/task_state）+ 可读的 Markdown（durable memory）。
- Prompt 预算控制使用“字符预算”与分区降级（prefix/memory/relevant_memory/history）。
- OpenAI-compatible 后端优先走 `/responses`；Anthropic-compatible 走 `/messages`；Ollama 走 `/api/generate`。

详见 `docs/technical-decisions.md`。

## 9. 当前存在的技术债务（摘要）

- `.env.example` 已切换为 `PURE_*` 前缀；历史 `PICO_*` 示例不再代表当前配置方式。
- 核心类已重命名为 `PureRuntime`；`Pico` 保留为向后兼容 alias。
- prompt 预算以字符为单位，无法严格对应不同模型的 token 计费/限制。
- 工件 schema 虽有版本字段（如 `CHECKPOINT_SCHEMA_VERSION`），但整体 schema 演进策略仍偏隐式。

详见 `docs/technical-decisions.md` 与 `docs/next-phase-suggestion.md`。

## 10. 当前运行方式（最小集）

- 安装依赖（推荐）：`uv sync`
- One-shot：`uv run pure --provider deepseek "your task..."`
- 交互 REPL：`uv run pure --provider deepseek`
- 直接模块运行：`python -m pure --provider deepseek`

## 11. 环境变量说明（摘要）

运行时会从工作区向上查找并加载 `.env`（UTF-8），并写入进程环境（可覆盖或保留取决于 `load_project_env(..., override=True)`）。

主要使用的变量（以代码为准）：

- OpenAI-compatible：`PURE_OPENAI_API_BASE`、`PURE_OPENAI_API_KEY`、`PURE_OPENAI_MODEL`（兼容 `OPENAI_API_*` / `OPENAI_MODEL`）。
- Anthropic-compatible：`PURE_ANTHROPIC_API_BASE`、`PURE_ANTHROPIC_API_KEY`、`PURE_ANTHROPIC_MODEL`（兼容 `ANTHROPIC_API_*` / `ANTHROPIC_MODEL`；key 还会回退到 `PURE_RIGHT_CODES_API_KEY/RIGHT_CODES_API_KEY/PURE_OPENAI_API_KEY/OPENAI_API_KEY`）。
- DeepSeek：`PURE_DEEPSEEK_API_BASE`、`PURE_DEEPSEEK_API_KEY`、`PURE_DEEPSEEK_MODEL`（兼容 `DEEPSEEK_API_*` / `DEEPSEEK_MODEL`）。
- Secret 名单扩展：`PURE_SECRET_ENV_NAMES`（legacy：`MINI_CODING_AGENT_SECRET_ENV_NAMES`），逗号分隔。

详见 `docs/api-contract.md`。

## 12. 当前模块依赖关系（概览）

见 `docs/current-architecture.md` 的 Mermaid 依赖图（CLI/server -> core/db/services/tools/utils）。
## 13. Phase Update: Platform Metadata Database and Task API (2026-05-25)

This section supersedes older overview statements that described Pure as CLI-only, file-only, or without a database.

### 13.1 Current positioning

Pure is now a local CLI runtime plus a database-backed Agent Runtime platform:

- CLI entry remains `pure` / `python -m pure`.
- HTTP entry is `pure.server.main:app`.
- The service layer exposes projects, tasks, async task runs, task status polling, cancellation, sessions, run traces, run reports, tools, and health.
- Runtime behavior still flows through the existing `PureRuntime.ask()` loop.
- SQLite is the default development database through SQLAlchemy 2.x.
- The database stores platform metadata only. Session, trace, report, task-state, and durable-memory artifacts remain under `.pure/`.
- Redis, Celery, WebSocket streaming, and external background worker systems have not been introduced. Current background task execution is local to the FastAPI process via `asyncio` and a thread executor.

### 13.2 Current directory structure highlights

```text
pure/
  cli/        CLI argument parsing and runtime assembly
  core/       PureRuntime runtime, model clients, stores, memory, workspace context
  knowledge/   Project knowledge loaders, splitter, embeddings, vector store, retrieval service
  db/         SQLAlchemy models, session factory, repositories, init command
  services/   Prompt, tool execution, checkpoint, memory, workspace services
  tools/      Restricted tool registry and runners
  utils/      Config, migration, evaluator, metrics
  server/     FastAPI app, schemas, routers, RuntimeService
    api/
      projects.py
      tasks.py
      sessions.py
      runs.py
      tools.py
      knowledge.py
    main.py
    schemas.py
    state.py
tests/
  test_db_repositories.py
  test_server_api.py
  test_task_api.py
```

### 13.3 Current technology stack

- Python `>=3.10`.
- Runtime dependencies include `fastapi`, `sqlalchemy`, and `uvicorn`.
- Dev dependencies remain `pytest` and `ruff`.
- Packaging uses setuptools package discovery for `pure*` subpackages.
- Default database URL is `.pure/pure.db` via SQLite. `PURE_DATABASE_URL` can point at another SQLAlchemy-supported database.

### 13.4 Current API surface

HTTP API:

- `GET /health`
- `POST /projects`
- `GET /projects/{project_id}`
- `POST /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/{task_id}/run`
- `GET /tasks/{task_id}/status`
- `POST /tasks/{task_id}/cancel`
- `POST /sessions`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/ask`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/trace`
- `GET /runs/{run_id}/report`
- `GET /tools`
- `POST /knowledge/documents`
- `POST /knowledge/index`
- `POST /knowledge/search`

Existing non-HTTP contracts remain:

- CLI contract: `pure ...`
- Tool contract: `list_files/read_file/search/run_shell/write_file/patch_file/delegate`
- Provider contract: Ollama, OpenAI-compatible, Anthropic-compatible, DeepSeek-compatible clients.

### 13.5 Current technical debt snapshot

- HTTP live session handles are still in memory; project/task/run metadata is durable in the database.
- Task execution is asynchronous for the task API, but the job registry is process-local and not durable across server restarts.
- Cancellation is cooperative and updates metadata/trace state; it does not forcibly terminate an already-blocking provider or tool call.
- The service model-client assembly is intentionally minimal for the current dry-run/mock phase.
- HTTP error model is still basic; there is no typed error envelope yet.
- Trace events now have a standard event shape, but `task_state.json`, `report.json`, and the overall artifact versioning strategy are still not unified.
- Database migrations are not formalized yet; `init_db` creates the current metadata schema directly.
- The runtime class is now `PureRuntime`; `Pico` is kept as a backward-compatible alias.

## 14. Phase Update: Async Execution and Standard Trace (2026-05-26)

This section supersedes older statements that described task execution as synchronous or said there was no cancellation API.

### 14.1 Current positioning

Pure is now a local CLI runtime plus a background-task Agent Runtime platform:

- `POST /tasks` creates task metadata only.
- `POST /tasks/{task_id}/run` creates a run, marks the task queued, and returns immediately.
- A local background job invokes the existing `PureRuntime.ask()` loop without rewriting it.
- `GET /tasks/{task_id}/status` exposes task status, current run, current step, last trace event, and checkpoint count.
- `POST /tasks/{task_id}/cancel` marks task/run state cancelled and writes `run_cancelled` trace when possible.
- `GET /runs/{run_id}/trace` returns standardized TraceEvent objects.

### 14.2 Current async execution architecture

```text
HTTP client
  -> POST /tasks/{task_id}/run
  -> RuntimeService.start_task()
  -> DB task/run queued state
  -> immediate {run_id, status}
  -> asyncio + local ThreadPoolExecutor
  -> RuntimeService._run_task_job()
  -> PureRuntime.ask()
  -> RunStore artifacts + DB summary indexing
```

The implementation intentionally avoids Celery, Redis Queue, Kafka, and durable broker semantics.

### 14.3 Current TraceEvent schema

Trace events are standardized by `pure/services/trace_service.py`:

```text
run_id
step
event_type
timestamp
payload
latency_ms
status
```

Standard event types currently include:

```text
run_started, context_built, model_called, tool_requested,
tool_validated, tool_executed, memory_updated, checkpoint_created,
knowledge_retrieved, run_completed, run_failed, run_cancelled
```

Legacy `event`, `created_at`, and event-specific top-level fields are preserved for compatibility.

## 15. Phase Update: Tool Gateway and Checkpoint/Resume (2026-05-26)

This section supersedes older overview statements that described tool policy as only the legacy `risky` flag or checkpointing as session-local only.

### 15.1 Current positioning

Pure is now a governable, auditable, and resumable Agent Runtime:

- All model-requested tool calls still use the existing tool protocol, but execution is centralized behind `ToolGateway.execute()`.
- `ToolExecutionService` remains the runtime-facing service; it now delegates execution to the gateway.
- Tool metadata is represented as `ToolSpec` with `name`, `description`, `input_schema`, `risk_level`, and `requires_approval`.
- Risk levels are `safe`, `medium`, and `high`.
- Approval modes are `auto`, `readonly`, and `manual`.
- Tool audit metadata is written to trace events and indexed into the `tool_calls` table.
- Checkpoints are still stored in session artifacts, and checkpoint summaries are now indexed in the metadata database.
- The task API exposes checkpoint listing and resume.

### 15.2 Current directory structure highlights

```text
pure/
  cli/        CLI argument parsing and runtime assembly
  core/       PureRuntime runtime, model clients, stores, memory, workspace context
  db/         SQLAlchemy models, session factory, repositories, init command
  services/   Prompt, tool execution, checkpoint, memory, trace, workspace services
  tools/      Tool Gateway, ToolSpec registry, policies, and restricted runners
    gateway.py
    policies.py
    registry.py
    toolkit.py
  server/     FastAPI app, schemas, routers, RuntimeService
    api/
      projects.py
      tasks.py
      sessions.py
      runs.py
      tools.py
  utils/      Config, migration, evaluator, metrics
tests/
  test_tool_gateway_checkpoint_resume.py
```

### 15.3 Current API additions

HTTP API additions:

- `GET /tasks/{task_id}/checkpoints`
- `POST /tasks/{task_id}/resume`

`GET /tools` now returns `description` and `requires_approval` in addition to tool name, schema, and risk level. Risk level values are now `safe`, `medium`, and `high`.

### 15.4 Current database additions

- `tool_calls.approval_decision` records whether a tool was approved, denied, waiting for manual approval, or otherwise not executed.
- `checkpoints` now includes checkpoint summaries: `step`, `memory_snapshot`, `last_trace_event`, `runtime_metadata`, and `schema_version`.
- The database still stores summaries and indexes only; full session, trace, report, and checkpoint payloads remain under `.pure/`.

### 15.5 Current technical debt snapshot

- There is still no migration framework, so the expanded SQLAlchemy schema requires direct database initialization for fresh databases.
- Resume validation is intentionally strict around workspace hash and schema, but it does not yet implement rich checkpoint migration.
- `runtime_config` is still a dictionary at the API boundary; the recognized keys are documented but not modeled as a dedicated Pydantic schema.
- Manual approval currently returns `waiting_approval`; there is not yet a follow-up approval submission API.
- Tool policy is centralized, but path/command policy remains deliberately conservative and should be refined as new tools are added.

## 16. Phase Update: Project Knowledge Retrieval Layer (2026-05-26)

This section supersedes older overview statements that described Pure as “only Runtime” without project knowledge augmentation.

### 16.1 Current positioning

Pure is now a Runtime platform with project-level knowledge augmentation for context building:

- Knowledge is used for Runtime context enrichment, not customer-support Q&A and not a chat-style RAG product.
- Runtime retrieves knowledge before building the model prompt and injects it as a dedicated `knowledge_context` section with a fixed budget.
- Knowledge retrieval sources are written to trace (`knowledge_retrieved`) and to run report (`knowledge_sources`).

### 16.2 Knowledge processing pipeline

```text
document -> load -> split -> chunk -> embedding -> vector store -> retrieve
```

Supported sources include: markdown/txt/README, `docs/*`, and `report.json` summary.

### 16.3 Knowledge persistence

The default implementation persists the project knowledge index under:

```text
.pure/knowledge/index.json
```

Embeddings are deterministic fake/mock by default so tests and dry-run behavior never call a real embedding API.

## 17. Phase Update: Evaluator, Docker, and Engineering Docs (2026-05-26)

This section supersedes older overview statements that described Pure as only a runnable Runtime platform or omitted evaluator/deployment/documentation surfaces.

### 17.1 Current positioning

Pure is now a complete local Agent engineering project with runtime, API, metadata, governance, knowledge, evaluator, Docker, and documentation capabilities. The project positioning remains "enterprise-adjacent design"; it is not described as a production-grade distributed system.

### 17.2 Current directory structure highlights

```text
pure/
  cli/          CLI argument parsing and runtime assembly
  core/         PureRuntime runtime, model clients, stores, memory, workspace context
  db/           SQLAlchemy metadata models, repositories, session/init helpers
  evaluator/    Eval case loading, runner, metrics, and report writer
  knowledge/    Project document loading, splitting, embedding, storage, retrieval
  server/       FastAPI app, schemas, routers, RuntimeService
    api/
      evals.py
      knowledge.py
      projects.py
      runs.py
      sessions.py
      tasks.py
      tools.py
  services/     Prompt, tool execution, checkpoint, memory, trace, workspace services
  tools/        ToolGateway, policy, registry, restricted runners
  utils/        Config, migration, legacy benchmark evaluator, metrics
docs/
  api.md
  architecture.md
  evaluator.md
  knowledge.md
  runtime.md
  tool_gateway.md
Dockerfile
docker-compose.yml
eval_cases.json
.env.example
```

### 17.3 Evaluator summary

The platform evaluator lives under `pure/evaluator/` and consumes `eval_cases.json` cases with:

- `id`
- `task`
- `expected_tools`
- `forbidden_tools`
- `success_keywords`
- `max_steps`

`POST /eval/run` executes cases through the existing Runtime path. `dry_run=true` is supported end to end and uses `FakeModelClient`, so tests and local smoke runs do not call a real model. Reports are written to `.pure/evals/<eval_id>/report.json`.

### 17.4 Docker summary

Docker packaging now includes:

- `Dockerfile`
- `docker-compose.yml`
- `.env.example`

`docker-compose.yml` defines `api` and `db` services only. Redis is not included because Pure does not currently use Redis, Celery, a durable broker, or a cache layer.

### 17.5 Current technical debt snapshot

- Database migrations are still not formalized.
- Evaluator report lookup over HTTP is process-local after creation, although the report artifact is durable on disk.
- Evaluator runs create normal project/task/run metadata but there is no dedicated evaluator database table.
- API errors are still basic `HTTPException` responses rather than a typed error envelope.
- `runtime_config` is still an open dictionary rather than a typed Pydantic model.
- Docker Compose can start `api` and `db`, but operational concerns such as auth, CORS, migrations on startup, and production process supervision remain out of scope.
- Artifact schemas still need unified versioning across `task_state.json`, `trace.jsonl`, `report.json`, and evaluator reports.
