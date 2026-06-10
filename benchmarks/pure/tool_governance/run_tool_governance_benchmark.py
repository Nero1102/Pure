from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pure.core.models import FakeModelClient
from pure.core.run_store import RunStore
from pure.core.runtime import PureRuntime
from pure.core.session_store import SessionStore
from pure.core.workspace import WorkspaceContext
from pure.services.trace_service import TraceService


BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_PATH = BENCHMARK_DIR / "cases.json"
DEFAULT_RESULTS_PATH = BENCHMARK_DIR / "results.json"
DEFAULT_JSONL_PATH = BENCHMARK_DIR / "per_case_results.jsonl"
DEFAULT_SUMMARY_PATH = BENCHMARK_DIR / "summary.md"
DEFAULT_WORKSPACE_ROOT = BENCHMARK_DIR / "workspaces"

REQUIRED_CASE_FIELDS = {
    "case_id",
    "tool_name",
    "arguments",
    "approval_mode",
    "expected_allowed",
    "expected_error_code",
    "expected_security_event",
    "expected_workspace_changed",
    "expected_affected_paths",
    "notes",
}
AUDIT_FIELDS = {
    "risk_level",
    "approval_mode",
    "approval_decision",
    "tool_status",
    "tool_error_code",
    "security_event_type",
    "workspace_changed",
    "affected_paths",
}
EXECUTED_STATUSES = {"ok", "partial_success"}


