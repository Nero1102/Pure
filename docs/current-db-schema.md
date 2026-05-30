# Current Database and Artifact Schema

> Pure now uses a SQLAlchemy metadata database plus the existing `.pure/` artifact store. This document describes both layers and the boundary between them.

## 1. 根目录：`.pure/`

典型结构：

```
.pure/
├─ sessions/
│  └─ <session_id>.json
├─ runs/
│  └─ <run_id>/
│     ├─ task_state.json
│     ├─ trace.jsonl
│     └─ report.json
└─ memory/
   ├─ MEMORY.md
   └─ topics/
      └─ <topic>.md
└─ knowledge/
   └─ index.json
```

## 2. `sessions/<session_id>.json`（SessionStore）

来源：`pure/core/runtime.py` 初始化 `self.session` 与 `_ensure_session_shape()`。

### 2.1 顶层字段（当前实现）

- `id`：session id（形如 `YYYYmmdd-HHMMSS-<hex>`）
- `created_at`：ISO8601（UTC）
- `workspace_root`：workspace 根目录路径字符串
- `history`：事件列表（role: `user|assistant|tool`）
- `memory`：工作记忆状态（见下）
- `checkpoints`：
  - `current_id`：当前 checkpoint id
  - `items`：`{ checkpoint_id: checkpoint_object }`
- `runtime_identity`：上次运行时身份快照（用于 mismatch 检测）
- `resume_state`：resume 状态（由 `CheckpointService.evaluate_resume_state()` 写入）

### 2.2 `history` 元素形状（关键字段）

- user/assistant：
  - `role`: `"user"` / `"assistant"`
  - `content`: string
  - `created_at`: ISO8601
- tool：
  - `role`: `"tool"`
  - `name`: tool name
  - `args`: dict
  - `content`: tool 返回文本
  - `created_at`: ISO8601

### 2.3 `memory`（工作记忆）形状（默认结构）

来源：`pure/core/memory.py:default_memory_state()`。

- `working.task_summary`: string
- `working.recent_files`: string[]
- `episodic_notes`: note[]
- `file_summaries`: `{ path: summary_text }`
- `task`: string
- `files`: string[]
- `notes`: string[]
- `next_note_index`: int

> durable memory（跨会话）不在 session json 内，而在 `.pure/memory/` 下。

### 2.4 `checkpoints.items.<checkpoint_id>` 结构（CheckpointService）

来源：`pure/services/checkpoint_service.py:create_checkpoint()`。

- `checkpoint_id` / `parent_checkpoint_id`
- `schema_version`：当前为 `phase1-v1`（`CHECKPOINT_SCHEMA_VERSION`）
- `created_at`
- `current_goal`
- `completed`: string[]（通常收录 final_answer）
- `excluded`: string[]
- `current_blocker`: string（stop_reason 的解释性字段）
- `next_step`: string（由 `infer_next_step()` 推断）
- `key_files`: `{path,freshness}[]`（基于 recent_files）
- `freshness`: `{ path: freshness_value }`
- `summary`: string
- `runtime_identity`: dict（见下）

## 3. `runs/<run_id>/task_state.json`（TaskState）

来源：`pure/core/task_state.py:TaskState.to_dict()`。

- `run_id`
- `task_id`
- `user_request`
- `status`: `running|completed|stopped|failed`
- `tool_steps`: int
- `attempts`: int
- `last_tool`: string
- `stop_reason`: string（枚举见 `task_state.py`）
- `final_answer`: string
- `checkpoint_id`: string
- `resume_status`: string

## 4. `runs/<run_id>/trace.jsonl`（事件流）

来源：`pure/core/runtime.py:emit_trace()`。

### 4.1 每行 JSON 的通用字段

- `event`: string（事件名）
- `created_at`: ISO8601
- 其余字段：随事件不同变化（payload 会先做 redact）

### 4.2 当前代码中出现的事件名（非穷举，但来自实际调用点）

- `run_started`
- `prompt_built`
- `runtime_identity_mismatch`
- `model_requested`
- `model_parsed`
- `tool_executed`
- `checkpoint_created`
- `run_finished`

## 5. `runs/<run_id>/report.json`（结果摘要）

