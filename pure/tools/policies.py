import os
import shlex
from pathlib import Path
from typing import Any


APPROVAL_AUTO = "auto"
APPROVAL_READONLY = "readonly"
APPROVAL_MANUAL = "manual"
APPROVAL_MODES = {APPROVAL_AUTO, APPROVAL_READONLY, APPROVAL_MANUAL}

READONLY_BLOCKED_TOOLS = {"write_file", "patch_file", "run_shell", "delete_file"}


class ToolPolicyError(ValueError):
    def __init__(self, message: str, code: str, security_event_type: str = ""):
        super().__init__(message)
        self.code = code
        self.security_event_type = security_event_type


def normalize_approval_mode(mode: str | None, *, approval_policy: str = "auto", read_only: bool = False):
    raw = str(mode or "").strip().lower()
    if raw in APPROVAL_MODES:
        return raw
    if read_only:
        return APPROVAL_READONLY
    # Historical CLI policy names remain supported. "ask" keeps the old
    # interactive path, so only explicit "manual" returns waiting_approval.
    if approval_policy == "auto":
        return APPROVAL_AUTO
    return APPROVAL_AUTO


def approval_decision_for(mode: str, tool_name: str, risk_level: str):
    if mode == APPROVAL_READONLY and tool_name in READONLY_BLOCKED_TOOLS:
        return "denied_readonly"
    if mode == APPROVAL_MANUAL and risk_level == "high":
        return "waiting_approval"
    return "approved"


def check_readonly_policy(mode: str, tool_name: str):
    if mode == APPROVAL_READONLY and tool_name in READONLY_BLOCKED_TOOLS:
        raise ToolPolicyError(
            f"readonly mode blocks tool '{tool_name}'",
            code="readonly_block",
            security_event_type="read_only_block",
        )


def check_shell_workspace_policy(command: str, workspace_root: Path):
    root = workspace_root.resolve()
    try:
        # Use non-POSIX splitting on every platform so Windows-style paths such
        # as ..\outside.txt keep their backslash instead of being treated as an
        # escape sequence on Linux/macOS CI.
        tokens = shlex.split(command, posix=False)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens):
        cleaned = token.strip().strip("\"'")
        if not cleaned:
            continue
        if _looks_like_parent_escape(cleaned):
            raise ToolPolicyError(
                "shell command references a path that escapes workspace",
                code="path_escape",
                security_event_type="path_escape",
            )
        if _looks_like_absolute_path(cleaned):
            if index == 0:
                continue
            candidate = Path(cleaned)
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            try:
                escapes = os.path.commonpath([str(root), str(resolved)]) != str(root)
            except ValueError:
                escapes = True
            if escapes:
                raise ToolPolicyError(
                    "shell command references a path that escapes workspace",
                    code="path_escape",
                    security_event_type="path_escape",
                )


def _looks_like_parent_escape(value: str):
    normalized = value.replace("\\", "/")
    return normalized == ".." or normalized.startswith("../") or "/../" in normalized or normalized.endswith("/..")


def _looks_like_absolute_path(value: str):
    if value.startswith(("/", "\\")):
        return True
    return len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")


def validate_tool_policy(mode: str, tool_name: str, risk_level: str, args: dict[str, Any], workspace_root: Path):
    check_readonly_policy(mode, tool_name)
    if tool_name == "run_shell":
        check_shell_workspace_policy(str((args or {}).get("command", "")), workspace_root)
    decision = approval_decision_for(mode, tool_name, risk_level)
    if decision == "waiting_approval":
        return decision
    return "approved"
