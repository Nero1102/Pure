from dataclasses import dataclass
from typing import Any

from . import toolkit


RISK_SAFE = "safe"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_LEVELS = {RISK_SAFE, RISK_MEDIUM, RISK_HIGH}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: str
    requires_approval: bool


def risk_level_for_tool(name: str, raw_spec: dict[str, Any]):
    configured = str(raw_spec.get("risk_level", "") or "").strip().lower()
    if configured:
        if configured not in RISK_LEVELS:
            raise ValueError(f"unsupported risk_level for tool {name}: {configured}")
        return configured
    if name == "delegate":
        return RISK_MEDIUM
    return RISK_HIGH if raw_spec.get("risky") else RISK_SAFE


def tool_spec_from_raw(name: str, raw_spec: dict[str, Any]):
    risk_level = risk_level_for_tool(name, raw_spec)
    return ToolSpec(
        name=name,
        description=str(raw_spec.get("description", "")),
        input_schema=dict(raw_spec.get("schema", {})),
        risk_level=risk_level,
        requires_approval=bool(raw_spec.get("requires_approval", risk_level == RISK_HIGH)),
    )


def base_tool_specs():
    raw_specs = {**toolkit.BASE_TOOL_SPECS, "delegate": toolkit.DELEGATE_TOOL_SPEC}
    return {name: tool_spec_from_raw(name, raw_spec) for name, raw_spec in raw_specs.items()}


def runtime_tool_specs(tools: dict[str, dict[str, Any]]):
    return {name: tool_spec_from_raw(name, raw_spec) for name, raw_spec in tools.items()}