来源：`pure/core/runtime.py:build_report()`。

- `run_id` / `task_id`
- `status` / `stop_reason` / `final_answer`
- `tool_steps` / `attempts`
- `checkpoint_id` / `resume_status`
- `task_state`: task_state dict（镜像）
- `prompt_metadata`: dict（由 `PromptService` 产出并融合 provider completion metadata）
- `knowledge_sources`: Knowledge 检索来源列表（每项包含 `source/score/metadata` 等摘要）
- `durable_promotions` / `durable_rejections` / `durable_superseded`: string[]
- `redacted_env`: secret 环境变量摘要（用于报告脱敏）

## 6. `.pure/memory/`（DurableMemoryStore）

来源：`pure/core/memory.py:DurableMemoryStore`。

### 6.1 `MEMORY.md`（主题索引）

- Markdown 列表项形如：
  - `- [topic-slug](topics/topic-slug.md): Topic Title`
  - `  - summary: ...`
  - `  - tags: a, b, c`

### 6.2 `topics/<topic>.md`（主题条目）

包含 meta 与 `## Notes` 下的条目列表；解析规则在 `load_topic_notes()`：

- `- tags: ...` / `- updated_at: ...` 会作为后续 notes 的 meta
- `## Notes` 后每条 `- ...` 会被解析为 note（kind: `"durable"`）

默认主题（代码内置）：`project-conventions`、`key-decisions`、`dependency-facts`、`user-preferences`。

## 7. Legacy 迁移：`.pico/` -> `.pure/`

来源：`pure/utils/migration.py:migrate_legacy_pico_artifacts()`。

- 只迁移已知子目录：`runs`、`sessions`、`memory`
- 安全策略：拒绝触碰 workspace root 之外路径；尽量 move/merge，未知文件保留在 `.pico/`
## 8. Historical Phase Note: HTTP Service State Before the Database (2026-05-25)

The first FastAPI service phase used only in-memory HTTP indexes. That is no longer the current architecture after the database/task-system phase, but this note is kept for historical context.

The old in-memory `run_id -> session_id` lookup has been superseded for task-created runs by the `runs` table. Live `SessionHandle` objects still exist inside `RuntimeService` while a process is running, but they are not the durable source of project/task/run metadata.

### 8.1 Superseded schema debt

- The persisted `run_id` to `session_id` relationship now lives in the `runs` table.
- `trace.jsonl`, `report.json`, and `task_state.json` still lack a unified top-level `schema_version`.
- HTTP response schemas are Pydantic models, but persisted artifact schemas are still documented rather than enforced by generated validators.

## 9. Current Metadata Database Schema (2026-05-25)

Pure now includes a traditional metadata database under `pure/db/`.

Default local database:

```text
.pure/pure.db
```

Initialization:

```bash
python -m pure.db.init_db
```

Configuration:

- Default: SQLite via `.pure/pure.db`.
- Override: `PURE_DATABASE_URL` or an explicit `--database-url`.
- Design target: SQLAlchemy 2.x models compatible with SQLite, PostgreSQL, and MySQL.

### 9.1 Design boundary

The database stores platform metadata only:

- project records
- task lifecycle state
- run lifecycle state
- trace/report file paths
- tool-call summaries
- checkpoint indexes

The database does not store full `trace.jsonl` or full `report.json` payloads. Those remain owned by `RunStore` under `.pure/runs/<run_id>/`.

### 9.2 Tables

```text
projects
  id PK
  name
  root_path
  description
  created_at
  updated_at

tasks
  id PK
  project_id FK -> projects.id
  title
  prompt
  status
  priority
  created_at
  updated_at

runs
  id PK
  task_id FK -> tasks.id
  session_id
  status
  started_at
  ended_at
  trace_path
  report_path
  error
  total_steps
  token_usage_summary

tool_calls
  id PK
  run_id FK -> runs.id
  step
  tool_name
  args_json
  result_summary
  status
  latency_ms
  risk_level
  created_at

checkpoints
  id PK
  run_id FK -> runs.id
  task_id FK -> tasks.id
  checkpoint_path
  workspace_hash
  created_at
```

