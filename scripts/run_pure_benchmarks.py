import argparse
import importlib.util
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = REPO_ROOT / "benchmarks" / "pure"
DEFAULT_OUTPUT = BENCHMARK_ROOT / "_runs" / "latest"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SUITES: dict[str, dict[str, str]] = {
    "context_compression": {
        "label": "Context Compression",
        "script": "benchmarks/pure/context_compression/run_context_compression_benchmark.py",
        "scope": "Measures prompt character reduction with current request preservation, verifier checks, and final status.",
    },
    "tool_loop": {
        "label": "Tool Loop / Repetition Guard",
        "script": "benchmarks/pure/tool_loop/run_tool_loop_benchmark.py",
        "scope": "Compares guard off, warn, and block modes for short-window repeated tool calls.",
    },
    "tool_governance": {
        "label": "Tool Governance",
        "script": "benchmarks/pure/tool_governance/run_tool_governance_benchmark.py",
        "scope": "Measures ToolGateway argument validation, approval policy, workspace boundary checks, and risky tool audit fields.",
    },
    "checkpoint_resume": {
        "label": "Checkpoint / Resume",
        "script": "benchmarks/pure/checkpoint_resume/run_checkpoint_resume_benchmark.py",
        "scope": "Measures checkpoint metadata, workspace mismatch, schema mismatch, runtime identity, and context-reduction checkpoint events.",
    },
    "evaluator_regression": {
        "label": "Evaluator Regression",
        "script": "benchmarks/pure/evaluator_regression/run_evaluator_regression_benchmark.py",
        "scope": "Runs offline evaluator cases with FakeModelClient and reports runtime behavior, trace events, tool policy, and step budget.",
    },
}


KEY_METRIC_NAMES: dict[str, tuple[str, ...]] = {
    "context_compression": (
        "case_count",
        "avg_compression_rate",
        "verifier_pass_rate",
        "current_request_preserved_rate",
        "normal_final_rate",
        "negative_compression_case_count",
    ),
    "tool_loop": (
        "case_count",
        "variant_count",
        "trace_event_hit_rate",
    ),
    "tool_governance": (
        "case_count",
        "policy_pass_rate",
        "unsafe_rejection_rate",
        "workspace_escape_block_rate",
        "risky_tool_audit_coverage",
        "false_allow_count",
        "false_reject_count",
    ),
    "checkpoint_resume": (
        "case_count",
        "resume_status_accuracy",
        "mismatch_detection_rate",
        "checkpoint_event_hit_rate",
        "false_accept_count",
        "false_reject_count",
    ),
    "evaluator_regression": (
        "case_count",
        "case_pass_rate",
        "expected_trace_event_hit_rate",
        "forbidden_tool_violation_count",
        "step_budget_met_rate",
    ),
}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    suites = list(SUITES) if args.all else [args.suite]
    output_root = Path(args.output).resolve()

    artifact = run_benchmark_suites(
        suites=suites,
        output_root=output_root,
        command=_command_string(),
    )
    print(json.dumps(artifact["key_metrics"], indent=2, sort_keys=True, ensure_ascii=True))
    return 1 if artifact["failed_suites"] else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Pure benchmark suites with a unified output structure.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Run every Pure benchmark suite.")
    group.add_argument("--suite", choices=sorted(SUITES), help="Run one Pure benchmark suite.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Directory for the unified benchmark run artifacts.",
    )
    return parser.parse_args(argv)


