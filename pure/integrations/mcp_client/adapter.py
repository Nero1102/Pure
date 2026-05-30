from __future__ import annotations

import json
import time
from functools import partial
from typing import Any, Callable

from pure.tools.registry import RISK_HIGH, ToolSpec

from .schemas import (
    MCPClientError,
    MCPServerConfig,
    MCPToolDefinition,
    requires_approval_for_risk,
)


class MCPToolExecutionError(RuntimeError):
    """Raised when an external MCP tool call fails."""


class MCPToolAdapter:
    """Map external MCP tools into Pure runtime tool definitions."""

    def __init__(
        self,
        server_config: MCPServerConfig,
        client,
        trace_callback: Callable[[str, dict[str, Any]], Any] | None = None,
    ):
        self.server_config = server_config
        self.client = client
        self.trace_callback = trace_callback

    def to_tool_spec(self, mcp_tool: MCPToolDefinition):
        risk_level = self.server_config.risk_level_for(mcp_tool.name)
        return ToolSpec(
            name=self.pure_tool_name(mcp_tool.name),
            description=mcp_tool.description,
            input_schema=dict(mcp_tool.input_schema),
            risk_level=risk_level,
            requires_approval=requires_approval_for_risk(risk_level),
        )

    def to_runtime_tool(self, mcp_tool: MCPToolDefinition):
        spec = self.to_tool_spec(mcp_tool)
        return {
            "schema": dict(spec.input_schema),
            "description": spec.description,
            "risky": spec.risk_level == RISK_HIGH,
            "risk_level": spec.risk_level,
            "requires_approval": spec.requires_approval,
            "mcp_server_name": self.server_config.name,
            "mcp_tool_name": mcp_tool.name,
            "run": partial(self.run_tool, mcp_tool.name, spec.name),
        }

    def pure_tool_name(self, mcp_tool_name: str):
        return f"mcp.{self.server_config.name}.{mcp_tool_name}"

    def run_tool(self, mcp_tool_name: str, pure_tool_name: str, arguments: dict[str, Any] | None = None):
        started = time.perf_counter()
        arguments = dict(arguments or {})
        try:
            result = self.client.call_tool(mcp_tool_name, arguments)
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._emit_trace(
                "mcp_tool_called",
                {
                    "server_name": self.server_config.name,
                    "tool_name": pure_tool_name,
                    "mcp_tool_name": mcp_tool_name,
                    "latency_ms": latency_ms,
                    "status": "ok",
                },
            )
            return format_mcp_result(result)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._emit_trace(
                "mcp_tool_failed",
                {
                    "server_name": self.server_config.name,
                    "tool_name": pure_tool_name,
                    "mcp_tool_name": mcp_tool_name,
                    "latency_ms": latency_ms,
                    "status": "failed",
                    "error": str(exc),
                },
            )
            if isinstance(exc, MCPClientError):
                raise MCPToolExecutionError(
                    f"MCP tool '{mcp_tool_name}' on server '{self.server_config.name}' failed: {exc}"
                ) from exc
            raise

    def _emit_trace(self, event_type: str, payload: dict[str, Any]):
        if self.trace_callback is not None:
            self.trace_callback(event_type, payload)


def format_mcp_result(result: Any):
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        texts = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    texts.append(item["text"])
        structured = result.get("structured_content", result.get("structuredContent"))
        if texts and not structured:
            return "\n".join(texts)
        if texts and structured:
            return "\n".join(texts) + "\n" + _json_text({"structured_content": structured})
        return _json_text(result)
    return _json_text(result)


def _json_text(value: Any):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    except TypeError:
        return str(value)
