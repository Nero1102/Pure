# Core Technical Decisions (Current)

> 目标：用“结论 + 理由 + 影响”的方式记录当前代码已落地的关键决策，便于下一阶段沿用或有依据地调整。

## 1. 以受限工具集作为安全边界

**结论**

- runtime 只暴露固定白名单工具（`list_files/read_file/search/run_shell/write_file/patch_file/delegate`）。
- 将 risky 行为集中在 `ToolExecutionService`（校验、审批、快照 diff、记录 metadata）。

**理由**

- 将“模型意图”与“真实副作用”隔离，便于审计与控制。

**影响**

- 新能力必须以“新增/扩展工具”的方式进入系统（更可控，但扩展速度较慢）。

## 2. 路径安全：强制 workspace root 内访问

**结论**

- 所有文件路径通过 `PureRuntime.path()` 解析并拒绝逃逸（基于 `commonpath`）。

**理由**

- 防止 `../` 或符号链接导致越权读写。

**影响**

- 工具与服务必须尊重 workspace 根目录；跨 repo 操作需要额外设计。

## 3. 审批策略与只读模式

**结论**

- risky 工具（`run_shell/write_file/patch_file`）在 `--approval ask|auto|never` 下执行；`read_only=True` 时直接拒绝 risky。
- `delegate` 生成 child agent，强制 `approval_policy="never"` 且 `read_only=True`。

**理由**

- 把“权限提升”变为显式用户决策，降低误操作风险。

**影响**

- 自动化批量任务需要更清晰的审批策略与权限模型（下一阶段可考虑）。

## 4. 工件落盘为核心：Session vs Run 分离

**结论**

- session（`.pure/sessions/*.json`）保存可恢复对话状态。
- run（`.pure/runs/<run_id>/...`）保存单次 ask 的审计证据（task_state/trace/report）。

**理由**

- 恢复现场与复盘证据关注点不同；分离便于组织与清理。

**影响**

- 需要明确 schema 演进策略（report/trace/task_state）以保持兼容性。

## 5. Prompt 构建：分区预算 + 降级顺序

**结论**

- `ContextManager` 将 prompt 分为：`prefix/memory/relevant_memory/history/current_request`。
- 使用“字符预算”做上限，并按 `reduction_order` 压缩/剪裁，metadata 写入 trace/report。

**理由**

- 不依赖特定 tokenizer；实现简单、跨模型。

**影响**

- 字符预算与 token 限制并不等价；长中文/代码块会引入偏差。

## 6. Prompt cache：基于 prefix hash 的稳定 key

**结论**

- 对“明确支持 prompt cache 的后端”（OpenAI compatible：base_url 包含 `openai.com/right.codes`）发送 `prompt_cache_key`。
- key 使用 prefix 的 hash（稳定段），避免 history 变化导致缓存失效。

**理由**

- 让缓存粒度对齐“稳定前缀”，提高命中率。

**影响**

- `supports_prompt_cache` 的判定逻辑是启发式；未来可能需要更明确的能力探测。

## 7. Durable memory：用 Markdown 做跨会话稳定事实存储

**结论**

- durable memory 使用 `.pure/memory/MEMORY.md` + `topics/*.md`（人可读、可 diff）。
- 支持将 final answer 中的特定格式行提升为 durable（例如 `Decision:`/`Project convention:` 等）。

**理由**

- “稳定事实/约定”更适合可读与可审阅的格式；方便手工维护。

**影响**

- 需要更明确的提取规范与冲突处理策略（目前已有 subject-key 替换逻辑，但仍偏启发式）。

## 8. 当前存在的技术债务（清单）

- `.env.example` 使用 `PICO_*` 前缀，但代码读取 `PURE_*`（一致性问题，易误导使用者）。
- 核心类已重命名为 `PureRuntime`，文档与代码统一使用 `Pure` branding。
- 运行时依赖为空，但对外表现“推荐 uv”，缺少明确的最小安装/运行文档分层（README 与 `.env.example` 也不一致）。
- prompt 预算使用字符而非 token；在严格 token 限制/计费分析时不精确。
- artifact schema 版本化不完全统一（checkpoint 有 schema_version，report/trace/task_state 没有统一顶层 schema_version 字段）。
## 9. 2026-05-25: Add a Thin FastAPI Service Layer Without Replacing the Runtime

**Decision**

