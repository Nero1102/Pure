# Next Phase Suggestions

> Current status note (2026-05-26): the earlier `.env.example` prefix mismatch has been resolved by switching the example file to `PURE_*`. The recommendations below keep the historical notes for context, but the active next-phase focus has moved to migrations, evaluator report discovery, typed API contracts, artifact versioning, and Docker/runtime operational hardening.

> 目标：基于当前真实实现与测试/脚本使用方式，给出下一阶段可执行的工程建议（不包含任何“已实现但未提交”的假设）。

## 1. 优先级建议（建议从上到下）

### Resolved：配置文档一致性

- `.env.example` 已从历史 `PICO_*` 示例切换为当前代码读取的 `PURE_*` 变量。
- 核心类已重命名为 `PureRuntime`，README/Docs 统一使用 `Pure` branding。`Pico` 保留为向后兼容 alias。

### P1：把 artifact schema 明确化与版本化

- 为 `task_state.json`、`report.json`、`trace.jsonl` 增加显式 `schema_version`（或在 report 中补齐），并给出变更策略（向后兼容/迁移脚本）。
- 在 `trace.jsonl` 的 event payload 里约束字段（至少定义 `tool_executed/prompt_built/model_parsed/run_finished` 的稳定字段集合）。

### P1：把“字符预算”升级为更接近 token 的预算

- 维持“分区预算”的设计，但提供可选的 token 估算（例如对不同 provider 以近似策略估算），用于更可靠的超限控制与指标。

### P2：工具扩展与权限模型

- 将 tool registry 设计为可扩展（配置/插件化），并将权限从“全局审批策略”扩展为“按工具/按路径/按命令前缀”的策略（与当前 `approval ask/auto/never` 保持兼容）。
- 对 `run_shell` 引入更强约束（允许命令白名单/前缀规则，或分离出更细粒度工具：`run_tests`、`git_status` 等）。

### P2：增强可观测性与开发体验

- 将 trace/report 的关键字段抽出成稳定的文档（并在 `docs/` 加入 sample 片段）。
- 在 `scripts/` 里补齐“如何生成 metrics 报告”的入口说明（当前 `pure/utils/metrics.py` 已有聚合逻辑）。

## 2. 具体技术债务与建议处理方式

- **配置变量前缀不一致**：已通过 `.env.example` 和 README 更新解决；后续只需决定是否为更早的 `PICO_*` 用户提供迁移说明。
- **命名统一（PureRuntime）**：核心类 `PureRuntime` 已完成重命名（2026-05-26），`Pico` 作为向后兼容 alias 保留。
- **schema 演进缺少统一策略**：建议从 `report.json` 开始，因为它是聚合入口，且 `metrics.py` 已在消费。
- **prompt/token 不一致**：建议先提供“估算值”并写入 report，后续再引入真实 tokenizer（如果要避免新依赖，也可保持估算）。

## 3. 下一阶段可交付物（建议）

- `docs/`：
  - “运行与配置”单页（兼容说明、示例命令、常见错误）
  - “Artifact schema reference”（含 schema_version）
  - “Tool policy reference”（审批与风险等级）
- `code`（若下一阶段允许改业务代码）：
  - schema_version 统一、metrics 对齐
  - tool policy 细化与可配置化
  - 配置命名统一/legacy 迁移策略
## 4. Phase Update: Recommended Next Phase After Metadata DB and Task API (2026-05-25)

The previous recommendations around config consistency, artifact schema versioning, token budgeting, and tool policy still apply. Durable project/task/run metadata now exists, so the next phase should focus on hardening lifecycle semantics and operational behavior.

### P0: Add database migration strategy

- Introduce a lightweight migration plan before evolving the SQLAlchemy schema further.
- Decide whether to use Alembic or a small project-local migration runner.
- Define compatibility behavior for existing `.pure/pure.db` files.

### P0: Clarify task execution semantics

- `POST /tasks` is now metadata-only and `POST /tasks/{task_id}/run` starts queued/background execution.
- Cancellation and status polling exist, so the next decision is durable recovery: what happens to queued/running jobs after process restart.
- Define retry policy, stale run detection, and recovery behavior before introducing any external queue.
- Keep Redis/Celery out until the lifecycle contract requires them.

### P0: Define typed HTTP error responses

- Replace ad hoc `HTTPException` payloads with a stable error envelope such as `{code, message, details}`.
- Cover not found, invalid project path, invalid runtime config, invalid lifecycle transition, exhausted mock outputs, DB write failures, and artifact read errors.
- Add TestClient coverage for error cases.

### P1: Formalize API schemas, examples, and filters

- Keep Pydantic schemas as the source for HTTP request/response shape.
- Add OpenAPI examples for `/projects`, `/tasks`, `/runs`, `/sessions`, `/ask`, `/trace`, `/report`, and `/tools`.
- Document `runtime_config` as a stable contract instead of an open-ended dict.
- Add list/filter endpoints for projects, tasks, and runs if the UI or automation layer needs them.

### P1: Expand service model-client assembly

