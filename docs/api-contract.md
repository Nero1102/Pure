# API Contract (HTTP / CLI / Tools / Providers)

> Phase update (2026-05-26): Pure includes a FastAPI service layer, a SQLAlchemy metadata database, asynchronous task execution, cancellation, standardized trace events, a Tool Gateway, checkpoint/resume task APIs, project-level Knowledge retrieval, and a dry-run-capable Evaluator. Any older statement in this document saying Pure is not an HTTP service, that task execution is synchronous, or that tool policy is only the legacy `risky` flag is historical and superseded by the HTTP/tool/evaluator contracts below. CLI, tool, and provider contracts remain valid.

## 0. HTTP Server Contract: FastAPI

Entry point: `pure.server.main:app`

Local startup:

```bash
uvicorn pure.server.main:app --reload
```

Current phase constraints:

- SQLAlchemy 2.x metadata database is available. SQLite at `.pure/pure.db` is the default local database.
- No Redis, Celery, WebSocket streaming, broker, or external task queue.
- `POST /tasks/{task_id}/run` uses the FastAPI async route plus `asyncio`/thread executor dispatch in the current process. It is a local background execution mechanism, not a durable distributed queue.
- Live session handles remain in memory, while project/task/run metadata is durable in the database.
- Trace/report artifacts still come from `SessionStore` and `RunStore`.
- `dry_run` and `runtime_config.mock_outputs` use `FakeModelClient` and do not call a real model API.
- Evaluator reports are file artifacts under `.pure/evals/<eval_id>/report.json`; there is no evaluator table in the current metadata database.
- Docker Compose includes only `api` and `db`; Redis is intentionally absent because Pure does not use it.

### 0.1 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check. |
| `POST` | `/projects` | Create a database-backed project record. |
| `GET` | `/projects/{project_id}` | Read a project record. |
| `POST` | `/tasks` | Create a database-backed task record. Does not execute it. |
| `GET` | `/tasks/{task_id}` | Read task metadata and the latest run id. |
| `POST` | `/tasks/{task_id}/run` | Create a run and start task execution asynchronously. Returns immediately. |
| `GET` | `/tasks/{task_id}/status` | Read task lifecycle state, current run, current step, last trace event, and checkpoint count. |
| `POST` | `/tasks/{task_id}/cancel` | Mark a running/queued task cancelled and write a cancellation trace event when possible. |
| `GET` | `/tasks/{task_id}/checkpoints` | List indexed checkpoints for a task. |
| `POST` | `/tasks/{task_id}/resume` | Validate a checkpoint and start a new asynchronous run from the task context. |
| `POST` | `/sessions` | Create a runtime session for a workspace. |
| `GET` | `/sessions/{session_id}` | Read session metadata, status, memory summary, and checkpoint count. |
| `POST` | `/sessions/{session_id}/ask` | Run one task through `RuntimeService.run_task()` and the existing `PureRuntime.ask()` loop. |
| `GET` | `/runs/{run_id}` | Read database-backed run metadata. |
| `GET` | `/runs/{run_id}/trace` | Return structured events parsed from `trace.jsonl`. |
| `GET` | `/runs/{run_id}/report` | Return `report.json`. |
| `GET` | `/tools` | Return tool name, argument schema, and risk level. |
| `POST` | `/knowledge/documents` | Add one or more documents into the project knowledge index. |
| `POST` | `/knowledge/index` | Index project documents (README/docs/report summaries) into the knowledge index. |
| `POST` | `/knowledge/search` | Search the knowledge index and return top-k results. |
| `POST` | `/eval/run` | Run evaluator cases through the normal Runtime path and write an evaluator report. |
| `GET` | `/eval/{eval_id}/report` | Return the evaluator report for an in-process evaluation id. |

Knowledge is used for Runtime context enrichment (not customer-support Q&A and not a chat-style RAG product). The default embedding provider is deterministic fake/mock for tests and dry runs.

### 0.2 `POST /projects`

Request:

```json
{
  "name": "Pure",
  "root_path": "D:/path/to/repo",
  "description": "Local runtime workspace"
}
```

Response:

```json
{
  "id": "project_...",
  "name": "Pure",
  "root_path": "D:/path/to/repo",
  "description": "Local runtime workspace",
  "created_at": "...",
  "updated_at": "..."
}
```

### 0.3 `POST /tasks`

Request:

```json
{
  "project_id": "project_...",
  "title": "Inspect repository",
  "prompt": "Inspect the repo without calling a real model.",
  "priority": 0,
  "runtime_config": {
    "approval_policy": "auto",
    "max_steps": 2
  },
  "dry_run": true
}
```

Response:

```json
{
  "id": "task_...",
  "project_id": "project_...",
  "title": "Inspect repository",
  "prompt": "Inspect the repo without calling a real model.",
  "status": "created",
  "priority": 0,
  "created_at": "...",
  "updated_at": "...",
  "run_id": null
}
```

`runtime_config` and `dry_run` are accepted for compatibility with the request model, but execution options are applied by `POST /tasks/{task_id}/run`.

### 0.4 `POST /tasks/{task_id}/run`

Request:

```json
{
  "runtime_config": {
    "approval_policy": "auto",
    "max_steps": 2,
    "mock_outputs": ["<final>Done.</final>"]
  },
  "dry_run": true
}
```

Response:

```json
{
  "run_id": "run_...",
  "status": "queued"
}
```

Lifecycle:

```text
Task: created -> queued -> running -> completed | failed | cancelled
Run:  created|queued -> running -> completed | failed | cancelled
```

The endpoint returns before `PureRuntime.ask()` completes. `dry_run` still creates database records and trace/report artifacts.

### 0.5 `GET /tasks/{task_id}/status`

Response shape:

```json
{
  "task_id": "task_...",
  "status": "running",
  "current_run": {"run_id": "run_...", "status": "running"},
  "current_step": 1,
  "last_trace_event": {
    "run_id": "run_...",
    "step": 1,
    "event_type": "tool_executed",
    "timestamp": "...",
    "payload": {},
    "latency_ms": 12,
    "status": "ok",
    "event": "tool_executed",
    "created_at": "..."
  },
  "checkpoint_count": 1
}
```

### 0.6 `POST /knowledge/index`

Request:

```json
{
  "project_id": "project_...",
  "paths": ["README.md", "docs"],
  "reset": true
}
```

Response:

```json
{
  "project_id": "project_...",
  "document_count": 2,
  "chunk_count": 10
}
```

### 0.7 `POST /knowledge/search`

Request:

```json
{
  "project_id": "project_...",
  "query": "sqlite metadata",
  "top_k": 5,
  "budget_chars": 1400
}
```

Response:

```json
{
  "project_id": "project_...",
  "results": [
    {"content": "...", "source": "README.md", "score": 0.9, "metadata": {"chunk_index": 0}}
  ]
}
```

### 0.8 `POST /eval/run`

Request:

```json
{
  "project_path": ".",
  "cases_path": "eval_cases.json",
  "runtime_config": {
    "approval_policy": "auto",
    "max_steps": 2
  },
  "dry_run": true
}
```

Response:

```json
{
  "eval_id": "eval_...",
  "status": "completed",
  "report_path": "D:/path/to/repo/.pure/evals/eval_.../report.json",
  "summary": {
    "case_count": 2,
    "task_success": 1.0,
    "expected_tool_hit_rate": 0.0,
    "forbidden_tool_count": 0,
    "average_steps": 0.0,
    "average_latency": 10.0,
    "checkpoint_created": 1.0,
    "knowledge_retrieved": 1.0
  }
}
```

`dry_run=true` is the default. The evaluator still creates projects, tasks, runs, trace/report artifacts, checkpoints, and a final evaluator report; it must not call a real model.

### 0.9 `GET /eval/{eval_id}/report`

Response shape:

```json
{
  "schema_version": 1,
  "eval_id": "eval_...",
  "created_at": "...",
  "project_path": "D:/path/to/repo",
  "cases_path": "D:/path/to/repo/eval_cases.json",
  "dry_run": true,
  "summary": {},
  "rows": []
}
```

The current lookup is process-local through `RuntimeService.eval_reports`; the report itself is durable on disk.

### 0.10 `POST /tasks/{task_id}/cancel`

Response shape:

```json
{
  "task_id": "task_...",
  "run_id": "run_...",
  "status": "cancelled"
}
```

Cancellation is cooperative at the service boundary. The task/run metadata is marked `cancelled`; if the runtime has an active task state, Pure writes a `run_cancelled` trace event and updates `task_state.json`. Work already inside a blocking provider/tool call may finish before the cancellation mark is observed.

### 0.11 `GET /tasks/{task_id}/checkpoints`

Response shape:

```json
{
  "task_id": "task_...",
  "checkpoints": [
    {
      "id": "ckpt_...",
      "run_id": "run_...",
      "task_id": "task_...",
      "checkpoint_path": "D:/path/to/repo/.pure/sessions/<session_id>.json",
      "workspace_hash": "...",
      "step": 1,
      "schema_version": "phase1-v1",
      "last_trace_event": {},
      "runtime_metadata": {},
      "created_at": "..."
    }
  ]
}
```

The database stores checkpoint metadata and summaries only. The full checkpoint object remains inside the session artifact.

### 0.12 `POST /tasks/{task_id}/resume`

Request:

```json
{
  "checkpoint_id": "ckpt_...",
  "runtime_config": {
    "approval_mode": "auto",
    "max_steps": 2
  },
  "dry_run": true
}
```

Response:

```json
{
  "run_id": "run_...",
  "status": "queued"
}
```

If `checkpoint_id` is omitted, the latest checkpoint for the task is used. Resume validation checks checkpoint schema, workspace hash, and runtime metadata before starting a run. Invalid checkpoints return `409` with explicit reasons such as `workspace hash mismatch` or `checkpoint schema mismatch`.

### 0.13 `GET /runs/{run_id}`

Response shape:

```json
{
  "id": "run_...",
  "task_id": "task_...",
  "session_id": "20260525-120000-abcdef",
  "status": "completed",
  "started_at": "...",
  "ended_at": "...",
  "trace_path": "D:/path/to/repo/.pure/runs/run_.../trace.jsonl",
  "report_path": "D:/path/to/repo/.pure/runs/run_.../report.json",
  "error": "",
  "total_steps": 0,
  "token_usage_summary": {}
}
```

### 0.14 `POST /sessions`

Request:

```json
{
  "project_path": "D:/path/to/repo",
  "runtime_config": {
    "approval_policy": "auto",
    "max_steps": 6,
    "max_new_tokens": 512,
    "read_only": false,
    "mock_outputs": ["<final>Done.</final>"]
  },
  "dry_run": true
}
```

Response:

```json
{
  "session_id": "20260525-120000-abcdef",
  "status": "created"
}
```

### 0.15 `GET /sessions/{session_id}`

Response shape:

```json
{
  "session_id": "...",
  "status": "idle|running|completed|stopped|failed",
  "metadata": {
    "created_at": "...",
    "workspace_root": "...",
    "history_count": 0,
    "run_count": 0
  },
  "memory_summary": {
    "task_summary": "",
    "recent_files": [],
    "note_count": 0
  },
  "checkpoint_count": 0
}
```

### 0.16 `POST /sessions/{session_id}/ask`

Request:

```json
{
  "prompt": "Inspect the repo.",
  "dry_run": true
}
```

Response:

```json
{
  "run_id": "run_20260525-120001-abcdef",
  "status": "completed",
  "report_path": "D:/path/to/repo/.pure/runs/run_.../report.json"
}
```

### 0.17 `GET /runs/{run_id}/trace`

Response shape:

```json
{
  "run_id": "run_...",
  "events": [
    {
      "run_id": "run_...",
      "step": 0,
      "event_type": "run_started",
      "timestamp": "...",
      "payload": {"task_id": "task_..."},
      "latency_ms": 0,
      "status": "ok",
      "event": "run_started",
      "created_at": "..."
    }
  ]
}
```

Standard `event_type` values:

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

Legacy top-level event fields are still preserved for existing consumers:

- `event`
- `created_at`
- event-specific payload fields such as `prompt_metadata`, `checkpoint_id`, `name`, `args`, `result`, and `trigger`

### 0.18 `GET /tools`

Response item shape:

```json
{
  "name": "read_file",
  "description": "Read a UTF-8 file by line range.",
  "schema": {"path": "str", "start": "int=1", "end": "int=200"},
  "risk_level": "safe",
  "requires_approval": false
}
```

`risk_level` values are `safe`, `medium`, and `high`.

Older statements that described Pure as not providing HTTP service are superseded by section 0.

### 0.19 `runtime_config` keys used by the service layer

The HTTP request schemas still accept `runtime_config` as a dictionary, but the current service implementation recognizes these keys:

- `approval_policy`: legacy CLI-compatible policy (`ask`, `auto`, `never`).
- `approval_mode`: gateway policy (`auto`, `readonly`, `manual`).
- `read_only`: boolean compatibility flag; treated like `approval_mode=readonly` for tool execution.
- `max_steps`: runtime tool-step limit.
- `max_new_tokens`: model output limit per step.
- `feature_flags`: runtime feature flags.
- `tool_repetition_guard`: optional object with `enabled`, `window`, and `mode` (`warn` or `block`).
- `mock_outputs`: deterministic `FakeModelClient` outputs for tests and dry-run-like execution.