- Add `pure/server/` as an HTTP adapter layer built with FastAPI and Pydantic.
- Keep `PureRuntime.ask()` as the only task execution loop.
- Route `POST /sessions/{session_id}/ask` through `RuntimeService.run_task()`, which delegates to `PureRuntime.ask()`.
- Keep CLI behavior unchanged.

**Reason**

- The project needs a serviceable runtime surface without destabilizing the proven local CLI runtime.
- A thin adapter lets the HTTP API reuse existing session, run, trace, report, memory, and tool semantics.

**Impact**

- Pure can now be run as `uvicorn pure.server.main:app --reload`.
- HTTP tests can exercise the runtime with `TestClient`.
- The service boundary is explicit, but long-running job orchestration is still out of scope for this phase.

## 10. 2026-05-25: Use In-Memory HTTP Session/Run Indexes for the First Service Phase

**Decision**

- Use `RuntimeService.sessions` and `RuntimeService.run_to_session` dictionaries for HTTP request routing.
- Do not add SQLite, Postgres, Redis, Celery, or a worker queue in this phase.
- Continue to treat `.pure/` artifacts as the persisted source of session and run evidence.

**Reason**

- The phase goal is to expose the runtime over HTTP while preserving the existing artifact model.
- In-memory indexes are enough for `TestClient`, local development, and single-process API usage.

**Impact**

- Server restart loses the HTTP-only mapping from `run_id` to live session handle.
- Persisted artifacts remain intact, but the current HTTP API cannot discover every historical run without extra indexing.
- A future phase should define a durable run/session index before supporting multi-process or production deployment.

## 11. 2026-05-25: Keep Trace and Report Reads Backed by RunStore Artifacts

**Decision**

- `GET /runs/{run_id}/trace` parses `RunStore.trace_path(run_id)` JSONL into a structured `events` response.
- `GET /runs/{run_id}/report` returns `RunStore.report_path(run_id)` JSON.
- Do not duplicate trace/report data into HTTP-specific storage.

**Reason**

- `RunStore` is already the audit boundary for run evidence.
- Reusing it avoids divergence between CLI-created and HTTP-created runs.

**Impact**

- The API exposes the same evidence chain used by metrics and debugging tools.
- Response stability now depends on the existing artifact schema, which still needs explicit versioning.

## 12. 2026-05-25: Expose Tool Metadata Without Changing the Tool Protocol

**Decision**

- `GET /tools` returns tool name, argument schema, and risk level derived from `pure.tools.toolkit`.
- The endpoint does not execute tools and does not alter `build_tool_registry()` semantics.

**Reason**

- Clients need discoverability before asking the runtime to act.
- The existing tool protocol and risk metadata already contain the required information.

**Impact**

- HTTP clients can show available capabilities and risk levels.
- Tool policy remains centralized in `ToolExecutionService`; the HTTP layer is not a second policy engine.

## 13. 2026-05-25: Add FastAPI/Uvicorn as Runtime Dependencies

**Decision**

- Add `fastapi` and `uvicorn` to project dependencies.
- Keep `pytest` and `ruff` in the dev dependency group.
- Use setuptools package discovery for `pure*` so `pure.server` is packaged with the rest of the project.

**Reason**

- FastAPI and Pydantic are required by the new HTTP service layer.
- Uvicorn is the documented local server runner.
- Package discovery prevents new subpackages from being accidentally omitted.

**Impact**

- The previous "runtime dependencies are empty" statement is no longer true.
- Local installation and CI should install the project dependencies before running API tests.

## 14. 2026-05-25: Add a SQLAlchemy Metadata Database Without Moving Artifacts

**Decision**

- Add `pure/db/` with SQLAlchemy 2.x models, a session factory, repositories, and an initialization command.
- Use SQLite as the default local database at `.pure/pure.db`.
- Keep the schema compatible with PostgreSQL and MySQL by using portable column types and JSON-encoded text for summary fields.
- Store only platform metadata in the database: projects, tasks, runs, tool-call summaries, checkpoint indexes, and artifact paths.
- Keep full `trace.jsonl`, `report.json`, session JSON, task-state JSON, and durable memory files in the existing `.pure/` artifact layout.

**Reason**

- Pure needs durable platform metadata for project/task/run lookup, but trace/report artifacts are already the audit boundary and can be large.
- Avoiding full trace/report duplication keeps the database small and prevents divergence between file artifacts and DB records.
- SQLAlchemy gives a path from SQLite development to PostgreSQL/MySQL deployment without introducing a broker or worker stack.

