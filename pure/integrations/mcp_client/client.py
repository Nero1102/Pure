from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict, is_dataclass
from typing import Any

from .schemas import MCPClientError, MCPConfigError, MCPServerConfig, MCPToolDefinition


class MCPClient:
    """Small synchronous wrapper around an MCP client implementation."""

    def __init__(self):
        self.config: MCPServerConfig | None = None
        self._delegate: FakeMCPClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._session: Any = None

    def connect(self, server_config: dict[str, Any] | MCPServerConfig):
        self.config = MCPServerConfig.from_mapping(server_config)
        if self.config.transport == "fake":
            self._delegate = FakeMCPClient()
            self._delegate.connect(self.config)
            return self
        if self.config.transport != "stdio":
            raise MCPConfigError(f"unsupported MCP transport '{self.config.transport}'")
        if not self.config.command:
            raise MCPConfigError("stdio MCP server config requires command")
        self._connect_stdio_with_sdk(self.config)
        return self

    def list_tools(self):
        if self._delegate is not None:
            return self._delegate.list_tools()
        self._require_session()
        result = self._run(self._session.list_tools())
        raw_tools = getattr(result, "tools", result)
        return [MCPToolDefinition.from_raw(tool) for tool in list(raw_tools or [])]

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None):
        if self._delegate is not None:
            return self._delegate.call_tool(tool_name, arguments)
        self._require_session()
        result = self._run(self._session.call_tool(str(tool_name), arguments or {}))
        return _jsonable(result)

    def close(self):
        if self._delegate is not None:
            self._delegate.close()
            self._delegate = None
            return
        if self._exit_stack is not None and self._loop is not None and not self._loop.is_closed():
            self._run(self._exit_stack.aclose())
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._exit_stack = None
        self._loop = None
        self._session = None

    def _connect_stdio_with_sdk(self, config: MCPServerConfig):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except Exception as exc:
            raise MCPClientError(
                "MCP Python SDK is not installed or could not be imported; "
                "install the 'mcp' package or use transport='fake' in tests"
            ) from exc

        async def connect():
            self._exit_stack = AsyncExitStack()
            params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env=dict(config.env) or None,
            )
            read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(params))
            session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            return session

        self._loop = asyncio.new_event_loop()
        try:
            self._session = self._loop.run_until_complete(connect())
        except Exception:
            self.close()
            raise

    def _require_session(self):
        if self._session is None or self._loop is None:
            raise MCPClientError("MCP client is not connected")

    def _run(self, awaitable):
        if self._loop is None:
            raise MCPClientError("MCP client is not connected")
        return self._loop.run_until_complete(awaitable)


class FakeMCPClient:
    """Deterministic in-process MCP client used by tests and dry integrations."""

    def __init__(self):
        self.config: MCPServerConfig | None = None
        self.connected = False
        self._tools: list[MCPToolDefinition] = []
        self._results: dict[str, Any] = {}
        self._failures: dict[str, Any] = {}

    def connect(self, server_config: dict[str, Any] | MCPServerConfig):
        self.config = MCPServerConfig.from_mapping(server_config)
        raw_tools = self.config.raw.get("tools")
        if raw_tools:
            self._tools = [MCPToolDefinition.from_raw(tool) for tool in raw_tools]
        else:
            self._tools = list(_default_fake_tools())
        self._results = dict(
            self.config.raw.get("tool_results")
            or self.config.raw.get("results")
            or {}
        )
        self._failures = dict(self.config.raw.get("failures") or {})
        self.connected = True
        return self

    def list_tools(self):
        self._require_connected()
        return list(self._tools)

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None):
        self._require_connected()
        tool_name = str(tool_name)
        arguments = dict(arguments or {})
        known_tools = {tool.name for tool in self._tools}
        if tool_name not in known_tools:
            raise MCPClientError(f"unknown MCP tool '{tool_name}'")
        if tool_name in self._failures:
            raise MCPClientError(str(self._failures[tool_name]))
        if tool_name in self._results:
            result = self._results[tool_name]
            return result(arguments) if callable(result) else result
        if tool_name == "echo":
            return {
                "content": [{"type": "text", "text": str(arguments.get("message", ""))}],
                "structured_content": {"echo": arguments},
            }
        if tool_name == "get_build_status":
            return {
                "status": "passed",
                "branch": str(arguments.get("branch", "main") or "main"),
                "commit": str(arguments.get("commit", "local") or "local"),
            }
        return {"status": "ok", "tool": tool_name, "arguments": arguments}

    def close(self):
        self.connected = False

    def _require_connected(self):
        if not self.connected:
            raise MCPClientError("fake MCP client is not connected")


def _default_fake_tools():
    return (
        MCPToolDefinition(
            name="echo",
            description="Echo a message through the fake MCP server.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
        MCPToolDefinition(
            name="get_build_status",
            description="Return a deterministic fake build status.",
            input_schema={
                "type": "object",
                "properties": {
                    "branch": {"type": "string"},
                    "commit": {"type": "string"},
                },
            },
        ),
    )


def _jsonable(value: Any):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json", by_alias=True))
        except TypeError:
            return _jsonable(value.model_dump(by_alias=True))
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)
