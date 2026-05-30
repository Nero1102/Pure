import time

from ..tools.gateway import ToolGateway
from ..tools.registry import runtime_tool_specs
from .tool_repetition_guard import ToolRepetitionGuard, WORKSPACE_MUTATING_TOOLS


class ToolExecutionService:
    def __init__(self, agent):
        self.agent = agent
        self.gateway = ToolGateway(agent)
        self.repetition_guard = ToolRepetitionGuard(
            agent,
            getattr(agent, "tool_repetition_guard_config", None),
        )

    def run_tool(self, name, args):
        args = args or {}
        match = self.repetition_guard.check(name, args)
        if match is not None:
            self.agent.repeated_tool_call_count += 1
            if match.mode == "block":
                self._emit_repetition_trace("tool_rejected_repeated_call", match)
                outcome = self._blocked_repeated_call(name, args, match)
                self.agent._last_tool_result_metadata = outcome.metadata
                return outcome.result
            self._emit_repetition_trace("repeated_tool_call_detected", match)

        outcome = self.gateway.execute(name, args)
        if match is not None and match.mode == "warn":
            outcome.result = match.warning + "\n" + outcome.result
        self.agent._last_tool_result_metadata = outcome.metadata
        if self._should_record_call(name, outcome.metadata):
            if name in WORKSPACE_MUTATING_TOOLS:
                self.repetition_guard.clear()
            self.repetition_guard.record(name, args)
        return outcome.result

    def _blocked_repeated_call(self, name, args, match):
        from ..tools.gateway import ToolGatewayResult

        spec = runtime_tool_specs(self.agent.tools).get(name)
        risk_level = spec.risk_level if spec is not None else "high"
        metadata = self.gateway._metadata(
            name=name,
            args=args,
            started=time.perf_counter(),
            tool_status="rejected",
            tool_error_code="repeated_tool_call",
            risk_level=risk_level,
            approval_decision="not_requested",
        )
        metadata.update(
            {
                "repeated_tool_call": True,
                "normalized_args": match.normalized_args,
                "previous_step": match.previous_step,
                "current_step": match.current_step,
            }
        )
        return ToolGatewayResult(
            "error: repeated tool call rejected\n" + match.warning,
            metadata,
        )

    def _emit_repetition_trace(self, event_type, match):
        task_state = getattr(self.agent, "current_task_state", None)
        if task_state is None:
            return None
        return self.agent.emit_trace(task_state, event_type, match.trace_payload())

    @staticmethod
    def _should_record_call(name, metadata):
        del name
        status = str((metadata or {}).get("tool_status", "")).strip()
        return status not in {"rejected", "waiting_approval"}
