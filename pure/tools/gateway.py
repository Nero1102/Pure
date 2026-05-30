import re
import time
from dataclasses import dataclass
from typing import Any

from ..core.workspace import clip
from .policies import ToolPolicyError, normalize_approval_mode, validate_tool_policy
from .registry import runtime_tool_specs


@dataclass
class ToolGatewayResult:
    result: str
    metadata: dict[str, Any]


class ToolGateway:
    def __init__(self, agent):
        self.agent = agent

    def execute(self, name: str, args: dict[str, Any] | None):
        started = time.perf_counter()
        args = args or {}
        specs = runtime_tool_specs(self.agent.tools)
        tool = self.agent.tools.get(name)
        spec = specs.get(name)
        if tool is None or spec is None:
            metadata = self._metadata(
                name=name,
                args=args,
                started=started,
                tool_status="rejected",
                tool_error_code="unknown_tool",
                risk_level="high",
                approval_decision="denied_unknown_tool",
            )
            return ToolGatewayResult(f"error: unknown tool '{name}'", metadata)

        approval_mode = normalize_approval_mode(
            getattr(self.agent, "approval_mode", None),
            approval_policy=getattr(self.agent, "approval_policy", "auto"),
            read_only=bool(getattr(self.agent, "read_only", False)),
        )

        try:
            self.agent.validate_tool(name, args)
            approval_decision = validate_tool_policy(approval_mode, name, spec.risk_level, args, self.agent.root)
        except ToolPolicyError as exc:
            metadata = self._metadata(
                name=name,
                args=args,
                started=started,
                tool_status="waiting_approval" if exc.code == "waiting_approval" else "rejected",
                tool_error_code=exc.code,
                security_event_type=exc.security_event_type,
                risk_level=spec.risk_level,
                approval_decision="denied_readonly" if exc.code == "readonly_block" else "denied",
            )
            return ToolGatewayResult(f"error: {exc}", metadata)
        except Exception as exc:
            example = self.agent.tool_example(name)
            message = f"error: invalid arguments for {name}: {exc}"
            if example:
                message += f"\nexample: {example}"
            security_event_type = "path_escape" if "path escapes workspace" in str(exc) else ""
            metadata = self._metadata(
                name=name,
                args=args,
                started=started,
                tool_status="rejected",
                tool_error_code="invalid_arguments",
                security_event_type=security_event_type,
                risk_level=spec.risk_level,
                approval_decision="not_requested",
            )
            return ToolGatewayResult(message, metadata)

        if approval_decision == "waiting_approval":
            metadata = self._metadata(
                name=name,
                args=args,
                started=started,
                tool_status="waiting_approval",
                tool_error_code="approval_required",
                risk_level=spec.risk_level,
                approval_decision="waiting_approval",
            )
            return ToolGatewayResult(f"waiting_approval: approval required for {name}", metadata)

        if tool["risky"] and approval_mode != "manual" and not self.agent.approve(name, args):
            metadata = self._metadata(
                name=name,
                args=args,
                started=started,
                tool_status="rejected",
                tool_error_code="approval_denied",
                security_event_type="read_only_block" if self.agent.read_only else "approval_denied",
                risk_level=spec.risk_level,
                approval_decision="denied",
            )
            return ToolGatewayResult(f"error: approval denied for {name}", metadata)

        before_snapshot = self.agent.capture_workspace_snapshot() if tool["risky"] else {}
        after_snapshot = before_snapshot
        try:
            result = clip(tool["run"](args))
            after_snapshot = self.agent.capture_workspace_snapshot() if tool["risky"] else before_snapshot
            affected_paths, diff_summary = self.agent.diff_workspace_snapshots(before_snapshot, after_snapshot)
            workspace_changed = bool(affected_paths)
            tool_status = "ok"
            tool_error_code = ""
            if name == "run_shell":
                match = re.search(r"exit_code:\s*(-?\d+)", result)
                exit_code = int(match.group(1)) if match else 0
                if exit_code != 0 and workspace_changed:
                    tool_status = "partial_success"
                    tool_error_code = "tool_partial_success"
                elif exit_code != 0:
                    tool_status = "error"
                    tool_error_code = "tool_failed"
            self.agent.memory_service.update_after_tool(name, args, result)
            metadata = self._metadata(
                name=name,
                args=args,
                started=started,
                tool_status=tool_status,
                tool_error_code=tool_error_code,
                risk_level=spec.risk_level,
                approval_decision=approval_decision,
                affected_paths=affected_paths,
                workspace_changed=workspace_changed,
                workspace_fingerprint=self.agent.workspace.fingerprint(),
                diff_summary=diff_summary,
            )
            self.agent.record_process_note_for_tool(name, metadata)
            return ToolGatewayResult(result, metadata)
        except Exception as exc:
            after_snapshot = self.agent.capture_workspace_snapshot() if tool["risky"] else before_snapshot
            affected_paths, diff_summary = self.agent.diff_workspace_snapshots(before_snapshot, after_snapshot)
            workspace_changed = bool(affected_paths)
            security_event_type = "path_escape" if "path escapes workspace" in str(exc) else ""
            metadata = self._metadata(
                name=name,
                args=args,
                started=started,
                tool_status="partial_success" if workspace_changed else "error",
                tool_error_code="tool_partial_success" if workspace_changed else "tool_failed",
                security_event_type=security_event_type,
                risk_level=spec.risk_level,
                approval_decision=approval_decision,
                affected_paths=affected_paths,
                workspace_changed=workspace_changed,
                workspace_fingerprint=self.agent.workspace.fingerprint(),
                diff_summary=diff_summary,
            )
            self.agent.record_process_note_for_tool(name, metadata)
            return ToolGatewayResult(f"error: tool {name} failed: {exc}", metadata)

    def _metadata(
        self,
        *,
        name: str,
        args: dict[str, Any],
        started: float,
        tool_status: str,
        tool_error_code: str,
        risk_level: str,
        approval_decision: str,
        security_event_type: str = "",
        affected_paths: list[str] | None = None,
        workspace_changed: bool = False,
        workspace_fingerprint: str = "",
        diff_summary: list[str] | None = None,
    ):
        return {
            "tool_name": name,
            "tool_args": args,
            "tool_status": tool_status,
            "tool_error_code": tool_error_code,
            "security_event_type": security_event_type,
            "risk_level": risk_level,
            "requires_approval": risk_level == "high",
            "approval_decision": approval_decision,
            "approval_mode": normalize_approval_mode(
                getattr(self.agent, "approval_mode", None),
                approval_policy=getattr(self.agent, "approval_policy", "auto"),
                read_only=bool(getattr(self.agent, "read_only", False)),
            ),
            "read_only": risk_level == "safe",
            "affected_paths": affected_paths or [],
            "workspace_changed": bool(workspace_changed),
            "workspace_fingerprint": workspace_fingerprint,
            "diff_summary": diff_summary or [],
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
