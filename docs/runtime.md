# Runtime

Pure Runtime executes one task through the existing `PureRuntime.ask()` loop.

## Flow

```text
user task
  -> workspace snapshot
  -> knowledge retrieval
  -> prompt build
  -> model completion
  -> parse final/tool/retry
  -> Tool Repetition Guard check when needed
  -> ToolGateway execution when needed
  -> memory update
  -> checkpoint
  -> trace.jsonl + report.json
```

## Artifacts

Runtime artifacts are written below `.pure/`:

```text
.pure/sessions/<session_id>.json
.pure/runs/<run_id>/task_state.json
.pure/runs/<run_id>/trace.jsonl
.pure/runs/<run_id>/report.json
.pure/knowledge/index.json
.pure/evals/<eval_id>/report.json
```

The metadata database stores paths and summaries, not full trace/report payloads.

## Dry Run

Dry run uses `FakeModelClient` and never calls a real provider. It still creates normal runtime artifacts, database task/run metadata, checkpoints, trace events, and evaluator reports.

## Tool Repetition Guard

Tool Repetition Guard is a lightweight runtime execution policy that reduces short exploration loops where the model calls the same tool with the same arguments again and again. It is not a planner and does not guarantee that every loop is solved.

Pure tracks recent tool calls by tool name, normalized arguments, step, and timestamp. Normalized arguments use sorted object keys, preserve list order, normalize path-like fields such as `path`, `directory`, and `file` to workspace-relative slash paths, and ignore non-semantic fields such as `timeout`, `limit`, `display_limit`, and `max_lines`.

Default behavior is `warn`: repeated calls still execute, Pure writes `repeated_tool_call_detected`, and the tool observation includes a warning for the next model turn. `block` mode writes `tool_rejected_repeated_call` and returns `error: repeated tool call rejected` without invoking the tool handler. `block` is useful for stronger constraints, but it can reject a repeat that would have been useful after context changed.

```json
{
  "tool_repetition_guard": {
    "enabled": true,
    "window": 5,
    "mode": "warn"
  }
}
```

Run reports include `repeated_tool_call_count`.