- Decide whether HTTP should reuse CLI provider configuration code or own a separate server configuration module.
- Support real provider configuration intentionally, including secret handling and explicit dry-run defaults.
- Avoid silently calling real model APIs in tests; keep mock/dry-run paths first-class.

### P1: Add artifact schema versioning

- Add explicit schema versions for `task_state.json`, `trace.jsonl`, and `report.json`.
- Treat the standardized TraceEvent shape as the current trace contract, but still add a version marker for future evolution.
- Define backwards compatibility behavior for API reads.
- Ensure metrics and API consumers agree on versioned fields.

### P2: Improve DB/artifact consistency

- Define recovery behavior when DB metadata exists but trace/report files are missing.
- Define recovery behavior when artifacts exist but DB rows were not created, especially for legacy session ask flows.
- Consider a one-shot indexing command for historical `.pure/runs/*` artifacts.

### P2: Improve operational boundaries

- Add CORS/auth decisions before exposing the service beyond local development.
- Add structured logging for HTTP request id, session id, and run id.
- Add startup checks for project path validity and dependency availability.

## 5. Phase Update: Recommended Next Phase After Async Runtime and Trace Standardization (2026-05-26)

The async task API and standard TraceEvent schema are now implemented. The next phase should harden durability, recovery, and API ergonomics around the new lifecycle rather than adding a heavier worker stack immediately.

### P0: Durable job recovery and stale run handling

- Define how the server treats DB rows with `queued` or `running` status on startup.
- Add a recovery command or startup policy to mark stale runs failed/cancelled with an explanatory trace event.
- Decide whether local background jobs should have heartbeat timestamps in the database.
- Keep the implementation single-process unless product requirements demand distributed workers.

### P0: Typed lifecycle transitions

- Centralize allowed task/run state transitions instead of setting raw status strings from multiple service methods.
- Add explicit transition errors for invalid states such as running a cancelled task or cancelling a completed run.
- Extend tests for repeated run requests, repeated cancel requests, failed mock outputs, missing artifacts, and status polling during failure.

### P1: Artifact schema versioning

- Add `schema_version` to `task_state.json`, `report.json`, and trace events.
- Document compatibility rules for old `event/created_at` traces and current TraceEvent fields.
- Update metrics readers to prefer standard TraceEvent fields while retaining legacy fallback.

### P1: Provider/runtime interruption model

- Decide whether cancellation should remain cooperative or attempt to interrupt subprocesses/provider calls.
- If stronger cancellation is needed, isolate cancellable tool execution and provider timeouts before adding external queues.
- Ensure dry-run/mock behavior exercises cancellation and failure trace paths.

### P1: HTTP error envelope and OpenAPI examples

- Standardize API errors as `{code, message, details}`.
- Add examples for task creation, async run start, status polling, cancellation, trace, and dry-run.
- Document `runtime_config` keys as a stable contract instead of leaving it as an opaque dict.

### P2: Historical artifact indexing

- Add a one-shot command to index existing `.pure/runs/*` artifacts into the metadata database.
- Define behavior for artifacts without DB rows and DB rows without artifacts.
- Decide whether legacy `/sessions/{session_id}/ask` runs should be indexed automatically or remain artifact-first.

## 6. Phase Update: Recommended Next Phase After Tool Gateway and Checkpoint/Resume (2026-05-26)

Tool Gateway and checkpoint/resume are now implemented. The next phase should harden schema evolution, approval workflow, and recovery behavior rather than adding a distributed worker stack immediately.

### P0: Add database migrations

- Introduce Alembic or a small project-local migration runner before the metadata schema changes again.
- Cover new Phase8 columns: `tool_calls.approval_decision`, `checkpoints.step`, `checkpoints.memory_snapshot`, `checkpoints.last_trace_event`, `checkpoints.runtime_metadata`, and `checkpoints.schema_version`.
- Define how existing `.pure/pure.db` files are upgraded or marked incompatible.

### P0: Formalize checkpoint schema evolution

- Decide whether `phase1-v1` remains the checkpoint artifact schema name after the Phase8 field additions or whether a migration to a new explicit schema version is required.
- Add checkpoint migration/compatibility tests for old session artifacts.
- Define resume behavior for missing `workspace_hash`, missing `runtime_metadata`, and older checkpoint objects.

### P0: Add an approval continuation API

- `approval_mode=manual` now returns `waiting_approval` for high-risk tools.
- Add a first-class API for listing pending approvals, approving/denying a specific tool call, and resuming execution after approval.
- Decide whether pending approval state belongs in trace artifacts, the database, or both.

### P1: Type `runtime_config`

- Replace opaque `dict[str, Any]` request fields with a typed Pydantic model.
- Document defaults and validation for `approval_policy`, `approval_mode`, `read_only`, `max_steps`, `max_new_tokens`, `feature_flags`, and `mock_outputs`.
- Keep tests from ever calling a real provider unless explicitly configured.

### P1: Improve resume semantics

