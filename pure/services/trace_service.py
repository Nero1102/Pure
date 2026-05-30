import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STANDARD_EVENT_TYPES = {
    "run_started",
    "context_built",
    "model_called",
    "tool_requested",
    "tool_validated",
    "tool_executed",
    "repeated_tool_call_detected",
    "tool_rejected_repeated_call",
    "memory_updated",
    "checkpoint_created",
    "knowledge_retrieved",
    "mcp_server_connected",
    "mcp_tools_registered",
    "mcp_tool_called",
    "mcp_tool_failed",
    "run_completed",
    "run_failed",
    "run_cancelled",
}

EVENT_ALIASES = {
    "prompt_built": "context_built",
    "model_requested": "model_called",
    "model_parsed": "model_called",
    "run_finished": "run_completed",
    "runtime_identity_mismatch": "knowledge_retrieved",
}


class TraceService:
    """Validate and format run trace events without owning runtime control flow."""

    def __init__(self, run_store=None):
        self.run_store = run_store

    def append(self, task_state, event_type: str, payload: dict[str, Any] | None = None):
        if self.run_store is None:
            raise ValueError("TraceService.append requires a run_store")
        event = self.format_event(task_state, event_type, payload)
        self.run_store.append_trace(task_state, event)
        return event

    def format_event(self, task_state, event_type: str, payload: dict[str, Any] | None = None):
        payload = dict(payload or {})
        canonical = self.canonical_event_type(event_type, payload)
        timestamp = self._timestamp(payload)
        latency_ms = int(payload.get("latency_ms", payload.get("duration_ms", 0)) or 0)
        status = str(payload.get("status", self._status_for_event(canonical, task_state)) or "")
        step = int(getattr(task_state, "tool_steps", 0) or 0)
        event = dict(payload)
        event.update({
            "run_id": str(getattr(task_state, "run_id", "")),
            "step": step,
            "event_type": canonical,
            "timestamp": timestamp,
            "payload": payload,
            "latency_ms": latency_ms,
            "status": status,
            # Legacy compatibility for existing artifact consumers.
            "event": event_type,
            "created_at": timestamp,
        })
        self.validate_event(event)
        return event

    @staticmethod
    def canonical_event_type(event_type: str, payload: dict[str, Any] | None = None):
        payload = payload or {}
        if event_type == "run_finished" and payload.get("status") not in {"completed", "stopped"}:
            return "run_failed"
        return EVENT_ALIASES.get(event_type, event_type)

    @staticmethod
    def validate_event(event: dict[str, Any]):
        missing = [
            key
            for key in ("run_id", "step", "event_type", "timestamp", "payload", "latency_ms", "status")
            if key not in event
        ]
        if missing:
            raise ValueError(f"trace event missing required fields: {', '.join(missing)}")
        if event["event_type"] not in STANDARD_EVENT_TYPES:
            raise ValueError(f"unsupported trace event_type: {event['event_type']}")
        if not isinstance(event["payload"], dict):
            raise ValueError("trace event payload must be a dict")
        return event

    @classmethod
    def load_events(cls, path: Path):
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            events.append(cls.normalize_legacy_event(raw))
        return events

    @classmethod
    def normalize_legacy_event(cls, raw: dict[str, Any]):
        if "event_type" in raw:
            cls.validate_event(raw)
            return raw
        event_type = str(raw.get("event", ""))
        payload = {key: value for key, value in raw.items() if key not in {"event", "created_at"}}
        canonical = cls.canonical_event_type(event_type, payload)
        event = {
            "run_id": str(raw.get("run_id", "")),
            "step": int(raw.get("tool_steps", raw.get("step", 0)) or 0),
            "event_type": canonical,
            "timestamp": str(raw.get("created_at") or cls._utc_now()),
            "payload": payload,
            "latency_ms": int(raw.get("duration_ms", raw.get("latency_ms", 0)) or 0),
            "status": str(raw.get("status", "ok") or "ok"),
            "event": event_type,
            "created_at": str(raw.get("created_at") or cls._utc_now()),
        }
        cls.validate_event(event)
        return event

    @staticmethod
    def _timestamp(payload: dict[str, Any]):
        return str(payload.get("timestamp", payload.get("created_at", "")) or TraceService._utc_now())

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _status_for_event(event_type: str, task_state):
        if event_type == "run_failed":
            return "failed"
        if event_type == "run_cancelled":
            return "cancelled"
        if event_type == "run_completed":
            return str(getattr(task_state, "status", "completed") or "completed")
        return "ok"