### 9.3 Relationships

```text
Project 1 -> N Task
Task    1 -> N Run
Run     1 -> N ToolCall
Run     1 -> N Checkpoint
Task    1 -> N Checkpoint
```

### 9.4 Lifecycle values

Task status:

```text
created -> queued -> running -> completed | failed | cancelled
```

`cancelled` is now used by `POST /tasks/{task_id}/cancel`.

Run status:

```text
created | queued -> running -> completed | failed | cancelled
```

### 9.5 Repository ownership

The API layer uses repositories instead of writing SQLAlchemy statements directly:

- `ProjectRepository`
- `TaskRepository`
- `RunRepository`
- `ToolCallRepository`
- `CheckpointRepository`

### 9.6 Current schema debt

- No migration framework is present yet; `init_db` creates the current schema directly.
- `token_usage_summary` and `args_json` are JSON-encoded text for cross-database compatibility.
- The metadata DB has no uniqueness policy for duplicate projects with the same `root_path`.
- Historical run discovery is now durable for DB-created tasks, but legacy `/sessions/{id}/ask` runs are still primarily live-session/artifact based unless explicitly indexed by a task flow.

## 10. Current TraceEvent Schema (2026-05-26)

`runs/<run_id>/trace.jsonl` remains a JSONL artifact under `.pure/runs/<run_id>/`, but events are now standardized by `pure/services/trace_service.py`.

Each event includes the standard fields below:

```text
run_id       string
step         int
event_type   string
timestamp    ISO8601 string
payload      object
latency_ms   int
status       string
```

For compatibility, the persisted event also keeps:

```text
event        legacy/original event name
created_at   legacy timestamp alias
```

Event-specific payload fields are still mirrored at top level for older artifact consumers. Examples include `prompt_metadata`, `checkpoint_id`, `trigger`, `name`, `args`, `result`, `tool_status`, and `final_answer`.

### 10.1 Standard event types

```text
run_started
context_built
model_called
tool_requested
tool_validated
tool_executed
memory_updated
checkpoint_created
knowledge_retrieved
run_completed
run_failed
run_cancelled
```

Legacy event aliases are normalized on API read:

```text
prompt_built              -> context_built
model_requested           -> model_called
model_parsed              -> model_called
run_finished              -> run_completed or run_failed, depending on status
runtime_identity_mismatch -> knowledge_retrieved
```

### 10.2 API trace response

`GET /runs/{run_id}/trace` returns:

```json
{
  "run_id": "run_...",
  "events": [
    {
      "run_id": "run_...",
      "step": 0,
      "event_type": "run_started",
      "timestamp": "...",
      "payload": {},
      "latency_ms": 0,
      "status": "ok",
      "event": "run_started",
      "created_at": "..."
    }
  ]
}
```

### 10.3 Task and run lifecycle artifacts

Task-created runs now use the database as the current lifecycle index, while artifacts remain the audit record:

- task/run metadata: `.pure/pure.db`
- task status polling: `GET /tasks/{task_id}/status`
- run evidence: `.pure/runs/<run_id>/task_state.json|trace.jsonl|report.json`
- cancellation evidence: database status plus a `run_cancelled` trace event when a trace path is available

`task_state.json` can now record `cancelled` in addition to `running|completed|stopped|failed` when cancellation is applied to an active runtime state.

### 10.4 Remaining schema debt

- `trace.jsonl` has standardized fields but no explicit top-level `schema_version` yet.
- `task_state.json` and `report.json` still do not have a unified artifact schema version.
- Cancellation is represented in DB state and trace, but interrupted blocking tool/provider calls are not forcibly stopped at the OS/process boundary.

## 11. Phase Update: Tool Audit and Checkpoint Resume Schema (2026-05-26)

This section supersedes the table sketches above where they omit Tool Gateway audit fields or enhanced checkpoint summary fields.

### 11.1 Updated `tool_calls` table

```text
tool_calls
  id PK
  run_id FK -> runs.id
  step
  tool_name
  args_json
  result_summary
  status
  latency_ms
  risk_level
  approval_decision
  created_at
```

`approval_decision` records gateway policy outcomes such as `approved`, `denied`, `denied_readonly`, `waiting_approval`, or `not_requested`.

