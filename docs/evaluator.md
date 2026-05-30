# Evaluator

The Evaluator runs a set of cases through the normal Runtime path and writes a report under `.pure/evals/<eval_id>/report.json`.

## Case File

Default file:

```text
eval_cases.json
```

Schema:

```json
{
  "cases": [
    {
      "id": "dry_run_runtime_smoke",
      "task": "Inspect the repository without calling a real model.",
      "expected_tools": [],
      "forbidden_tools": ["run_shell"],
      "success_keywords": ["Dry run"],
      "max_steps": 2,
      "expected_trace_events": [
        "run_started",
        "knowledge_retrieved",
        "checkpoint_created",
        "run_completed"
      ]
    }
  ]
}
```

## Metrics

The report includes:

- `task_success`
- `case_pass_rate`
- `expected_tool_hit_rate`
- `forbidden_tool_count`
- `expected_trace_event_hit_rate`
- `trace_event_success`
- `average_steps`
- `average_latency`
- `checkpoint_created`
- `knowledge_retrieved`
- `tool_rejection_count`
- `security_event_count`

Each row includes `case_passed` and `failure_reasons` so a failed case explains which keyword, tool, step budget, or trace expectation was missed.

## API

Run an evaluation:

```bash
curl -X POST http://localhost:8000/eval/run \
  -H "Content-Type: application/json" \
  -d '{"project_path":".","cases_path":"eval_cases.json","dry_run":true}'
```

Read a report:

```bash
curl http://localhost:8000/eval/eval_id_here/report
```

`dry_run=true` is the default and never calls a real model.