**Impact**

- `sqlalchemy` is now a runtime dependency.
- Database initialization is explicit through `python -m pure.db.init_db`.
- Future phases need a migration strategy before schema changes become frequent.

## 15. 2026-05-25: Use a Repository Layer Between API and SQLAlchemy Sessions

**Decision**

- Add `ProjectRepository`, `TaskRepository`, `RunRepository`, `ToolCallRepository`, and `CheckpointRepository`.
- Keep FastAPI routers thin; they call `RuntimeService`, which uses repositories for persistence.
- Do not write SQLAlchemy session operations directly in API route handlers.

**Reason**

- Repositories preserve a stable persistence boundary as the API grows.
- They make database tests direct and keep task lifecycle logic outside Pydantic route code.

**Impact**

- Tests can exercise CRUD and lifecycle behavior using temporary SQLite databases.
- Future migrations, validation, or transaction policies have a clear place to land.

## 16. 2026-05-25: Add a Synchronous Task API Without Rewriting the Runtime

**Decision**

- Add `POST /projects`, `GET /projects/{project_id}`, `POST /tasks`, `GET /tasks/{task_id}`, and `GET /runs/{run_id}`.
- Implement the task lifecycle as `created -> queued -> running -> completed|failed`.
- Implement the run lifecycle as `created -> running -> completed|failed`.
- Keep `PureRuntime.ask()` as the execution loop. It now accepts optional `task_id` and `run_id` so service-created database records can align with artifact ids.
- Keep legacy `/sessions` and `/sessions/{session_id}/ask` endpoints for compatibility.

**Reason**

- The platform needs task/run entities without destabilizing the local CLI runtime.
- Synchronous execution is enough for the current phase and avoids premature Redis/Celery/WebSocket design.

**Impact**

- `POST /tasks` creates DB records, executes the runtime, writes trace/report files, and indexes summary metadata.
- There is still no queue, cancellation API, background worker, or streaming run updates.
- CLI remains independent of the database.

## 17. 2026-05-25: Require Dry Run to Exercise Both DB and Artifacts

**Decision**

- `dry_run` uses `FakeModelClient` and must still create `Project`/`Task`/`Run` metadata when invoked through the task API.
- `dry_run` must still produce the normal `.pure/runs/<run_id>/trace.jsonl` and `report.json` artifacts.

**Reason**

- Tests and local development need deterministic execution without real model API calls.
- Exercising both metadata and artifact paths in dry-run mode prevents production-only persistence failures.

**Impact**

- API tests can verify end-to-end task execution with temporary SQLite and no LLM credentials.
- Dry-run is now a first-class platform workflow, not just a mock model shortcut.

## 18. 2026-05-26: Split Task Creation from Asynchronous Task Execution

**Decision**

- Change the task platform flow so `POST /tasks` creates metadata only.
- Add `POST /tasks/{task_id}/run` to create a run, mark the task/run queued, and return immediately with `{run_id, status}`.
- Dispatch execution through the FastAPI async route using `asyncio` and a local `ThreadPoolExecutor`.
- Keep `PureRuntime.ask()` as the execution loop; do not rewrite the runtime loop.
- Do not introduce Celery, Redis Queue, Kafka, or a durable broker.
- Keep legacy `/sessions/{session_id}/ask` available as a direct session ask path.

**Reason**

- The platform needs background task semantics without destabilizing the proven runtime loop.
- A local async/thread dispatch path is enough for current single-process API use and test coverage.
- Separating task creation from run start gives clients a clearer lifecycle boundary and makes status polling explicit.

**Impact**

- Task lifecycle is now `created -> queued -> running -> completed|failed|cancelled`.
- Run lifecycle is now `created|queued -> running -> completed|failed|cancelled`.
- `POST /tasks/{task_id}/run` is non-blocking for clients.
- In-flight job ownership is process-local; server restart does not recover queued/running jobs automatically.
- Tests must poll task status instead of assuming task creation completes the runtime.

## 19. 2026-05-26: Add Cooperative Task Cancellation Without a Distributed Worker

**Decision**

- Add `POST /tasks/{task_id}/cancel`.
- Store a process-local cancellation flag in `RuntimeService.task_jobs`.
- Mark task and latest run metadata as `cancelled`.
- Attempt to cancel the local future when possible.
- Append `run_cancelled` to trace when an active task state or trace path is available.

