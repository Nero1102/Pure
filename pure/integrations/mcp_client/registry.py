from __future__ import annotations

import time
from typing import Any, Callable

from .adapter import MCPToolAdapter
from .client import MCPClient
from .schemas import MCPConfigError, MCPServerConfig, MCPToolDefinition


class MCPRegistrationError(RuntimeError):
    """Raised when external MCP tools cannot be registered as Pure tools."""


class MCPClientRegistry:
    """Own connected MCP clients and register their tools on a Pure runtime."""

    def __init__(self, client_factory: Callable[..., Any] | None = None):
        self.client_factory = client_factory
        self.clients: dict[str, Any] = {}
        self.adapters: dict[str, MCPToolAdapter] = {}

    def register_tools(self, agent, mcp_config: dict[str, Any] | None):
        config = dict(mcp_config or {})
        if not bool(config.get("enabled", False)):
            return []
        raw_servers = config.get("servers") or []
        if not isinstance(raw_servers, list):
            raise MCPConfigError("mcp.servers must be a list")

        trace_events: list[tuple[str, dict[str, Any]]] = []
        for raw_server in raw_servers:
            server_config = MCPServerConfig.from_mapping(raw_server)
            client = self._new_client(server_config)

            connected_started = time.perf_counter()
            client.connect(server_config)
            trace_events.append(
                (
                    "mcp_server_connected",
                    {
                        "server_name": server_config.name,
                        "transport": server_config.transport,
                        "latency_ms": int((time.perf_counter() - connected_started) * 1000),
                        "status": "ok",
                    },
                )
            )

            adapter = MCPToolAdapter(
                server_config,
                client,
                trace_callback=self._trace_callback(agent),
            )
            list_started = time.perf_counter()
            mcp_tools = [MCPToolDefinition.from_raw(tool) for tool in client.list_tools()]
            registered = []
            for mcp_tool in mcp_tools:
                pure_name = adapter.pure_tool_name(mcp_tool.name)
                if pure_name in agent.tools:
                    raise MCPRegistrationError(f"Pure tool '{pure_name}' is already registered")
                agent.tools[pure_name] = adapter.to_runtime_tool(mcp_tool)
                registered.append(pure_name)
            trace_events.append(
                (
                    "mcp_tools_registered",
                    {
                        "server_name": server_config.name,
                        "tool_count": len(registered),
                        "tools": registered,
                        "latency_ms": int((time.perf_counter() - list_started) * 1000),
                        "status": "ok",
                    },
                )
            )
            self.clients[server_config.name] = client
            self.adapters[server_config.name] = adapter
        return trace_events

    def close(self):
        for client in self.clients.values():
            close = getattr(client, "close", None)
            if callable(close):
                close()
        self.clients.clear()
        self.adapters.clear()

    def _new_client(self, server_config: MCPServerConfig):
        if self.client_factory is None:
            return MCPClient()
        try:
            return self.client_factory(server_config)
        except TypeError:
            return self.client_factory()

    @staticmethod
    def _trace_callback(agent):
        def emit(event_type: str, payload: dict[str, Any]):
            task_state = getattr(agent, "current_task_state", None)
            if task_state is not None:
                return agent.emit_trace(task_state, event_type, payload)
            return None

        return emit
