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
from pure.services.tool_repetition_guard import ToolRepetitionGuard, WORKSPACE_MUTATING_TOOLS
from pure.services.trace_service import TraceService


BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_PATH = BENCHMARK_DIR / "cases.json"
DEFAULT_RESULTS_PATH = BENCHMARK_DIR / "results.json"
DEFAULT_JSONL_PATH = BENCHMARK_DIR / "per_case_results.jsonl"
DEFAULT_SUMMARY_PATH = BENCHMARK_DIR / "summary.md"
DEFAULT_WORKSPACE_ROOT = BENCHMARK_DIR / "workspaces"

GUARD_VARIANTS = [
    {"guard_mode": "off", "window": 5, "config": {"enabled": False, "window": 5, "mode": "warn"}},
    {"guard_mode": "warn", "window": 5, "config": {"enabled": True, "window": 5, "mode": "warn"}},
    {"guard_mode": "block", "window": 5, "config": {"enabled": True, "window": 5, "mode": "block"}},
]
REQUIRED_CASE_FIELDS = {
    "case_id",
    "description",
    "mock_outputs",
    "max_steps",
    "expected_repeated_count",
    "expected_blocked_count_in_block_mode",
    "expected_trace_events",
    "expected_final_keywords",
    "notes",
}


def load_cases(path: str | Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases", payload)
    if not isinstance(cases, list) or not cases:
        raise ValueError("tool loop cases must be a non-empty list")

    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each tool loop case must be an object")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(f"case {case.get('case_id', '<unknown>')} missing fields: {', '.join(missing)}")
        case_id = str(case["case_id"]).strip()
        if not case_id:
            raise ValueError("case_id must not be empty")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if not isinstance(case["mock_outputs"], list) or not case["mock_outputs"]:
            raise ValueError(f"case {case_id} mock_outputs must be a non-empty list")
        if int(case["max_steps"]) < 1:
            raise ValueError(f"case {case_id} max_steps must be positive")
        if int(case["expected_repeated_count"]) < 0:
            raise ValueError(f"case {case_id} expected_repeated_count must be non-negative")
        if int(case["expected_blocked_count_in_block_mode"]) < 0:
            raise ValueError(f"case {case_id} expected_blocked_count_in_block_mode must be non-negative")
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

    rows: list[dict[str, Any]] = []
    for case in cases:
        variant_rows = [
            run_case_variant(case, variant=variant, workspace_base=workspace_base)
            for variant in GUARD_VARIANTS
        ]
        _apply_baseline_rates(variant_rows)
        rows.extend(variant_rows)

    summary = summarize(rows)
    artifact = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "artifact_type": "tool-loop-repetition-guard-benchmark",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(Path(cases_path).resolve()),
        "model_provider": "FakeModelClient",
        "benchmark_target": "ToolRepetitionGuard",
        "summary": summary,
        "rows": rows,
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


def run_case_variant(case: dict[str, Any], *, variant: dict[str, Any], workspace_base: Path) -> dict[str, Any]:
    case_id = str(case["case_id"])
    guard_mode = str(variant["guard_mode"])
    root = workspace_base / f"{case_id}-{guard_mode}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    _write_workspace_fixture(root)

    agent = PureRuntime(
        model_client=FakeModelClient(list(case["mock_outputs"])),
        workspace=_build_isolated_workspace_context(root),
        session_store=SessionStore(root / ".pure" / "sessions"),
        run_store=RunStore(root / ".pure" / "runs"),
        approval_policy="auto",
        max_steps=int(case["max_steps"]),
        max_new_tokens=128,
        tool_repetition_guard=dict(variant["config"]),
        runtime_config={"benchmark": "tool_loop", "case_id": case_id, "guard_mode": guard_mode},
    )

    final_answer = ""
    runner_error = ""
    try:
        final_answer = agent.ask(f"Run tool loop benchmark case {case_id} in {guard_mode} mode.")
    except Exception as exc:  # benchmark rows preserve failures instead of hiding them
        runner_error = str(exc)

    events, report = _load_run_artifacts(agent, final_answer=final_answer, runner_error=runner_error)
    row = _case_result_row(
        case=case,
        variant=variant,
        workspace_root=root,
        agent=agent,
        events=events,
        report=report,
        runner_error=runner_error,
    )
    return row


def _write_workspace_fixture(root: Path) -> None:
    files = {
        "README.md": (
            "# Pure Tool Loop Fixture\n\n"
            "PureRuntime is mentioned here so repeated search cases have a deterministic match.\n"
        ),
        "notes.txt": "Notes for repeated read tests.\n",
        "pure/__init__.py": "",
        "pure/core/runtime.py": "class PureRuntime:\n    pass\n",
        "docs/guide.md": "Tool loop benchmark docs.\n",
    }
    for raw_path, content in files.items():
        path = root / raw_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


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


def _load_run_artifacts(agent: PureRuntime, *, final_answer: str, runner_error: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "status": "failed" if runner_error else "",
        "stop_reason": "runner_error" if runner_error else "",
        "final_answer": final_answer,
        "tool_steps": 0,
        "repeated_tool_call_count": 0,
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
    variant: dict[str, Any],
    workspace_root: Path,
    agent: PureRuntime,
    events: list[dict[str, Any]],
    report: dict[str, Any],
    runner_error: str,
) -> dict[str, Any]:
    guard_mode = str(variant["guard_mode"])
    window = int(variant["window"])
    requested_events = [event for event in events if event.get("event_type") == "tool_requested"]
    tool_events = [event for event in events if event.get("event_type") == "tool_executed"]
    repeated_detected_events = [event for event in events if event.get("event_type") == "repeated_tool_call_detected"]
    rejected_repeated_events = [event for event in events if event.get("event_type") == "tool_rejected_repeated_call"]
    blocked_repeated_call_count = len(rejected_repeated_events)
    executed_tool_call_count = sum(1 for event in tool_events if _tool_was_executed(event))
    repeated_tool_call_count, computed_normalized_samples = _executed_repeated_call_count(
        agent=agent,
        events=tool_events,
        window=window,
    )
    normalized_args_samples = _normalized_args_samples(repeated_detected_events + rejected_repeated_events)
    for sample in computed_normalized_samples:
        if sample not in normalized_args_samples:
            normalized_args_samples.append(sample)

    final_answer = str(report.get("final_answer", ""))
    expected_keywords = [str(item) for item in case.get("expected_final_keywords", []) or []]
    missing_keywords = [keyword for keyword in expected_keywords if keyword.lower() not in final_answer.lower()]
    expected_repeated_count = int(case["expected_repeated_count"])
    expected_blocked_count = int(case["expected_blocked_count_in_block_mode"]) if guard_mode == "block" else 0
    expected_executed_repeated_count = 0 if guard_mode == "block" else expected_repeated_count

    failure_reasons = []
    if runner_error:
        failure_reasons.append(f"runner error: {runner_error}")
    if missing_keywords:
        failure_reasons.append("missing final keywords: " + ", ".join(missing_keywords))
    if repeated_tool_call_count != expected_executed_repeated_count:
        failure_reasons.append(
            f"repeated_tool_call_count mismatch: expected {expected_executed_repeated_count}, got {repeated_tool_call_count}"
        )
    if blocked_repeated_call_count != expected_blocked_count:
        failure_reasons.append(
            f"blocked_repeated_call_count mismatch: expected {expected_blocked_count}, got {blocked_repeated_call_count}"
        )
    if guard_mode == "warn" and expected_repeated_count and len(repeated_detected_events) != expected_repeated_count:
        failure_reasons.append(
            f"warn event mismatch: expected {expected_repeated_count}, got {len(repeated_detected_events)}"
        )
    if guard_mode == "block" and expected_blocked_count and len(rejected_repeated_events) != expected_blocked_count:
        failure_reasons.append(
            f"block event mismatch: expected {expected_blocked_count}, got {len(rejected_repeated_events)}"
        )
    if guard_mode == "off" and (repeated_detected_events or rejected_repeated_events):
        failure_reasons.append("guard off emitted repetition trace events")
    if str(report.get("status", "")) != "completed":
        failure_reasons.append(f"final status was {report.get('status', '<empty>')}")

    return {
        "case_id": str(case["case_id"]),
        "guard_mode": guard_mode,
        "window": window,
        "total_tool_calls": len(requested_events),
        "executed_tool_call_count": executed_tool_call_count,
        "repeated_tool_call_count": repeated_tool_call_count,
        "blocked_repeated_call_count": blocked_repeated_call_count,
        "repeated_tool_call_detected_events": len(repeated_detected_events),
        "tool_rejected_repeated_call_events": len(rejected_repeated_events),
        "repeated_call_reduction_rate": None,
        "blocked_call_rate": _ratio(blocked_repeated_call_count, len(requested_events)),
        "step_saving_estimate": 0,
        "max_steps_used": int(report.get("tool_steps", len(requested_events)) or 0),
        "final_status": str(report.get("status", "")),
        "stop_reason": str(report.get("stop_reason", "")),
        "observation_contains_warning": _observation_contains_warning(tool_events),
        "normalized_args_samples": normalized_args_samples,
        "passed": not failure_reasons,
        "failure_reason": "; ".join(failure_reasons),
        "expected_repeated_count": expected_repeated_count,
        "expected_blocked_count_in_block_mode": int(case["expected_blocked_count_in_block_mode"]),
        "expected_trace_events": [str(item) for item in case.get("expected_trace_events", []) or []],
        "workspace_root": str(workspace_root),
        "notes": str(case["notes"]),
    }


def _tool_was_executed(event: dict[str, Any]) -> bool:
    payload = dict(event.get("payload", {}) or {})
    if payload.get("repeated_tool_call"):
        return False
    if str(payload.get("tool_error_code", "")) == "repeated_tool_call":
        return False
    return str(payload.get("tool_status", "")) not in {"rejected", "waiting_approval"}


def _executed_repeated_call_count(*, agent: PureRuntime, events: list[dict[str, Any]], window: int) -> tuple[int, list[str]]:
    guard = ToolRepetitionGuard(agent, {"enabled": True, "window": window, "mode": "warn"})
    recent: list[dict[str, Any]] = []
    repeated_count = 0
    samples: list[str] = []
    for event in events:
        payload = dict(event.get("payload", {}) or {})
        if not _tool_was_executed(event):
            continue
        tool_name = str(payload.get("tool_name") or payload.get("name", ""))
        args = dict(payload.get("tool_args") or payload.get("args") or {})
        normalized_args = guard.normalize_args(args)
        step = int(event.get("step", 0) or 0)
        matched = False
        for item in reversed(recent):
            if item["tool_name"] != tool_name:
                continue
            if item["normalized_args"] != normalized_args:
                continue
            if step - int(item["step"]) <= window:
                matched = True
                break
        if matched:
            repeated_count += 1
            if normalized_args not in samples:
                samples.append(normalized_args)
        if tool_name in WORKSPACE_MUTATING_TOOLS:
            recent = []
        recent.append({"tool_name": tool_name, "normalized_args": normalized_args, "step": step})
        recent = recent[-window:]
    return repeated_count, samples


def _normalized_args_samples(events: list[dict[str, Any]]) -> list[str]:
    samples: list[str] = []
    for event in events:
        payload = dict(event.get("payload", {}) or {})
        value = str(payload.get("normalized_args", event.get("normalized_args", "")) or "")
        if value and value not in samples:
            samples.append(value)
    return samples


def _observation_contains_warning(tool_events: list[dict[str, Any]]) -> bool:
    for event in tool_events:
        payload = dict(event.get("payload", {}) or {})
        if "warning: repeated tool call detected" in str(payload.get("result", "")):
            return True
    return False


def _apply_baseline_rates(rows: list[dict[str, Any]]) -> None:
    off = next((row for row in rows if row["guard_mode"] == "off"), None)
    if not off:
        return
    denominator = int(off["repeated_tool_call_count"])
    off_steps = int(off["max_steps_used"])
    for row in rows:
        if denominator == 0:
            row["repeated_call_reduction_rate"] = None
        else:
            row["repeated_call_reduction_rate"] = (
                denominator - int(row["repeated_tool_call_count"])
            ) / denominator
        row["step_saving_estimate"] = off_steps - int(row["max_steps_used"])


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_mode.setdefault(str(row["guard_mode"]), []).append(row)

    mode_summaries = {}
    for mode, mode_rows in sorted(by_mode.items()):
        total_tool_calls = sum(int(row["total_tool_calls"]) for row in mode_rows)
        blocked = sum(int(row["blocked_repeated_call_count"]) for row in mode_rows)
        reduction_values = [
            float(row["repeated_call_reduction_rate"])
            for row in mode_rows
            if row["repeated_call_reduction_rate"] is not None
        ]
        mode_summaries[mode] = {
            "case_count": len(mode_rows),
            "total_tool_calls": total_tool_calls,
            "executed_tool_call_count": sum(int(row["executed_tool_call_count"]) for row in mode_rows),
            "repeated_tool_call_count": sum(int(row["repeated_tool_call_count"]) for row in mode_rows),
            "blocked_repeated_call_count": blocked,
            "repeated_tool_call_detected_events": sum(int(row["repeated_tool_call_detected_events"]) for row in mode_rows),
            "tool_rejected_repeated_call_events": sum(int(row["tool_rejected_repeated_call_events"]) for row in mode_rows),
            "blocked_call_rate": _ratio(blocked, total_tool_calls),
            "avg_repeated_call_reduction_rate": _mean(reduction_values) if reduction_values else None,
            "total_step_saving_estimate": sum(int(row["step_saving_estimate"]) for row in mode_rows),
            "passed_rate": _ratio(sum(1 for row in mode_rows if row["passed"]), len(mode_rows)),
        }

    return {
        "case_count": len({row["case_id"] for row in rows}),
        "variant_count": len(rows),
        "guard_modes": mode_summaries,
        "trace_event_hit_rate": _ratio(
            sum(
                1
                for row in rows
                if (
                    row["guard_mode"] == "off"
                    or row["expected_repeated_count"] == 0
                    or row["repeated_tool_call_detected_events"]
                    or row["tool_rejected_repeated_call_events"]
                )
            ),
            len(rows),
        ),
        "failed_cases": [
            {
                "case_id": row["case_id"],
                "guard_mode": row["guard_mode"],
                "failure_reason": row["failure_reason"],
            }
            for row in rows
            if not row["passed"]
        ],
    }


def render_summary_markdown(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    rows = list(artifact["rows"])
    lines = [
        "# Tool Loop / Repetition Guard Benchmark",
        "",
        "## Goal",
        "",
        "ReAct-style agents can waste steps by repeatedly calling the same tool with the same arguments while exploring a workspace. This benchmark measures Pure's short-window Tool Repetition Guard on scripted offline tool loops.",
        "",
        "## Method",
        "",
        "Each case runs the same FakeModelClient mock_outputs under three guard configurations: off, warn, and block. Guard off records how many duplicate tool executions actually happened. Warn emits an advisory trace event and still executes the tool. Block emits a rejection trace event and returns a repeated-call error without executing the duplicate tool handler.",
        "",
        "## Normalized Args",
        "",
        "The guard sorts dict keys, normalizes path-like arguments relative to the workspace, collapses slash and Windows-style path variants, and ignores non-semantic keys such as timeout, limit, display_limit, and max_lines.",
        "",
        "## Results",
        "",
        "| Guard Mode | Cases | Tool Calls | Executed Calls | Repeated Executed Calls | Blocked Repeats | Warn Events | Block Events | Blocked Call Rate | Avg Reduction | Step Saving Estimate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in ("off", "warn", "block"):
        mode_summary = summary["guard_modes"].get(mode, {})
        reduction = mode_summary.get("avg_repeated_call_reduction_rate")
        reduction_text = "-" if reduction is None else _pct(float(reduction))
        lines.append(
            f"| {mode} | {mode_summary.get('case_count', 0)} | "
            f"{mode_summary.get('total_tool_calls', 0)} | "
            f"{mode_summary.get('executed_tool_call_count', 0)} | "
            f"{mode_summary.get('repeated_tool_call_count', 0)} | "
            f"{mode_summary.get('blocked_repeated_call_count', 0)} | "
            f"{mode_summary.get('repeated_tool_call_detected_events', 0)} | "
            f"{mode_summary.get('tool_rejected_repeated_call_events', 0)} | "
            f"{_pct(float(mode_summary.get('blocked_call_rate', 0.0)))} | "
            f"{reduction_text} | "
            f"{mode_summary.get('total_step_saving_estimate', 0)} |"
        )

    lines.extend(
        [
            "",
            "## Trace Events",
            "",
            "- repeated_tool_call_detected is emitted in warn mode when a matching recent tool call is found.",
            "- tool_rejected_repeated_call is emitted in block mode before returning a repeated-call error observation.",
            "",
            "## Case Analysis",
            "",
            "| Case | Off Repeats | Warn Events | Blocked Repeats | Block Reduction | Normalized Args Samples |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for case_id in (
        "repeated_list_files_same_path",
        "repeated_list_files_windows_path",
        "ignored_non_semantic_args",
        "window_expiry_allows_repeat",
        "mixed_tool_sequence",
    ):
        case_rows = {row["guard_mode"]: row for row in rows if row["case_id"] == case_id}
        if not case_rows:
            continue
        block_rate = case_rows.get("block", {}).get("repeated_call_reduction_rate")
        block_text = "-" if block_rate is None else _pct(float(block_rate))
        samples = ", ".join(case_rows.get("warn", {}).get("normalized_args_samples", [])) or "-"
        lines.append(
            f"| {case_id} | "
            f"{case_rows.get('off', {}).get('repeated_tool_call_count', 0)} | "
            f"{case_rows.get('warn', {}).get('repeated_tool_call_detected_events', 0)} | "
            f"{case_rows.get('block', {}).get('blocked_repeated_call_count', 0)} | "
            f"{block_text} | {samples} |"
        )

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This only addresses short-window repeated tool calls.",
            "- It cannot replace a planner.",
            "- It cannot solve loops where parameters differ but are semantically similar.",
            "- It does not represent real model overall success rate.",
            "",
            "## Resume Bullet Candidate",
            "",
            "- Built a reproducible offline Tool Repetition Guard benchmark for Pure that compares off/warn/block behavior, normalized-argument detection, trace events, blocked duplicate executions, and step usage from FakeModelClient runs.",
            "",
            "## Reproduction Command",
            "",
            "```bash",
            "python benchmarks/pure/tool_loop/run_tool_loop_benchmark.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _mean(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _pct(value: float) -> str:
    return f"{float(value) * 100:.2f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pure's offline Tool Loop / Repetition Guard benchmark.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to tool loop cases JSON.")
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