**Reason**

- Clients need a way to stop caring about queued/running work and observe a terminal lifecycle state.
- Cancellation should be available without introducing a full task queue or worker supervisor.
- Trace evidence must record cancellation so status, report consumers, and debugging tools can explain the terminal state.

**Impact**

- Cancellation is cooperative at the service boundary.
- Blocking provider calls, shell commands, or tool calls may finish before the cancellation mark is observed.
- Database status and trace artifacts can show `cancelled`.
- Future phases need stronger interruption semantics if Pure must terminate subprocesses or provider requests mid-flight.

## 20. 2026-05-26: Standardize TraceEvent While Preserving Legacy Trace Fields

**Decision**

- Add `pure/services/trace_service.py`.
- Standardize persisted and API trace events around:
  - `run_id`
  - `step`
  - `event_type`
  - `timestamp`
  - `payload`
  - `latency_ms`
  - `status`
- Support the standard event types:
  - `run_started`
  - `context_built`
  - `model_called`
  - `tool_requested`
  - `tool_validated`
  - `tool_executed`
  - `memory_updated`
  - `checkpoint_created`
  - `knowledge_retrieved`
  - `run_completed`
  - `run_failed`
  - `run_cancelled`
- Preserve legacy top-level fields such as `event`, `created_at`, `prompt_metadata`, `checkpoint_id`, `trigger`, `name`, `args`, and `result`.
- Normalize old event names on API read, for example `prompt_built -> context_built` and `run_finished -> run_completed|run_failed`.

**Reason**

- Trace is now an API contract, not just an internal debug artifact.
- Standard fields let clients build status views, timelines, and diagnostics without hard-coding every historical event shape.
- Keeping legacy fields avoids breaking metrics, tests, and existing artifact consumers.

**Impact**

- `GET /runs/{run_id}/trace` returns structured TraceEvent objects.
- Dry-run/mock execution produces the same lifecycle trace shape as normal execution.
- The trace file remains JSONL under `.pure/runs/<run_id>/trace.jsonl`.
- Artifact-level versioning is still incomplete; future phases should add explicit `schema_version` handling across trace, report, and task_state.

## 21. 2026-05-26: Add ToolGateway Without Changing the Tool Protocol

**Decision**

- Add `pure/tools/gateway.py` as the single execution gateway for runtime tools.
- Keep model-facing tool syntax and existing `toolkit.py` runners unchanged.
- Keep `PureRuntime.ask()` unchanged as the runtime loop; it still calls `run_tool()` through `ToolExecutionService`.
- Make `ToolExecutionService` delegate to `ToolGateway.execute()`.

**Reason**

- Pure needs governed tool execution without destabilizing the existing runtime loop or prompting contract.
- A gateway layer centralizes policy, audit metadata, latency measurement, and workspace safety checks while preserving the established tool registry.

**Impact**

- All tool calls now pass through the same policy and audit path.
- Existing tests and tool-call syntax remain compatible.
- Future tool additions should register specs and runners rather than bypassing the gateway.

## 22. 2026-05-26: Normalize Tool Metadata and Approval Modes

**Decision**

- Add `ToolSpec` in `pure/tools/registry.py` with `name`, `description`, `input_schema`, `risk_level`, and `requires_approval`.
- Use risk levels `safe`, `medium`, and `high`.
- Add gateway approval modes `auto`, `readonly`, and `manual`.
- Preserve legacy `approval_policy` behavior for CLI compatibility.
- Make `readonly` reject `write_file`, `patch_file`, `run_shell`, and `delete_file`.
- Make `manual` return `waiting_approval` for high-risk tools instead of executing them.

**Reason**

- The legacy `risky` boolean was enough for a local CLI agent but too coarse for platform governance.
- Separating risk level from approval mode makes future policies easier to express without changing tool schemas.

**Impact**

- `GET /tools` can expose richer tool metadata.
- Tool trace payloads and database rows now include policy decisions.
- There is not yet an approval submission API; `waiting_approval` is a terminal tool result for the current phase.

## 23. 2026-05-26: Index Tool Audit Decisions in the Database

**Decision**

- Extend `tool_calls` with `approval_decision`.
- Continue storing tool args as JSON-encoded text and result as a summary.
- Index tool audit data from `tool_executed` trace events after a run completes.

**Reason**

