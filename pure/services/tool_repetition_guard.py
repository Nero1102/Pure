from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TOOL_REPETITION_GUARD = {
    "enabled": True,
    "window": 5,
    "mode": "warn",
}

NON_SEMANTIC_ARG_KEYS = {"timeout", "limit", "display_limit", "max_lines"}
PATH_ARG_KEYS = {"path", "directory", "dir", "file", "filepath", "file_path"}
WORKSPACE_MUTATING_TOOLS = {"write_file", "patch_file", "run_shell"}
VALID_MODES = {"warn", "block"}


@dataclass(frozen=True)
class ToolRepetitionGuardConfig:
    enabled: bool = True
    window: int = 5
    mode: str = "warn"
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, value: Any):
        warnings: list[str] = []
        if value is None:
            data: dict[str, Any] = {}
        elif isinstance(value, dict):
            data = dict(value)
        else:
            warnings.append("tool_repetition_guard must be an object; using defaults")
            data = {}

        enabled = data.get("enabled", DEFAULT_TOOL_REPETITION_GUARD["enabled"])
        if not isinstance(enabled, bool):
            warnings.append("tool_repetition_guard.enabled must be a boolean; using true")
            enabled = True

        try:
            window = int(data.get("window", DEFAULT_TOOL_REPETITION_GUARD["window"]))
        except (TypeError, ValueError):
            warnings.append("tool_repetition_guard.window must be a positive integer; using 5")
            window = 5
        if window < 1:
            warnings.append("tool_repetition_guard.window must be a positive integer; using 5")
            window = 5

        mode = str(data.get("mode", DEFAULT_TOOL_REPETITION_GUARD["mode"]) or "warn").strip().lower()
        if mode not in VALID_MODES:
            warnings.append(f"tool_repetition_guard.mode must be warn or block; got {mode!r}; using warn")
            mode = "warn"

        return cls(enabled=enabled, window=window, mode=mode, warnings=tuple(warnings))

    def to_dict(self):
        return {
            "enabled": self.enabled,
            "window": self.window,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class RecentToolCall:
    tool_name: str
    normalized_args: str
    step: int
    timestamp: str
    workspace_fingerprint: str = ""

    def to_dict(self):
        return {
            "tool_name": self.tool_name,
            "normalized_args": self.normalized_args,
            "step": self.step,
            "timestamp": self.timestamp,
            "workspace_fingerprint": self.workspace_fingerprint,
        }


@dataclass(frozen=True)
class RepeatedToolCallMatch:
    tool_name: str
    normalized_args: str
    previous_step: int
    current_step: int
    previous_timestamp: str
    window: int
    mode: str

    @property
    def warning(self):
        return (
            "warning: repeated tool call detected. "
            f"{self.tool_name} with the same args was already executed at step {self.previous_step}. "
            "Avoid repeating the same exploration unless the workspace changed."
        )

    def trace_payload(self):
        return {
            "tool_name": self.tool_name,
            "normalized_args": self.normalized_args,
            "previous_step": self.previous_step,
            "current_step": self.current_step,
            "previous_timestamp": self.previous_timestamp,
            "window": self.window,
            "mode": self.mode,
        }


class ToolRepetitionGuard:
    def __init__(self, agent, config: ToolRepetitionGuardConfig | dict[str, Any] | None = None):
        self.agent = agent
        self.config = (
            config
            if isinstance(config, ToolRepetitionGuardConfig)
            else ToolRepetitionGuardConfig.from_mapping(config)
        )

    def current_step(self):
        task_state = getattr(self.agent, "current_task_state", None)
        if task_state is not None:
            return int(getattr(task_state, "tool_steps", 0) or 0)
        return len(getattr(self.agent, "recent_tool_calls", []) or []) + 1

    def check(self, tool_name: str, args: dict[str, Any] | None):
        if not self.config.enabled:
            return None

        normalized_args = self.normalize_args(args or {})
        current_step = self.current_step()
        current_fingerprint = self.workspace_fingerprint()

        for item in reversed(getattr(self.agent, "recent_tool_calls", []) or []):
            if item.get("tool_name") != tool_name:
                continue
            if item.get("normalized_args") != normalized_args:
                continue
            previous_fingerprint = str(item.get("workspace_fingerprint", ""))
            if previous_fingerprint and current_fingerprint and previous_fingerprint != current_fingerprint:
                continue
            previous_step = int(item.get("step", 0) or 0)
            if current_step - previous_step <= self.config.window:
                return RepeatedToolCallMatch(
                    tool_name=tool_name,
                    normalized_args=normalized_args,
                    previous_step=previous_step,
                    current_step=current_step,
                    previous_timestamp=str(item.get("timestamp", "")),
                    window=self.config.window,
                    mode=self.config.mode,
                )
        return None

    def record(self, tool_name: str, args: dict[str, Any] | None):
        if not self.config.enabled:
            return
        calls = list(getattr(self.agent, "recent_tool_calls", []) or [])
        calls.append(
            RecentToolCall(
                tool_name=str(tool_name),
                normalized_args=self.normalize_args(args or {}),
                step=self.current_step(),
                timestamp=_utc_now(),
                workspace_fingerprint=self.workspace_fingerprint(),
            ).to_dict()
        )
        self.agent.recent_tool_calls = calls[-self.config.window :]

    def clear(self):
        self.agent.recent_tool_calls = []

    def normalize_args(self, args: dict[str, Any] | None):
        normalized = self._normalize_value(args or {}, key="")
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _normalize_value(self, value: Any, key: str):
        if isinstance(value, dict):
            return {
                str(item_key): self._normalize_value(item_value, str(item_key))
                for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
                if str(item_key).lower() not in NON_SEMANTIC_ARG_KEYS
            }
        if isinstance(value, list):
            return [self._normalize_value(item, key) for item in value]
        if isinstance(value, tuple):
            return [self._normalize_value(item, key) for item in value]
        if isinstance(value, str) and self._is_path_key(key):
            return self._normalize_path(value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _normalize_path(self, value: str):
        raw = str(value).strip()
        if not raw:
            return raw
        path_text = raw.replace("\\", "/")
        try:
            path = Path(path_text)
            candidate = path if path.is_absolute() else Path(self.agent.root) / path
            resolved = candidate.resolve()
            root = Path(self.agent.root).resolve()
            if os.path.commonpath([str(root), str(resolved)]) == str(root):
                relative = resolved.relative_to(root).as_posix()
                return relative or "."
        except Exception:
            pass
        return _collapse_path_text(path_text)

    @staticmethod
    def _is_path_key(key: str):
        lowered = str(key).lower()
        return (
            lowered in PATH_ARG_KEYS
            or lowered.endswith("_path")
            or lowered.endswith("_dir")
            or lowered.endswith("_file")
        )

    def workspace_fingerprint(self):
        return str(
            getattr(
                getattr(self.agent, "prefix_state", None),
                "workspace_fingerprint",
                "",
            )
            or getattr(self.agent.workspace, "fingerprint", lambda: "")()
        )


def _collapse_path_text(path_text: str):
    parts: list[str] = []
    for part in str(path_text).replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    collapsed = "/".join(parts) or "."
    if collapsed.startswith("./"):
        return collapsed[2:] or "."
    return collapsed


def _utc_now():
    return datetime.now(timezone.utc).isoformat()
