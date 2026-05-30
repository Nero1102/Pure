# Tool Gateway

ToolGateway is the policy and audit boundary for tool execution.

## Tool Metadata

Each tool has:

- `name`
- `description`
- `input_schema`
- `risk_level`: `safe`, `medium`, or `high`
- `requires_approval`

## Approval Modes

- `auto`: validate and execute allowed tools.
- `readonly`: reject write/shell style tools.
- `manual`: return `waiting_approval` for high-risk tools.

## Runtime Flow

```text
PureRuntime.ask()
  -> ToolExecutionService.run_tool()
  -> ToolGateway.execute()
  -> policy validation
  -> toolkit runner
  -> trace + DB summary indexing
```

The model-facing tool protocol is unchanged.