def load_cases(path: str | Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases", payload)
    if not isinstance(cases, list) or not cases:
        raise ValueError("tool governance cases must be a non-empty list")

    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each tool governance case must be an object")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(f"case {case.get('case_id', '<unknown>')} missing fields: {', '.join(missing)}")
        case_id = str(case["case_id"]).strip()
        if not case_id:
            raise ValueError("case_id must not be empty")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if not isinstance(case["arguments"], dict):
            raise ValueError(f"case {case_id} arguments must be an object")
        if str(case["approval_mode"]) not in {"auto", "readonly", "manual"}:
            raise ValueError(f"case {case_id} approval_mode must be auto, readonly, or manual")
        if not isinstance(case["expected_allowed"], bool):
            raise ValueError(f"case {case_id} expected_allowed must be a bool")
        if not isinstance(case["expected_workspace_changed"], bool):
            raise ValueError(f"case {case_id} expected_workspace_changed must be a bool")
        if not isinstance(case["expected_affected_paths"], list):
            raise ValueError(f"case {case_id} expected_affected_paths must be a list")
    return cases


def run_benchmark(
    *,
    cases_path: str | Path = DEFAULT_CASES_PATH,
    results_path: str | Path = DEFAULT_RESULTS_PATH,
    per_case_path: str | Path = DEFAULT_JSONL_PATH,
    summary_path: str | Path = DEFAULT_SUMMARY_PATH,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    workspace_base = Path(workspace_root).resolve() if workspace_root else DEFAULT_WORKSPACE_ROOT.resolve()
    workspace_base.mkdir(parents=True, exist_ok=True)

    rows = [run_case(case, workspace_base=workspace_base) for case in cases]
    summary = summarize(rows)
    artifact = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "artifact_type": "tool-governance-benchmark",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(Path(cases_path).resolve()),
        "model_provider": "FakeModelClient",
        "benchmark_target": "ToolGateway",
        "summary": summary,
        "cases": rows,
    }

    results_path = Path(results_path)
    per_case_path = Path(per_case_path)
    summary_path = Path(summary_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    per_case_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    per_case_path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary_path.write_text(render_summary_markdown(artifact), encoding="utf-8")
    return artifact


def run_case(case: dict[str, Any], *, workspace_base: Path) -> dict[str, Any]:
    case_id = str(case["case_id"])
    root = workspace_base / f"{case_id}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    outside_absolute_path = _write_workspace_fixture(root, case, workspace_base=workspace_base)
    arguments = _replace_placeholders(dict(case["arguments"]), outside_absolute_path=outside_absolute_path)

    workspace = _build_isolated_workspace_context(root)
    model_client = FakeModelClient(
        [
            _tool_call_output(str(case["tool_name"]), arguments),
            f"<final>Tool governance benchmark case {case_id} completed.</final>",
        ]
    )
    agent = PureRuntime(
        model_client=model_client,
        workspace=workspace,
        session_store=SessionStore(root / ".pure" / "sessions"),
        run_store=RunStore(root / ".pure" / "runs"),
        approval_policy="auto",
        approval_mode=str(case["approval_mode"]),
        max_steps=2,
        max_new_tokens=128,
        runtime_config={"benchmark": "tool_governance", "case_id": case_id},
    )

    final_answer = ""
    runner_error = ""
    try:
        final_answer = agent.ask(f"Run tool governance benchmark case {case_id}.")
    except Exception as exc:  # benchmark rows keep failures visible instead of hiding them
        runner_error = str(exc)

    events, report = _load_run_artifacts(agent, final_answer=final_answer, runner_error=runner_error)
    tool_event = next(
        (event for event in reversed(events) if event.get("event_type") == "tool_executed"),
        None,
    )
    payload = dict((tool_event or {}).get("payload", {}) or {})
    return _case_result_row(
        case=case,
        arguments=arguments,
        workspace_root=root,
        payload=payload,
        tool_event=tool_event,
        report=report,
        runner_error=runner_error,
    )


def _build_isolated_workspace_context(root: Path) -> WorkspaceContext:
    previous_ceiling = os.environ.get("GIT_CEILING_DIRECTORIES")
    os.environ["GIT_CEILING_DIRECTORIES"] = str(REPO_ROOT)
    try:
        return WorkspaceContext.build(root, repo_root_override=root)
    finally:
        if previous_ceiling is None:
            os.environ.pop("GIT_CEILING_DIRECTORIES", None)
        else:
            os.environ["GIT_CEILING_DIRECTORIES"] = previous_ceiling


def _write_workspace_fixture(root: Path, case: dict[str, Any], *, workspace_base: Path) -> Path:
    del case
    root.mkdir(parents=True, exist_ok=True)
    workspace_files = {
        "README.md": (
            "# Pure Tool Governance Fixture\n\n"
            "This fixture mentions PureRuntime so safe_search_allowed has a deterministic match.\n"
        ),
        "tmp.txt": "before\n",
        "patch_target.txt": "before\n",
        "notes.txt": "unchanged fixture file\n",
    }
    for raw_path, content in workspace_files.items():
        path = root / raw_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    outside_dir = workspace_base / "_outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_path = outside_dir / f"outside-{uuid.uuid4().hex[:8]}.txt"
    outside_path.write_text("outside fixture secret\n", encoding="utf-8")
    return outside_path.resolve()


def _replace_placeholders(value: Any, *, outside_absolute_path: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _replace_placeholders(item, outside_absolute_path=outside_absolute_path) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(item, outside_absolute_path=outside_absolute_path) for item in value]
    if value == "{outside_absolute_path}":
        return str(outside_absolute_path)
    return value


def _tool_call_output(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = {"name": tool_name, "args": arguments}
    return "<tool>" + json.dumps(payload, ensure_ascii=True, sort_keys=True) + "</tool>"


def _load_run_artifacts(agent: PureRuntime, *, final_answer: str, runner_error: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "status": "failed" if runner_error else "",
        "stop_reason": "runner_error" if runner_error else "",
        "final_answer": final_answer,
    }
    task_state = getattr(agent, "current_task_state", None)
    if task_state is None:
        return events, report
    trace_path = agent.run_store.trace_path(task_state)
    if trace_path.exists():
        events = TraceService.load_events(trace_path)
    report_path = agent.run_store.report_path(task_state)
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    return events, report


def _case_result_row(
    *,
    case: dict[str, Any],
    arguments: dict[str, Any],
    workspace_root: Path,
    payload: dict[str, Any],
    tool_event: dict[str, Any] | None,
    report: dict[str, Any],
    runner_error: str,
) -> dict[str, Any]:
    actual_allowed = str(payload.get("tool_status", "")) in EXECUTED_STATUSES
    expected_allowed = bool(case["expected_allowed"])
    expected_error_code = str(case["expected_error_code"])
    expected_security_event = str(case["expected_security_event"])
    expected_affected_paths = [str(path) for path in case["expected_affected_paths"]]
    actual_affected_paths = [str(path) for path in payload.get("affected_paths", []) or []]
    workspace_changed = bool(payload.get("workspace_changed", False))
    tool_error_code = str(payload.get("tool_error_code", ""))
    security_event_type = str(payload.get("security_event_type", ""))

    failure_reasons = []
    if tool_event is None:
        failure_reasons.append("missing tool_executed trace event")
    if runner_error:
        failure_reasons.append(f"runner error: {runner_error}")
    if actual_allowed != expected_allowed:
        failure_reasons.append(f"allowed mismatch: expected {expected_allowed}, got {actual_allowed}")
    if tool_error_code != expected_error_code:
        failure_reasons.append(f"error_code mismatch: expected {expected_error_code or '<empty>'}, got {tool_error_code or '<empty>'}")
    if security_event_type != expected_security_event:
        failure_reasons.append(
            f"security_event mismatch: expected {expected_security_event or '<empty>'}, got {security_event_type or '<empty>'}"
        )
    if workspace_changed != bool(case["expected_workspace_changed"]):
        failure_reasons.append(
            f"workspace_changed mismatch: expected {case['expected_workspace_changed']}, got {workspace_changed}"
        )
    if sorted(actual_affected_paths) != sorted(expected_affected_paths):
        failure_reasons.append(
            f"affected_paths mismatch: expected {expected_affected_paths}, got {actual_affected_paths}"
        )

    audit_fields_present = all(key in payload for key in AUDIT_FIELDS)
    if not audit_fields_present:
        missing = sorted(key for key in AUDIT_FIELDS if key not in payload)
        failure_reasons.append("missing audit fields: " + ", ".join(missing))

    row = {
        "case_id": str(case["case_id"]),
        "category": str(case.get("category", "")),
        "tool_name": str(case["tool_name"]),
        "arguments": arguments,
        "risk_level": str(payload.get("risk_level", "")),
        "approval_mode": str(payload.get("approval_mode", case["approval_mode"])),
        "expected_allowed": expected_allowed,
        "actual_allowed": actual_allowed,
        "approval_decision": str(payload.get("approval_decision", "")),
        "args_valid": tool_error_code not in {"invalid_arguments", "unknown_tool"},
        "path_within_workspace": security_event_type != "path_escape",
        "security_event_type": security_event_type,
        "tool_status": str(payload.get("tool_status", "")),
        "tool_error_code": tool_error_code,
        "workspace_changed": workspace_changed,
        "affected_paths": actual_affected_paths,
        "audit_fields_present": audit_fields_present,
        "trace_event_present": tool_event is not None,
        "passed": not failure_reasons,
        "failure_reason": "; ".join(failure_reasons),
        "expected_error_code": expected_error_code,
        "expected_security_event": expected_security_event,
        "expected_workspace_changed": bool(case["expected_workspace_changed"]),
        "expected_affected_paths": expected_affected_paths,
        "workspace_root": str(workspace_root),
        "report_status": str(report.get("status", "")),
        "report_stop_reason": str(report.get("stop_reason", "")),
        "notes": str(case["notes"]),
    }
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(rows)
    unsafe_cases = [row for row in rows if not row["expected_allowed"]]
    safe_cases = [row for row in rows if row["category"] == "safe_tools"]
    workspace_escape_cases = [row for row in rows if row["category"] == "workspace_escape"]
    readonly_cases = [row for row in rows if row["expected_error_code"] == "readonly_block"]
    invalid_arg_cases = [
        row for row in rows
        if row["expected_error_code"] in {"invalid_arguments", "unknown_tool"}
    ]
    audit_diff_cases = [row for row in rows if row["category"] == "audit_diff"]

    false_allows = [row["case_id"] for row in rows if not row["expected_allowed"] and row["actual_allowed"]]
    false_rejects = [row["case_id"] for row in rows if row["expected_allowed"] and not row["actual_allowed"]]
    return {
        "case_count": case_count,
        "policy_pass_rate": _ratio(sum(1 for row in rows if row["passed"]), case_count),
        "unsafe_rejection_rate": _ratio(sum(1 for row in unsafe_cases if not row["actual_allowed"]), len(unsafe_cases)),
        "safe_allow_rate": _ratio(sum(1 for row in safe_cases if row["actual_allowed"]), len(safe_cases)),
        "workspace_escape_block_rate": _ratio(
            sum(
                1
                for row in workspace_escape_cases
                if not row["actual_allowed"] and row["security_event_type"] == "path_escape"
            ),
            len(workspace_escape_cases),
        ),
        "readonly_block_rate": _ratio(
            sum(
                1
                for row in readonly_cases
                if not row["actual_allowed"] and row["tool_error_code"] == "readonly_block"
            ),
            len(readonly_cases),
        ),
        "invalid_args_rejection_rate": _ratio(
            sum(
                1
                for row in invalid_arg_cases
                if not row["actual_allowed"] and row["tool_error_code"] in {"invalid_arguments", "unknown_tool"}
            ),
            len(invalid_arg_cases),
        ),
        "risky_tool_audit_coverage": _ratio(
            sum(1 for row in audit_diff_cases if _audit_diff_expectation_passed(row)),
            len(audit_diff_cases),
        ),
        "trace_audit_coverage": _ratio(
            sum(1 for row in rows if row["trace_event_present"] and row["audit_fields_present"]),
            case_count,
        ),
        "false_allow_count": len(false_allows),
        "false_reject_count": len(false_rejects),
        "false_allows": false_allows,
        "false_rejects": false_rejects,
        "failed_cases": [
            {"case_id": row["case_id"], "failure_reason": row["failure_reason"]}
            for row in rows
            if not row["passed"]
        ],
    }


def _audit_diff_expectation_passed(row: dict[str, Any]) -> bool:
    return (
        row["trace_event_present"]
        and row["audit_fields_present"]
        and row["workspace_changed"] == row["expected_workspace_changed"]
        and sorted(row["affected_paths"]) == sorted(row["expected_affected_paths"])
    )


def render_summary_markdown(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    rows = list(artifact["cases"])
    lines = [
        "# Tool Governance Benchmark",
        "",
        "## Goal",
        "",
        "ToolGateway is the boundary between model intent and the local workspace. This benchmark measures whether the current Pure code records and enforces basic tool governance decisions before a model-directed action reaches files or shell execution.",
        "",
        "## Case Categories",
        "",
        "- safe tools: readonly read_file, list_files, and search calls that should be allowed.",
        "- risky tools: write_file, patch_file, and run_shell calls that require policy and audit handling.",
        "- approval policy: readonly mode blocks write, patch, and shell execution.",
        "- workspace escape: parent traversal and absolute paths outside the fixture workspace are rejected.",
        "- invalid args: unknown tools, missing required arguments, and wrong argument types are rejected.",
        "- audit diff: allowed risky tools report workspace_changed and affected_paths from actual fixture diffs.",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| case_count | {summary['case_count']} |",
        f"| policy_pass_rate | {_pct(summary['policy_pass_rate'])} |",
        f"| unsafe_rejection_rate | {_pct(summary['unsafe_rejection_rate'])} |",
        f"| safe_allow_rate | {_pct(summary['safe_allow_rate'])} |",
        f"| workspace_escape_block_rate | {_pct(summary['workspace_escape_block_rate'])} |",
        f"| readonly_block_rate | {_pct(summary['readonly_block_rate'])} |",
        f"| invalid_args_rejection_rate | {_pct(summary['invalid_args_rejection_rate'])} |",
        f"| risky_tool_audit_coverage | {_pct(summary['risky_tool_audit_coverage'])} |",
        f"| trace_audit_coverage | {_pct(summary['trace_audit_coverage'])} |",
        f"| false_allow_count | {summary['false_allow_count']} |",
        f"| false_reject_count | {summary['false_reject_count']} |",
        "",
        "## Security Events",
        "",
        "| Error Code | Security Event | Count |",
        "| --- | --- | ---: |",
    ]
    security_events = _security_event_counts(rows)
    if security_events:
        for (error_code, security_event), count in sorted(security_events.items()):
            lines.append(f"| {error_code or '-'} | {security_event or '-'} | {count} |")
    else:
        lines.append("| - | - | 0 |")

    lines.extend(
        [
            "",
            "## Risky Tool Audit",
            "",
            "workspace_changed is derived from before/after snapshots of the fixture workspace. affected_paths lists changed relative paths inside that fixture only.",
            "",
            "| Case | Tool | workspace_changed | affected_paths |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for row in rows:
        if row["category"] != "audit_diff":
            continue
        affected = ", ".join(row["affected_paths"]) or "-"
        lines.append(f"| {row['case_id']} | {row['tool_name']} | {str(row['workspace_changed']).lower()} | {affected} |")

    lines.extend(["", "## False Allow / False Reject", ""])
    if summary["false_allows"] or summary["false_rejects"]:
        lines.extend(["| Type | Cases |", "| --- | --- |"])
        lines.append(f"| false allow | {', '.join(summary['false_allows']) or '-'} |")
        lines.append(f"| false reject | {', '.join(summary['false_rejects']) or '-'} |")
    else:
        lines.append("No false allow or false reject cases were recorded in this run.")

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This is not a production-grade sandbox.",
            "- It cannot defend against every command risk.",
            "- It only measures local workspace boundary checks and policy governance in offline fixture cases.",
            "- It does not represent an enterprise security system.",
            "",
            "## Resume Bullet Candidate",
            "",
            "- Built a reproducible offline ToolGateway governance benchmark for Pure that measures readonly policy rejection, workspace escape blocking, invalid-argument rejection, and risky-tool audit coverage from FakeModelClient runtime traces.",
            "",
            "## Reproduction Command",
            "",
            "```bash",
            "python benchmarks/pure/tool_governance/run_tool_governance_benchmark.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _security_event_counts(rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        error_code = str(row.get("tool_error_code", ""))
        security_event = str(row.get("security_event_type", ""))
        if not error_code and not security_event:
            continue
        key = (error_code, security_event)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _pct(value: float) -> str:
    return f"{float(value) * 100:.2f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pure's offline Tool Governance benchmark.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to tool governance cases JSON.")
    parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH), help="Path to write results.json.")
    parser.add_argument("--per-case", default=str(DEFAULT_JSONL_PATH), help="Path to write per_case_results.jsonl.")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY_PATH), help="Path to write summary.md.")
    parser.add_argument("--workspace-root", default=None, help="Optional directory for generated fixture workspaces.")
    args = parser.parse_args(argv)
    artifact = run_benchmark(
        cases_path=args.cases,
        results_path=args.results,
        per_case_path=args.per_case,
        summary_path=args.summary,
        workspace_root=args.workspace_root,
    )
    print(json.dumps(artifact["summary"], indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