- Trace remains the detailed audit evidence, but the database needs enough summary metadata for task/run inspection and governance queries.
- Keeping only summaries avoids duplicating full trace payloads in the database.

**Impact**

- Tool governance state is visible in both trace artifacts and metadata rows.
- Existing databases need migration or reinitialization because formal migrations are still not present.
- Historical tool-call rows created before this phase do not have approval decision data.

## 24. 2026-05-26: Add Checkpoint/Resume APIs With Strict Validation

**Decision**

- Enhance checkpoint contents with `task_id`, `run_id`, `step`, `memory_snapshot`, `workspace_hash`, `last_trace_event`, and `runtime_metadata`.
- Keep full checkpoint payloads in session artifacts.
- Extend the `checkpoints` table with summary fields for listing and resume validation.
- Add `GET /tasks/{task_id}/checkpoints`.
- Add `POST /tasks/{task_id}/resume`.
- Validate workspace hash, checkpoint schema, and runtime metadata before starting a resumed run.

**Reason**

- Resume needs explicit validation evidence before Pure can claim recoverability.
- The database should index checkpoint summaries, while session artifacts remain the detailed state source.
- Strict validation is safer than attempting implicit checkpoint migration before a migration strategy exists.

**Impact**

- Clients can inspect checkpoints and request resume through the task API.
- Invalid checkpoints return explicit errors and do not start new runs.
- Resume still runs through the existing asynchronous task machinery and `PureRuntime.ask()` loop.
- Checkpoint schema migration remains a future-phase requirement.

## 25. 2026-05-26: Add Project Knowledge Retrieval Layer For Runtime Context Enrichment

**Decision**

- Add `pure/knowledge/` as a project-level Knowledge retrieval layer: document loaders, splitter, embedding providers, vector store abstraction, and `KnowledgeService`.
- Treat Knowledge as Runtime context augmentation, not customer-support Q&A and not a chat-style RAG product.
- Persist the default knowledge index under `.pure/knowledge/index.json`.

**Reason**

- Runtime needs a controlled way to inject relevant project context (README/docs/report summaries) without “infinite document concatenation”.
- A dedicated Knowledge layer keeps `ContextManager` and memory retrieval responsibilities intact while adding a new, auditable context source.

**Impact**

- `PureRuntime.ask()` retrieves knowledge before prompt building and emits `knowledge_retrieved` with sources in trace.
- `report.json` now records `knowledge_sources` for run-level auditability.

## 26. 2026-05-26: Add `knowledge_context` Prompt Section With Independent Budget Control

**Decision**

- Extend `ContextManager` to include a dedicated `knowledge_context` section between `memory` and `relevant_memory`.
- Add a separate budget for `knowledge_context` and include it in the reduction order so it can be clipped predictably under pressure.

**Reason**

- Knowledge context must be budget-bounded and must not crowd out the current user request or recent interaction history.
- A first-class section makes prompt composition explainable via prompt metadata and trace.

**Impact**

- Prompt metadata now includes knowledge section sizes and selected source summaries.
- Tests can enforce token-budget behavior without relying on any external tokenizer or embedding API.

## 27. 2026-05-26: Require Fake/Mock Embeddings For Tests and Dry Runs

**Decision**

- Provide `FakeEmbeddingProvider` as the default embedding provider for tests and dry runs.
- Make embedding selection configurable via `PURE_EMBEDDING_PROVIDER`, defaulting to `fake`/`mock`.

**Reason**

- CI and local tests must not depend on external embedding APIs.
- Deterministic embeddings keep retrieval behavior stable and testable.

**Impact**

- Knowledge indexing and retrieval are fully testable offline.
- The platform can later add a real embedding backend without changing the Knowledge API surface.

## 28. 2026-05-26: Expose Knowledge Index/Search Over HTTP Without Coupling It To Runtime Execution

**Decision**

- Add HTTP endpoints: `POST /knowledge/documents`, `POST /knowledge/index`, and `POST /knowledge/search`.
- Keep these endpoints focused on project knowledge ingestion/index/search; they do not execute tasks or call the model.

**Reason**

- The service layer needs a way to build and inspect the knowledge index independently of task runs.
- Decoupling indexing from execution keeps the Runtime loop stable and avoids hidden side effects.

**Impact**

- Clients can pre-index a project and verify retrieval behavior before starting runs.
- Runtime retrieval remains a separate, auditable step in `PureRuntime.ask()`.

