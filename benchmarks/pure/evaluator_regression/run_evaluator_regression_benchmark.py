import argparse
import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
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
from pure.evaluator.cases import EvalCase
from pure.evaluator.metrics import calculate_case_metrics, infer_latency_ms
from pure.services.trace_service import TraceService


BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_EVAL_CASES_PATH = REPO_ROOT / "eval_cases.json"
DEFAULT_SUPPLEMENTAL_CASES_PATH = BENCHMARK_DIR / "cases.json"
DEFAULT_RESULTS_PATH = BENCHMARK_DIR / "results.json"
DEFAULT_JSONL_PATH = BENCHMARK_DIR / "per_case_results.jsonl"
DEFAULT_SUMMARY_PATH = BENCHMARK_DIR / "summary.md"
DEFAULT_WORKSPACE_ROOT = BENCHMARK_DIR / "workspaces"
DRY_RUN_FINAL = "<final>Dry run: no LLM API called.</final>"


@dataclass(frozen=True)
class BenchmarkCase:
    eval_case: EvalCase
    source: str
    notes: str
    expected_failure_reasons: tuple[str, ...]


def load_benchmark_cases(
    eval_cases_path: str | Path = DEFAULT_EVAL_CASES_PATH,
    supplemental_cases_path: str | Path | None = DEFAULT_SUPPLEMENTAL_CASES_PATH,
) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    cases.extend(_load_case_specs(Path(eval_cases_path), source="eval_cases.json"))
    if supplemental_cases_path is not None:
        cases.extend(_load_case_specs(Path(supplemental_cases_path), source="evaluator_regression/cases.json"))
    seen: set[str] = set()
    for item in cases:
        case_id = item.eval_case.id
        if case_id in seen:
            raise ValueError(f"duplicate evaluator regression case id: {case_id}")
        seen.add(case_id)
    return cases


def _load_case_specs(path: Path, *, source: str) -> list[BenchmarkCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload)
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"{path} must contain a non-empty cases list")
    specs = []
    for raw in raw_cases:
        data = _normalize_case_mapping(dict(raw))
        specs.append(
            BenchmarkCase(
                eval_case=EvalCase.from_mapping(data),
                source=source,
                notes=str(raw.get("notes", "")),
                expected_failure_reasons=tuple(str(item) for item in raw.get("expected_failure_reasons", []) or []),
            )
        )
    return specs