- Define whether resume should reconstruct prior memory/checkpoint state into the new session, or only validate the checkpoint and start a new run with the task prompt.
- Add clear user-facing status for `full-valid`, `schema-mismatch`, `workspace-mismatch`, and `partial-stale` checkpoint states at the task API layer.
- Add richer tests for resume after tool execution, resume after workspace mutation, and resume with manual approval pending.

### P1: Strengthen shell policy without breaking developer workflows

- Current shell policy blocks obvious path escapes while allowing external executable paths.
- Add configurable command allowlists or purpose-built tools such as `run_tests`, `git_status`, and `git_diff`.
- Keep `run_shell` available for local development, but make safer specialized tools the preferred path for platform use.

### P2: Operational observability

- Add structured logs for run id, task id, checkpoint id, approval decision, and policy rejection reason.
- Add API examples for tool audit, checkpoint list, and resume failure responses.
- Add a compact admin/status endpoint for process-local jobs, queued/running DB rows, and stale run candidates.

## 7. Phase Update: Recommended Next Phase After Project Knowledge Retrieval Layer (2026-05-26)

Project-level Knowledge retrieval is now implemented and integrated into Runtime context building. The next phase should harden index lifecycle, configurability, and observability without turning Pure into a chat RAG product.

### P0: Index lifecycle + invalidation strategy

- Define when the knowledge index should be rebuilt: workspace fingerprint change, explicit API call, or scheduled maintenance.
- Record index metadata (workspace fingerprint, indexed paths, chunk params, embedding dimensions, schema version) alongside `.pure/knowledge/index.json`.
- Add a clear “index stale” signal in trace/report when the index is missing or inconsistent.

### P0: Pluggable vector store backends (still test-safe)

- Keep `VectorStore` as the stable interface; add optional adapters for FAISS/Chroma behind feature flags or optional dependencies.
- Ensure unit tests continue to use fake embeddings + in-memory/JSON vector store; never require external services.

### P1: Retrieval quality and context strategy

- Add a clearer chunking policy for markdown (headings, code blocks) to reduce noisy chunks.
- Improve deduplication across similar chunks and normalize source paths so `knowledge_sources` stays stable.
- Add a per-run `knowledge_budget_chars` and `top_k` to `runtime_config` (typed model) so behavior is configurable but bounded.

### P1: API contract hardening

- Add examples for `/knowledge/*` endpoints in `docs/api-contract.md` and define error shapes for invalid project id, missing docs, and empty index.
- Decide whether knowledge search results should include raw content, excerpts only, or both (privacy/safety tradeoff).

### P2: Artifact schema versioning (knowledge-aware)

- Extend the artifact schema-versioning plan to include `knowledge_sources` in `report.json` and a stable trace payload shape for `knowledge_retrieved`.
- Add migration/compatibility notes for older runs without knowledge fields.

## 8. Phase Update: Recommended Next Phase After Evaluator, Docker, and Docs (2026-05-26)

The evaluator, Docker packaging, README, `.env.example`, and focused engineering docs are now in place. The next phase should harden lifecycle, schema, and operational behavior without overstating production readiness.

### P0: Add database migrations

- Introduce Alembic or a small project-local migration runner before changing the metadata schema again.
- Cover SQLite local development and PostgreSQL Docker Compose usage.
- Define startup behavior when the configured database exists but is missing newer columns.

### P0: Add evaluator report discovery/indexing

- Current `GET /eval/{eval_id}/report` can read reports created in the same process.
- Add filesystem discovery for `.pure/evals/<eval_id>/report.json`, or add an evaluator metadata table if query/filter requirements justify it.
- Keep full evaluator reports as file artifacts even if summary indexing is added.

### P0: Type `runtime_config`

- Replace open `dict[str, Any]` request fields with a Pydantic model.
- Document and validate `approval_policy`, `approval_mode`, `read_only`, `max_steps`, `max_new_tokens`, `feature_flags`, and `mock_outputs`.
- Add evaluator-specific validation for case `max_steps` and dry-run defaults.

### P1: Artifact schema versioning

- Add explicit schema versions to `task_state.json`, `trace.jsonl`, and `report.json`.
- Keep evaluator report `schema_version` and document compatibility expectations.
- Update metrics and API readers to prefer versioned standard fields while preserving legacy fallback.

### P1: Docker startup and smoke workflow

- Decide whether the API container should run `python -m pure.db.init_db` on startup or require an explicit setup command.
- Add a documented Docker smoke path: health check, create project, create dry-run task, run evaluator.
- Keep Redis out unless a real queue/cache design is introduced.

### P1: HTTP error envelope and OpenAPI examples

- Standardize errors as `{code, message, details}`.
- Add examples for evaluator endpoints, knowledge endpoints, task lifecycle endpoints, checkpoint/resume, and Docker dry-run smoke.
- Keep docs and README commands runnable.

### P2: Operational boundaries

- Add local-only auth/CORS decisions before exposing the API beyond development.
- Add structured logs with task id, run id, eval id, checkpoint id, and approval decision.
- Define stale queued/running job recovery after API restart.