## 29. 2026-05-26: Add Platform Evaluator Without Rewriting Runtime

**Decision**

- Add `pure/evaluator/` with `cases.py`, `runner.py`, `metrics.py`, and `report.py`.
- Define evaluator cases in `eval_cases.json` with `id`, `task`, `expected_tools`, `forbidden_tools`, `success_keywords`, and `max_steps`.
- Execute evaluation cases through `RuntimeService` and the existing `PureRuntime.ask()` loop.
- Write evaluator reports to `.pure/evals/<eval_id>/report.json`.
- Keep evaluator reports out of the metadata database.

**Reason**

- Pure needs a repeatable assessment surface without creating a second runtime implementation.
- Consuming existing trace/report artifacts keeps evaluation aligned with the same evidence chain used by task APIs, metrics, and debugging.
- File-backed evaluator reports are easier to inspect, diff, archive, and regenerate than database-only blobs.

**Impact**

- The evaluator can measure `task_success`, `expected_tool_hit_rate`, `forbidden_tool_count`, `average_steps`, `average_latency`, `checkpoint_created`, and `knowledge_retrieved`.
- Evaluation runs create normal project/task/run metadata and normal run artifacts.
- There is no first-class evaluator database table yet; report discovery after process restart should read `.pure/evals/*` or add a future index.

## 30. 2026-05-26: Require Evaluator Dry Run To Exercise The Full Path

**Decision**

- `dry_run=true` is supported by the full evaluator, not just by isolated unit tests.
- Evaluator dry runs use `FakeModelClient` and must not call a real model provider.
- Dry runs still create tasks, runs, trace/report artifacts, checkpoints, and evaluator reports.

**Reason**

- Tests and local smoke checks need deterministic evaluation without credentials or network access.
- Exercising the complete runtime/evaluator path in dry-run mode prevents persistence or artifact bugs from being hidden behind mocks.

**Impact**

- `POST /eval/run` can be used as a local smoke test.
- CI can cover evaluator behavior without real model calls.
- Eval case success criteria should be written so dry-run behavior is meaningful and explicit.

## 31. 2026-05-26: Add Evaluator HTTP API As A Thin Adapter

**Decision**

- Add `POST /eval/run`.
- Add `GET /eval/{eval_id}/report`.
- Keep HTTP report lookup process-local in `RuntimeService.eval_reports`, while storing the report itself durably on disk.

**Reason**

- The service layer needs a simple way to trigger and inspect evaluations.
- A thin adapter avoids adding scheduler, queue, or database concepts before evaluator lifecycle requirements are clearer.

**Impact**

- The current API can retrieve reports created in the same server process.
- A future phase should add report indexing or filesystem discovery for historical evaluator reports.
- Evaluator execution is synchronous within the current request path.

## 32. 2026-05-26: Add Docker Compose With API And Database Only

**Decision**

- Add `Dockerfile` for the FastAPI app.
- Add `docker-compose.yml` with `api` and `db` services.
- Use PostgreSQL through `PURE_DATABASE_URL=postgresql+psycopg://pure:pure@db:5432/pure` in Compose.
- Do not add Redis.

**Reason**

- Pure now needs a reproducible local deployment shape for API + metadata DB.
- Redis would be misleading because there is no Redis-backed queue, cache, broker, or state path in the current implementation.

**Impact**

- Docker Compose documents the intended local service topology.
- `psycopg[binary]` is a runtime dependency so SQLAlchemy can connect to PostgreSQL in Compose.
- Startup still does not imply production readiness: migrations, auth, CORS, process supervision, and durable job recovery remain future work.

## 33. 2026-05-26: Expand Engineering Docs While Keeping Artifact-First Boundaries

**Decision**

- Add focused docs for architecture, runtime, ToolGateway, Knowledge, Evaluator, and API.
- Rewrite README around runnable commands and current capabilities.
- Align `.env.example` with `PURE_*` environment variables.

**Reason**

- Pure is now more than a CLI runtime; users need accurate docs for API, evaluator, Docker, dry-run, and artifact boundaries.
- Runnable commands reduce drift between documentation and tests.

**Impact**

- The historical `PICO_*` environment-variable mismatch is resolved in the example file.
- Documentation now states that trace/report/evaluator artifacts remain file-backed and are not duplicated into the metadata database.
- Future docs should keep the "enterprise-adjacent design" positioning and avoid claiming production-grade distributed behavior.