def _normalize_case_mapping(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    if "id" not in normalized and "case_id" in normalized:
        normalized["id"] = normalized["case_id"]
    if "task" not in normalized and "prompt" in normalized:
        normalized["task"] = normalized["prompt"]
    normalized.setdefault("mock_outputs", [])
    normalized.setdefault("runtime_config", {})
    return normalized


def run_benchmark(
    *,
    eval_cases_path: str | Path = DEFAULT_EVAL_CASES_PATH,
    supplemental_cases_path: str | Path | None = DEFAULT_SUPPLEMENTAL_CASES_PATH,
    results_path: str | Path = DEFAULT_RESULTS_PATH,
    per_case_path: str | Path = DEFAULT_JSONL_PATH,
    summary_path: str | Path = DEFAULT_SUMMARY_PATH,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    cases = load_benchmark_cases(eval_cases_path, supplemental_cases_path)
    workspace_base = Path(workspace_root).resolve() if workspace_root else DEFAULT_WORKSPACE_ROOT.resolve()
    workspace_base.mkdir(parents=True, exist_ok=True)

    rows = [run_case(case, workspace_base=workspace_base) for case in cases]
    summary = summarize(rows)
    artifact = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "artifact_type": "evaluator-regression-benchmark",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "eval_cases_path": str(Path(eval_cases_path).resolve()),
        "supplemental_cases_path": None if supplemental_cases_path is None else str(Path(supplemental_cases_path).resolve()),
        "model_provider": "FakeModelClient",
        "benchmark_target": "Evaluator Regression",
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


def run_case(case_spec: BenchmarkCase, *, workspace_base: Path) -> dict[str, Any]:
    case = case_spec.eval_case
    root = workspace_base / f"{case.id}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    _write_workspace_fixture(root)
    runtime_config = dict(case.runtime_config or {})
    outputs = list(case.mock_outputs) if case.mock_outputs else [DRY_RUN_FINAL]

    agent = PureRuntime(
        model_client=FakeModelClient(outputs),
        workspace=_build_isolated_workspace_context(root),
        session_store=SessionStore(root / ".pure" / "sessions"),
        run_store=RunStore(root / ".pure" / "runs"),
        approval_policy=str(runtime_config.get("approval_policy", "auto")),
        approval_mode=runtime_config.get("approval_mode"),
        read_only=bool(runtime_config.get("read_only", False)),
        max_steps=int(case.max_steps),
        max_new_tokens=int(runtime_config.get("max_new_tokens", 128)),
        feature_flags=dict(runtime_config.get("feature_flags", {}) or {}),
        tool_repetition_guard=runtime_config.get("tool_repetition_guard"),
        runtime_config={"benchmark": "evaluator_regression", "case_id": case.id},
    )
    _apply_context_config(agent, dict(runtime_config.get("context_config", {}) or {}))

    final_answer = ""
    runner_error = ""
    try:
        final_answer = agent.ask(case.task)
    except Exception as exc:  # benchmark rows preserve runner failures
        runner_error = str(exc)

    events, report, trace_path, report_path = _load_run_artifacts(agent, final_answer=final_answer, runner_error=runner_error)
    latency_ms = infer_latency_ms(events)
    metrics = calculate_case_metrics(case, report, events, latency_ms)
    if runner_error:
        metrics["failure_reasons"] = list(metrics.get("failure_reasons", [])) + [f"runner error: {runner_error}"]
        metrics["case_passed"] = False
    return _case_result_row(
        case_spec=case_spec,
        root=root,
        report=report,
        events=events,
        metrics=metrics,
        trace_path=trace_path,
        report_path=report_path,
    )


def _write_workspace_fixture(root: Path) -> None:
    files = {
        "README.md": (
            "# Pure Evaluator Regression Fixture\n\n"
            "This fixture exposes README content for dry-run/mock evaluator cases.\n"
            "PureRuntime appears here for search and knowledge-context checks.\n"
        ),
        "pyproject.toml": "[project]\nname = \"pure-fixture\"\n",
        "pure/__init__.py": "",
        "pure/core/runtime.py": "class PureRuntime:\n    pass\n",
        "docs/architecture.md": "Pure fixture architecture notes.\n",
        "notes.txt": "Evaluator regression note.\n",
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


def _apply_context_config(agent: PureRuntime, config: dict[str, Any]) -> None:
    if not config:
        return
    manager = agent.context_manager
    if "total_budget" in config:
        manager.total_budget = int(config["total_budget"])
    if "section_budgets" in config:
        manager.section_budgets.update({str(key): int(value) for key, value in dict(config["section_budgets"]).items()})
    if "section_floors" in config:
        manager._section_floor_overrides = {
            str(key): int(value) for key, value in dict(config["section_floors"]).items()
        }
        manager.section_floors = manager._compute_section_floors()


def _load_run_artifacts(agent: PureRuntime, *, final_answer: str, runner_error: str) -> tuple[list[dict[str, Any]], dict[str, Any], str, str]:
    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "status": "failed" if runner_error else "",
        "stop_reason": "runner_error" if runner_error else "",
        "final_answer": final_answer,
        "tool_steps": 0,
    }
    trace_path = ""
    report_path = ""
    task_state = getattr(agent, "current_task_state", None)
    if task_state is None:
        return events, report, trace_path, report_path
    trace = agent.run_store.trace_path(task_state)
    report_file = agent.run_store.report_path(task_state)
    trace_path = str(trace)
    report_path = str(report_file)
    if trace.exists():
        events = TraceService.load_events(trace)
    if report_file.exists():
        report = json.loads(report_file.read_text(encoding="utf-8"))
    return events, report, trace_path, report_path


def _case_result_row(
    *,
    case_spec: BenchmarkCase,
    root: Path,
    report: dict[str, Any],
    events: list[dict[str, Any]],
    metrics: dict[str, Any],
    trace_path: str,
    report_path: str,
) -> dict[str, Any]:
    case = case_spec.eval_case
    expected_tools = list(case.expected_tools)
    actual_tools = list(metrics.get("tools_used", []))
    expected_trace_events = list(case.expected_trace_events)
    actual_trace_events = [str(event.get("event_type", "")) for event in events if str(event.get("event_type", ""))]
    success_keywords = list(case.success_keywords)
    failure_reasons = list(metrics.get("failure_reasons", []) or [])
    return {
        "case_id": case.id,
        "case_source": case_spec.source,
        "case_passed": bool(metrics.get("case_passed")),
        "final_status": str(report.get("status", "")),
        "stop_reason": str(report.get("stop_reason", "")),
        "expected_tools": expected_tools,
        "actual_tools": actual_tools,
        "expected_tool_hit": _expected_tool_hit(expected_tools, actual_tools),
        "forbidden_tools": list(case.forbidden_tools),
        "forbidden_tool_violations": list(metrics.get("forbidden_tool_hits", []) or []),
        "expected_trace_events": expected_trace_events,
        "actual_trace_events": actual_trace_events,
        "expected_trace_event_hit": bool(metrics.get("trace_event_success")),
        "expected_trace_event_hit_rate": float(metrics.get("expected_trace_event_hit_rate", 0.0) or 0.0),
        "success_keywords": success_keywords,
        "success_keyword_hit": bool(metrics.get("keyword_success")),
        "success_keyword_hits": list(metrics.get("success_keyword_hits", []) or []),
        "step_count": int(metrics.get("steps", 0) or 0),
        "step_budget": int(case.max_steps),
        "step_budget_met": bool(metrics.get("step_budget_met")),
        "repeated_tool_call_count": int(metrics.get("repeated_tool_call_count", 0) or 0),
        "tool_rejection_count": int(metrics.get("tool_rejection_count", 0) or 0),
        "security_event_count": int(metrics.get("security_event_count", 0) or 0),
        "checkpoint_created_count": sum(1 for event in events if event.get("event_type") == "checkpoint_created"),
        "failure_reasons": failure_reasons,
        "expected_failure_reasons": list(case_spec.expected_failure_reasons),
        "trace_path": trace_path,
        "report_path": report_path,
        "model_client_class": "FakeModelClient",
        "workspace_root": str(root),
        "notes": case_spec.notes,
    }


def _expected_tool_hit(expected_tools: list[str], actual_tools: list[str]) -> bool:
    if not expected_tools:
        return True
    return all(tool in actual_tools for tool in expected_tools)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [int(row["step_count"]) for row in rows]
    failure_reason_counts = dict(Counter(reason for row in rows for reason in row["failure_reasons"]))
    return {
        "case_count": len(rows),
        "case_pass_rate": _ratio(sum(1 for row in rows if row["case_passed"]), len(rows)),
        "expected_tool_hit_rate": _ratio(sum(1 for row in rows if row["expected_tool_hit"]), len(rows)),
        "forbidden_tool_violation_count": sum(len(row["forbidden_tool_violations"]) for row in rows),
        "expected_trace_event_hit_rate": _ratio(sum(1 for row in rows if row["expected_trace_event_hit"]), len(rows)),
        "success_keyword_hit_rate": _ratio(sum(1 for row in rows if row["success_keyword_hit"]), len(rows)),
        "step_budget_met_rate": _ratio(sum(1 for row in rows if row["step_budget_met"]), len(rows)),
        "repeated_tool_call_count": sum(int(row["repeated_tool_call_count"]) for row in rows),
        "tool_rejection_count": sum(int(row["tool_rejection_count"]) for row in rows),
        "security_event_count": sum(int(row["security_event_count"]) for row in rows),
        "checkpoint_created_count": sum(int(row["checkpoint_created_count"]) for row in rows),
        "failure_reason_counts": failure_reason_counts,
        "avg_steps": _mean(steps),
        "p95_steps": _percentile(steps, 0.95),
        "failed_cases": [
            {"case_id": row["case_id"], "failure_reasons": list(row["failure_reasons"])}
            for row in rows
            if not row["case_passed"]
        ],
        "case_sources": dict(Counter(row["case_source"] for row in rows)),
    }


def render_summary_markdown(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    rows = list(artifact["cases"])
    lines = [
        "# Evaluator Regression Benchmark",
        "",
        "## Goal",
        "",
        "This benchmark validates Pure runtime behavior and evaluator contracts with fixed offline cases. It measures trace events, tool use, guardrail outcomes, step budget behavior, and failure reasons; it does not measure real model capability.",
        "",
        "## Cases",
        "",
        "| Case | Source | Status | Steps | Passed |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_id']} | {row['case_source']} | {row['final_status']} / {row['stop_reason']} | "
            f"{row['step_count']} | {str(row['case_passed']).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| case_count | {summary['case_count']} |",
            f"| case_pass_rate | {_pct(summary['case_pass_rate'])} |",
            f"| expected_tool_hit_rate | {_pct(summary['expected_tool_hit_rate'])} |",
            f"| forbidden_tool_violation_count | {summary['forbidden_tool_violation_count']} |",
            f"| expected_trace_event_hit_rate | {_pct(summary['expected_trace_event_hit_rate'])} |",
            f"| success_keyword_hit_rate | {_pct(summary['success_keyword_hit_rate'])} |",
            f"| step_budget_met_rate | {_pct(summary['step_budget_met_rate'])} |",
            f"| repeated_tool_call_count | {summary['repeated_tool_call_count']} |",
            f"| tool_rejection_count | {summary['tool_rejection_count']} |",
            f"| security_event_count | {summary['security_event_count']} |",
            f"| checkpoint_created_count | {summary['checkpoint_created_count']} |",
            f"| avg_steps | {summary['avg_steps']:.2f} |",
            f"| p95_steps | {summary['p95_steps']:.2f} |",
            "",
            "## Failure Reasons",
            "",
            "| Reason | Count |",
            "| --- | ---: |",
        ]
    )
    if summary["failure_reason_counts"]:
        for reason, count in sorted(summary["failure_reason_counts"].items()):
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("| - | 0 |")
    lines.extend(
        [
            "",
            "## Trace Event Coverage",
            "",
            "| Case | Expected Trace Hit | Hit Rate |",
            "| --- | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(f"| {row['case_id']} | {str(row['expected_trace_event_hit']).lower()} | {_pct(row['expected_trace_event_hit_rate'])} |")
    lines.extend(
        [
            "",
            "## Tool Policy Coverage",
            "",
            "| Case | Forbidden Violations | Tool Rejections | Security Events |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for row in rows:
        violations = ", ".join(row["forbidden_tool_violations"]) or "-"
        lines.append(
            f"| {row['case_id']} | {violations} | {row['tool_rejection_count']} | {row['security_event_count']} |"
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Uses FakeModelClient.",
            "- Does not represent real model pass rate.",
            "- Does not represent SWE-bench.",
            "- Case count is limited.",
            "- Better suited for validating runtime contracts than model intelligence.",
            "",
            "## Resume Bullet Candidate",
            "",
            "- Built a reproducible offline Evaluator Regression benchmark for Pure that reuses eval_cases.json plus benchmark-only cases to track runtime contracts, trace events, tool policy violations, repeated-call guard events, security events, step limits, and failure reasons from FakeModelClient runs.",
            "",
            "## Reproduction Command",
            "",
            "```bash",
            "python benchmarks/pure/evaluator_regression/run_evaluator_regression_benchmark.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _mean(values: list[int]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _percentile(values: list[int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(percentile)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _pct(value: float) -> str:
    return f"{float(value) * 100:.2f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pure's offline Evaluator Regression benchmark.")
    parser.add_argument("--eval-cases", default=str(DEFAULT_EVAL_CASES_PATH), help="Path to eval_cases.json.")
    parser.add_argument("--supplemental-cases", default=str(DEFAULT_SUPPLEMENTAL_CASES_PATH), help="Path to benchmark-only supplemental cases JSON.")
    parser.add_argument("--no-supplemental-cases", action="store_true", help="Run only --eval-cases.")
    parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH), help="Path to write results.json.")
    parser.add_argument("--per-case", default=str(DEFAULT_JSONL_PATH), help="Path to write per_case_results.jsonl.")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY_PATH), help="Path to write summary.md.")
    parser.add_argument("--workspace-root", default=None, help="Optional directory for generated fixture workspaces.")
    args = parser.parse_args(argv)
    artifact = run_benchmark(
        eval_cases_path=args.eval_cases,
        supplemental_cases_path=None if args.no_supplemental_cases else args.supplemental_cases,
        results_path=args.results,
        per_case_path=args.per_case,
        summary_path=args.summary,
        workspace_root=args.workspace_root,
    )
    print(json.dumps(artifact["summary"], indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