### 11.2 Updated `checkpoints` table

```text
checkpoints
  id PK
  run_id FK -> runs.id
  task_id FK -> tasks.id
  checkpoint_path
  workspace_hash
  step
  memory_snapshot
  last_trace_event
  runtime_metadata
  schema_version
  created_at
```

The JSON-like columns are stored as JSON-encoded text for cross-database compatibility:

- `memory_snapshot`
- `last_trace_event`
- `runtime_metadata`

The database does not store the complete checkpoint object as the source of truth. The full checkpoint still lives inside `.pure/sessions/<session_id>.json`; the database row is an index and resume-validation summary.

### 11.3 Current checkpoint object additions

`sessions/<session_id>.json` checkpoint items now include the previous fields plus:

- `task_id`
- `run_id`
- `step`
- `memory_snapshot`
- `workspace_hash`
- `last_trace_event`
- `runtime_metadata`

`schema_version` remains `phase1-v1` for compatibility with existing artifact tests and historical sessions. The schema content has been extended while the broader artifact versioning strategy remains a technical debt item.

### 11.4 Resume validation

`POST /tasks/{task_id}/resume` validates checkpoint metadata before starting a new run:

- checkpoint exists and belongs to the task
- checkpoint schema matches the runtime-supported schema
- workspace hash matches the current workspace fingerprint
- runtime metadata is parseable and compatible with the current dry-run/mock runtime path

Validation failures return HTTP `409` with explicit reasons. No run is created for an invalid checkpoint.

### 11.5 Remaining schema debt

- No migration framework exists for existing `.pure/pure.db` files.
- `memory_snapshot`, `last_trace_event`, and `runtime_metadata` are JSON-encoded text rather than native JSON columns.
- `task_state.json`, `report.json`, and `trace.jsonl` still lack a unified top-level artifact schema version.
- Checkpoint schema compatibility is strict; there is not yet a checkpoint migration layer.

## 12. Phase Update: Evaluator Artifacts and Docker Database Configuration (2026-05-26)

### 12.1 Evaluator artifact schema

Evaluator reports are stored as files, not database rows:

```text
.pure/evals/<eval_id>/report.json
```

Current report shape:

```json
{
  "schema_version": 1,
  "eval_id": "eval_...",
  "created_at": "...",
  "project_path": "D:/path/to/repo",
  "cases_path": "D:/path/to/repo/eval_cases.json",
  "dry_run": true,
  "summary": {
    "case_count": 2,
    "task_success": 1.0,
    "expected_tool_hit_rate": 0.0,
    "forbidden_tool_count": 0,
    "average_steps": 0.0,
    "average_latency": 10.0,
    "checkpoint_created": 1.0,
    "knowledge_retrieved": 1.0
  },
  "rows": [
    {
      "id": "dry_run_runtime_smoke",
      "task_id": "task_...",
      "run_id": "run_...",
      "status": "completed",
      "expected_tools": [],
      "forbidden_tools": ["run_shell"],
      "success_keywords": ["Dry run"],
      "max_steps": 2,
      "final_answer": "Dry run: no LLM API called.",
      "metrics": {}
    }
  ]
}
```

The evaluator uses normal platform metadata for each case run:

- `projects`
- `tasks`
- `runs`
- `tool_calls`
- `checkpoints`

There is no `evals` table in the current metadata schema.

### 12.2 Docker database configuration

Local default remains SQLite:

```text
sqlite:///.pure/pure.db
```

Docker Compose sets:

```text
PURE_DATABASE_URL=postgresql+psycopg://pure:pure@db:5432/pure
```

The SQLAlchemy models remain the single metadata schema. PostgreSQL is used by Compose for the `db` service; no Redis schema or cache state exists.

### 12.3 Current schema debt

- Database migrations are still absent.
- Evaluator report files have `schema_version`, but run artifacts still do not share one unified artifact versioning strategy.
- Evaluator report discovery after process restart requires reading `.pure/evals/*`; the current HTTP lookup map is process-local.
- The database stores evaluator case run metadata indirectly through normal task/run rows, not as first-class evaluation entities.