`dry_run=true` always uses `FakeModelClient` and must still create run artifacts, trace events, checkpoint metadata, and simulated tool audit records.

## 1. CLI Contract：`pure`

入口：`pure` console script -> `pure.cli.cli:main`（也可 `python -m pure`）。

### 1.1 模式

- REPL（默认）：无 prompt 参数时进入交互循环（支持 `/help`、`/memory`、`/session`、`/reset`、`/exit`）。
- One-shot：提供 `prompt` 位置参数时，执行一次 `agent.ask()` 并退出。

### 1.2 参数（来自 `build_arg_parser()`）

- `prompt ...`：可选 one-shot prompt（nargs="*"，会 join 成一条字符串）
- `--cwd <path>`：workspace 目录（默认 `.`）
- `--provider {ollama,openai,anthropic,deepseek}`：模型后端（默认 `openai`）
- `--model <name>`：覆盖模型名（默认随 provider）
- `--host <url>`：Ollama host（默认 `http://127.0.0.1:11434`）
- `--base-url <url>`：openai/anthropic/deepseek 的 API base url（可覆盖 env）
- `--ollama-timeout <seconds>`：Ollama 超时（默认 300）
- `--openai-timeout <seconds>`：OpenAI-compatible 超时（默认 300；Anthropic/DeepSeek 也复用该值）
- `--resume <session_id|latest>`：加载历史 session
- `--dry-run`：使用 `FakeModelClient`（不调用真实 API，输出确定性）
- `--approval {ask,auto,never}`：risky tools 的审批策略（默认 `ask`）
- `--secret-env-name <NAME>`：追加更多需要脱敏的环境变量名（可多次指定）
- `--max-steps <int>`：最大 tool 步数（默认 6）
- `--max-new-tokens <int>`：每步模型最大输出 token（默认 512）
- `--temperature <float>`：温度（主要用于 Ollama；也会传给 HTTP clients）
- `--top-p <float>`：top_p（仅 Ollama client 当前使用）

## 2. Tool Contract：runtime tool registry

来源：`pure/tools/toolkit.py:BASE_TOOL_SPECS`（并在 `build_tool_registry()` 注册）。

### 2.1 工具列表与参数 schema

| tool | risky | schema（字段:类型=默认） | 描述 |
|---|---:|---|---|
| `list_files` | 否 | `path:str='.'` | 列出 workspace 文件 |
| `read_file` | 否 | `path:str`, `start:int=1`, `end:int=200` | 读取 UTF-8 文件指定行 |
| `search` | 否 | `pattern:str`, `path:str='.'` | 在 workspace 内搜索（优先 `rg`） |
| `run_shell` | 是 | `command:str`, `timeout:int=20` | 在 repo root 执行 shell 命令 |
| `write_file` | 是 | `path:str`, `content:str` | 写入文本文件 |
| `patch_file` | 是 | `path:str`, `old_text:str`, `new_text:str` | 精确替换一次文本块 |
| `delegate` | 否 | `task:str`, `max_steps:int=3` | 只读 child agent 调查（受 depth 限制） |

### 2.2 关键安全语义

- 所有 `path` 通过 `PureRuntime.path()` 解析与限制，禁止逃逸 workspace root。
- `patch_file` 强约束：`old_text` 必须“出现且仅出现一次”，否则拒绝。
- `run_shell` 会传入过滤后的环境变量（`shell_env_allowlist`），降低意外泄露风险。
- risky 工具执行前后会计算 workspace snapshot diff，并把 `affected_paths/diff_summary` 写入 trace。

### 2.3 Tool Gateway contract

All runtime tool calls pass through `ToolExecutionService.run_tool()`, which delegates to `ToolGateway.execute()`. The model-facing tool call syntax is unchanged.

`ToolSpec` fields:

```text
name
description
input_schema
risk_level
requires_approval
```

Risk levels:

```text
safe    read-only workspace inspection
medium  bounded delegated investigation
high    workspace write, patch, shell, or equivalent side effect
```

Approval modes:

```text
auto      execute after validation and legacy approval policy checks
readonly  reject write_file, patch_file, run_shell, and delete_file
manual    return waiting_approval for high-risk tools instead of executing them
```

