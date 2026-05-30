from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pure.tools.registry import RISK_HIGH, RISK_LEVELS, RISK_MEDIUM


class MCPConfigError(ValueError):
    """Raised when MCP client configuration cannot be used safely."""


class MCPClientError(RuntimeError):
    """Raised when an MCP client operation fails."""


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    risk_level: str = RISK_MEDIUM
    tool_risk_levels: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | "MCPServerConfig"):
        if isinstance(value, MCPServerConfig):
            return value
        if not isinstance(value, dict):
            raise MCPConfigError("MCP server config must be a mapping")

        name = str(value.get("name", "")).strip()
        if not name:
            raise MCPConfigError("MCP server config requires a non-empty name")
        if "." in name:
            raise MCPConfigError("MCP server name must not contain '.'")

        transport = str(value.get("transport", "stdio") or "stdio").strip().lower()
        command = str(value.get("command", "") or "")
        args = [str(item) for item in (value.get("args") or [])]
        env = {str(key): str(item) for key, item in dict(value.get("env") or {}).items()}
        risk_level = normalize_risk_level(value.get("risk_level"), default=RISK_MEDIUM)
        raw_tool_risks = value.get("tool_risk_levels", value.get("risk_levels", {})) or {}
        if not isinstance(raw_tool_risks, dict):
            raise MCPConfigError("MCP tool_risk_levels must be a mapping")
        tool_risk_levels = {
            str(tool_name): normalize_risk_level(tool_risk, default=risk_level)
            for tool_name, tool_risk in raw_tool_risks.items()
        }

        return cls(
            name=name,
            transport=transport,
            command=command,
            args=args,
            env=env,
            risk_level=risk_level,
            tool_risk_levels=tool_risk_levels,
            raw=dict(value),
        )

    def risk_level_for(self, tool_name: str):
        return self.tool_risk_levels.get(str(tool_name), self.risk_level)


@dataclass(frozen=True)
class MCPToolDefinition:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, value: Any):
        data = _object_to_mapping(value)
        name = str(data.get("name", "")).strip()
        if not name:
            raise MCPClientError("MCP tool is missing a name")
        schema = (
            data.get("input_schema")
            or data.get("inputSchema")
            or data.get("schema")
            or {}
        )
        if not isinstance(schema, dict):
            schema = {}
        return cls(
            name=name,
            description=str(data.get("description", "") or ""),
            input_schema=dict(schema),
        )


def normalize_risk_level(value: Any, *, default: str = RISK_MEDIUM):
    risk_level = str(value or default).strip().lower()
    if risk_level not in RISK_LEVELS:
        accepted = ", ".join(sorted(RISK_LEVELS))
        raise MCPConfigError(f"unsupported MCP risk_level '{value}', expected one of: {accepted}")
    return risk_level


def requires_approval_for_risk(risk_level: str):
    return risk_level == RISK_HIGH


def _object_to_mapping(value: Any):
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(by_alias=True)
        if isinstance(dumped, dict):
            return dumped
    return {
        "name": getattr(value, "name", ""),
        "description": getattr(value, "description", ""),
        "input_schema": getattr(value, "input_schema", getattr(value, "inputSchema", {})),
    }
