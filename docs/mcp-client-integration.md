# MCP Client Integration

Pure 的 MCP 集成是 MCP Client 集成，不是 MCP Server 实现。

## MCP Client 和 MCP Server 的区别

MCP Server 暴露外部系统能力，例如 GitHub、CI/CD、文档库、Kubernetes 或监控平台的 tools。MCP Client 连接这些 server，发现 tools，并发起 tool 调用。

Pure 在这里扮演 MCP Client：它连接外部 MCP Server，把 server 暴露的 tools 转换成 Pure tools。Pure 不把 `PureRuntime` 改造成 MCP Server，也不要求内部工具全部改写为 MCP。

## Pure 为什么作为 MCP Client

Pure 已经有稳定的 runtime、工具治理和审计链路。作为 MCP Client 可以把外部系统接入现有工具调用链，而不是绕开治理：

```text
PureRuntime
  -> ToolExecutionService
  -> ToolGateway
  -> MCPToolAdapter
  -> External MCP Server
```

这样 MCP tools 可以复用 Pure 的 approval policy、risk metadata、trace、ToolCall 审计和 run artifact。

## ToolGateway 和 MCP 的关系

MCP tools 会先注册为 Pure tools，然后由 `ToolGateway` 执行。模型或 runtime 不会直接调用 MCP Server。

默认 MCP tool 风险等级是 `medium`，不是 `safe`。可以在 server 配置上用 `risk_level` 设置默认风险等级，也可以用 `tool_risk_levels` 覆盖单个 tool。`high` 风险的 MCP tool 在 `approval_mode=manual` 下会返回 `waiting_approval`，不会直接调用外部 MCP Server。

## 注册外部 MCP Tools

运行时配置示例：

```json
{
  "mcp": {
    "enabled": true,
    "servers": [
      {
        "name": "demo",
        "transport": "stdio",
        "command": "python",
        "args": ["examples/mcp_servers/demo_server.py"]
      }
    ]
  }
}
```

每个 MCP tool 映射为 Pure tool：

```text
name: mcp.<server_name>.<tool_name>
description: MCP tool description
input_schema: MCP tool input schema
risk_level: medium by default, configurable
requires_approval: true when risk_level is high
```

例如 demo server 的 `echo` 会注册为 `mcp.demo.echo`。

## Trace 事件

MCP Client Adapter 会写入以下 trace event：

- `mcp_server_connected`
- `mcp_tools_registered`
- `mcp_tool_called`
- `mcp_tool_failed`

事件 payload 包含 `server_name`、`tool_name`、`mcp_tool_name`、`latency_ms` 和 `status`。MCP 调用随后仍会产生普通 `tool_executed` 事件，因此会进入现有 ToolCall 审计索引。

## 当前限制

- 当前实现提供官方 MCP Python SDK 的 stdio wrapper，同时保留 fake client 用于稳定测试。
- 测试不依赖真实外部 MCP Server，也不调用真实模型 API。
- 运行时只作为 MCP Client，不暴露 MCP Server 能力。
- MCP tool 参数目前交给 MCP Server 做深度校验，Pure 只要求 arguments 是 JSON object。
- HTTP/SSE 等更多 transport 可以在 adapter 层扩展。

## 未来扩展方向

后续可以接入 GitHub、CI/CD、Docs、Kubernetes 和 Observability MCP Server。接入原则保持不变：外部 tools 注册为 Pure tools，并且必须经过 `ToolGateway` 的权限、trace 和审计链路。