Tool audit fields are written into `tool_executed` trace payloads and indexed into `tool_calls` where applicable:

- `args`
- `result` summary
- `latency_ms`
- `risk_level`
- `approval_decision`
- `tool_status`
- `tool_error_code`
- `security_event_type`
- `affected_paths`
- `workspace_changed`

## 3. Provider HTTP Contract（模型后端）

> 下述为当前实现的“客户端请求形状”，并不保证覆盖所有后端能力；仅描述代码真实使用到的字段/路径。

### 3.1 Ollama

客户端：`OllamaModelClient`（`pure/core/models.py`）

- URL：`{host}/api/generate`
- Method：POST
- Body（JSON，关键字段）：
  - `model`, `prompt`, `stream:false`, `options.num_predict`, `options.temperature`, `options.top_p`

### 3.2 OpenAI-compatible（Responses API 兼容）

客户端：`OpenAICompatibleModelClient`

- Base URL 规范化：若不以 `/v1` 结尾，会自动补 `/v1`
- Path：`/responses`
- Headers：`Authorization: Bearer <api_key>`（若提供）、`User-Agent: pure/0.1`
- Body（JSON，关键字段）：
  - `model`
  - `input`: `[{ role:"user", content:[{type:"input_text", text: prompt}]}]`
  - `max_output_tokens`
  - `stream:false`
  - 可选：`temperature`
  - 可选（仅 `supports_prompt_cache=True` 时）：`prompt_cache_key`、`prompt_cache_retention`

### 3.3 Anthropic-compatible

客户端：`AnthropicCompatibleModelClient`

- Base URL 规范化：若不以 `/v1` 结尾，会自动补 `/v1`
- Path：`/messages`
- Headers：`x-api-key: <api_key>`、`anthropic-version: 2023-06-01`
- Body（JSON，关键字段）：
  - `model`
  - `messages`: `[{ role:"user", content:[{type:"text", text: prompt}]}]`
  - `max_tokens`
  - `stream:false`
  - 可选：`temperature`

### 3.4 DeepSeek

CLI 选择 `--provider deepseek` 时，当前代码复用 `AnthropicCompatibleModelClient`，默认 base 为 `https://api.deepseek.com/anthropic`（并规范化到 `/v1`）。

## 4. 环境变量 Contract

### 4.1 `.env` 加载规则

- 从 `--cwd` 指定目录起向上查找 `.env`
- 以 UTF-8 读取，解析 `KEY=VALUE`（支持 `export KEY=VALUE`；会剥离同引号包裹）
- `load_project_env(..., override=True)`：默认会覆盖同名环境变量

### 4.2 Provider 相关变量（代码实际读取）

- OpenAI-compatible：
  - `PURE_OPENAI_API_BASE`（legacy：`OPENAI_API_BASE`）
  - `PURE_OPENAI_API_KEY`（legacy：`OPENAI_API_KEY`）
  - `PURE_OPENAI_MODEL`（legacy：`OPENAI_MODEL`）
- Anthropic-compatible：
  - `PURE_ANTHROPIC_API_BASE`（legacy：`ANTHROPIC_API_BASE`）
  - `PURE_ANTHROPIC_API_KEY`（legacy：`ANTHROPIC_API_KEY`；并会回退到 `PURE_RIGHT_CODES_API_KEY`、`RIGHT_CODES_API_KEY`、`PURE_OPENAI_API_KEY`、`OPENAI_API_KEY`）
  - `PURE_ANTHROPIC_MODEL`（legacy：`ANTHROPIC_MODEL`）
- DeepSeek：
  - `PURE_DEEPSEEK_API_BASE`（legacy：`DEEPSEEK_API_BASE`）
  - `PURE_DEEPSEEK_API_KEY`（legacy：`DEEPSEEK_API_KEY`）
  - `PURE_DEEPSEEK_MODEL`（legacy：`DEEPSEEK_MODEL`）

### 4.3 Secret 脱敏变量扩展

- `PURE_SECRET_ENV_NAMES`：逗号分隔，加入到 secret env 名单（legacy：`MINI_CODING_AGENT_SECRET_ENV_NAMES`）

### 4.4 Knowledge / Embedding

- `PURE_EMBEDDING_PROVIDER`：embedding provider 选择器；当前实现默认 `fake`（测试与 dry_run 不调用真实 embedding API）。

`.env.example` now uses the `PURE_*` variable names consumed by the code. Legacy provider variables such as `OPENAI_API_KEY` remain supported where the clients explicitly read them.