def run_benchmark_suites(
    *,
    suites: list[str],
    output_root: str | Path,
    command: str,
) -> dict[str, Any]:
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    suite_results: dict[str, dict[str, Any]] = {}
    key_metrics: dict[str, dict[str, Any]] = {}
    failed_suites: list[str] = []

    for suite in suites:
        if suite not in SUITES:
            raise ValueError(f"unknown benchmark suite: {suite}")
        suite_dir = output_root / suite
        try:
            suite_result = run_suite(suite, suite_dir)
        except Exception as exc:
            failed_suites.append(suite)
            suite_result = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "results_path": str((suite_dir / "results.json").resolve()),
                "per_case_path": str((suite_dir / "per_case_results.jsonl").resolve()),
                "summary_path": str((suite_dir / "summary.md").resolve()),
                "key_metrics": {},
            }
        suite_results[suite] = suite_result
        key_metrics[suite] = dict(suite_result.get("key_metrics", {}))

    artifact = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "python_version": sys.version.split()[0],
        "command": command,
        "suite_results": suite_results,
        "key_metrics": key_metrics,
        "failed_suites": failed_suites,
        "no_real_llm_used": True,
    }

    (output_root / "summary.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "summary.md").write_text(render_summary_markdown(artifact), encoding="utf-8")
    return artifact


def run_suite(suite: str, suite_dir: Path) -> dict[str, Any]:
    suite_dir.mkdir(parents=True, exist_ok=True)
    module = _load_suite_module(suite)
    results_path = suite_dir / "results.json"
    per_case_path = suite_dir / "per_case_results.jsonl"
    summary_path = suite_dir / "summary.md"
    workspace_root = suite_dir / "workspaces"

    artifact = module.run_benchmark(
        results_path=results_path,
        per_case_path=per_case_path,
        summary_path=summary_path,
        workspace_root=workspace_root,
    )
    summary = dict(artifact.get("summary", {}))
    key_metrics = extract_key_metrics(suite, summary)
    return {
        "status": "completed",
        "label": SUITES[suite]["label"],
        "artifact_type": artifact.get("artifact_type"),
        "model_provider": artifact.get("model_provider"),
        "results_path": str(results_path.resolve()),
        "per_case_path": str(per_case_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "workspace_root": str(workspace_root.resolve()),
        "case_count": summary.get("case_count"),
        "key_metrics": key_metrics,
        "failed_cases": summary.get("failed_cases", []),
    }


def extract_key_metrics(suite: str, summary: dict[str, Any]) -> dict[str, Any]:
    metrics = {name: summary.get(name) for name in KEY_METRIC_NAMES[suite] if name in summary}
    if suite == "tool_loop":
        guard_modes = summary.get("guard_modes", {})
        if isinstance(guard_modes, dict):
            block = guard_modes.get("block", {})
            warn = guard_modes.get("warn", {})
            off = guard_modes.get("off", {})
            if isinstance(off, dict):
                metrics["off_repeated_tool_call_count"] = off.get("repeated_tool_call_count")
            if isinstance(warn, dict):
                metrics["warn_repeated_tool_call_detected_events"] = warn.get("repeated_tool_call_detected_events")
            if isinstance(block, dict):
                metrics["block_repeated_tool_call_count"] = block.get("repeated_tool_call_count")
                metrics["block_blocked_call_rate"] = block.get("blocked_call_rate")
                metrics["block_avg_repeated_call_reduction_rate"] = block.get(
                    "avg_repeated_call_reduction_rate"
                )
    return metrics


def render_summary_markdown(artifact: dict[str, Any]) -> str:
    lines = [
        "# Pure Benchmark Suite",
        "",
        "## Benchmark Scope",
        "",
    ]
    for suite, config in SUITES.items():
        if suite in artifact["suite_results"]:
            lines.append(f"- **{config['label']}**: {config['scope']}")
    lines.extend(
        [
            "",
            "## Key Results",
            "",
            "| Suite | Status | Key Metrics | Failed Cases |",
            "|---|---:|---|---:|",
        ]
    )
    for suite, result in artifact["suite_results"].items():
        metrics = _metrics_text(result.get("key_metrics", {}))
        failed_case_count = len(result.get("failed_cases") or [])
        lines.append(
            f"| {SUITES[suite]['label']} | {result.get('status', 'unknown')} | {metrics} | {failed_case_count} |"
        )

    lines.extend(
        [
            "",
            "## What Can Be Claimed",
            "",
            "- Pure has an offline benchmark harness for runtime behavior across context handling, tool loop control, tool governance, checkpoint/resume validation, and evaluator regression.",
            "- The reported numbers come from the current Pure codebase and the checked benchmark cases for this run.",
            "- The benchmark uses deterministic FakeModelClient/mock outputs, which makes it suitable for repeatable runtime regression checks.",
            "",
            "## What Cannot Be Claimed",
            "",
            "- Cannot claim real model success rate.",
            "- Cannot claim SWE-bench performance.",
            "- Cannot claim token cost reduction unless tokens are actually measured.",
            "- Cannot claim production-grade security.",
            "- Cannot cite external project data as Pure data.",
            "",
            "## Limitations",
            "",
            "- Uses FakeModelClient and mock outputs.",
            "- Offline benchmark only.",
            "- Case count is limited.",
            "- Runtime behavior benchmark; not a real coding success-rate benchmark.",
            "",
            "## Reproduction Commands",
            "",
            "```bash",
            "python scripts/run_pure_benchmarks.py --all --output benchmarks/pure/_runs/latest",
            "python scripts/run_pure_benchmarks.py --suite context_compression --output benchmarks/pure/_runs/context_compression",
            "python scripts/run_pure_benchmarks.py --suite tool_loop --output benchmarks/pure/_runs/tool_loop",
            "python scripts/run_pure_benchmarks.py --suite tool_governance --output benchmarks/pure/_runs/tool_governance",
            "python scripts/run_pure_benchmarks.py --suite checkpoint_resume --output benchmarks/pure/_runs/checkpoint_resume",
            "python scripts/run_pure_benchmarks.py --suite evaluator_regression --output benchmarks/pure/_runs/evaluator_regression",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def _load_suite_module(suite: str) -> Any:
    script_path = REPO_ROOT / SUITES[suite]["script"]
    spec = importlib.util.spec_from_file_location(f"pure_benchmark_{suite}", script_path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"could not load benchmark suite module: {suite}")
    spec.loader.exec_module(module)
    return module


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None


def _command_string() -> str:
    return subprocess.list2cmdline([sys.executable, *sys.argv])


def _metrics_text(metrics: dict[str, Any]) -> str:
    if not metrics:
        return ""
    return "; ".join(f"{key}={_format_metric(value)}" for key, value in metrics.items())


def _format_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
