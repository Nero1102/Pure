import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path("benchmarks/pure/tool_loop/run_tool_loop_benchmark.py").resolve()
CASES_PATH = Path("benchmarks/pure/tool_loop/cases.json").resolve()


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("tool_loop_benchmark", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def benchmark_module():
    return _load_benchmark_module()


@pytest.fixture(scope="module")
def benchmark_run(tmp_path_factory, benchmark_module):
    root = tmp_path_factory.mktemp("tool_loop_benchmark")
    artifact = benchmark_module.run_benchmark(
        cases_path=CASES_PATH,
        results_path=root / "results.json",
        per_case_path=root / "per_case_results.jsonl",
        summary_path=root / "summary.md",
        workspace_root=root / "workspaces",
    )
    rows = {(row["case_id"], row["guard_mode"]): row for row in artifact["rows"]}
    return {"root": root, "artifact": artifact, "rows": rows}


def test_tool_loop_cases_include_required_minimum_cases(benchmark_module):
    cases = benchmark_module.load_cases(CASES_PATH)

    case_ids = {case["case_id"] for case in cases}
    assert len(cases) >= 12
    assert {
        "repeated_list_files_same_path",
        "repeated_list_files_windows_path",
        "repeated_list_files_slash_path",
        "repeated_read_file_same_file",
        "different_args_not_repeated",
        "ignored_non_semantic_args",
        "window_expiry_allows_repeat",
        "warn_mode_continues_execution",
        "block_mode_rejects_execution",
        "write_tool_resets_window",
        "repeated_search_query",
        "mixed_tool_sequence",
    } <= case_ids


def test_guard_off_repeated_calls_actually_happen(benchmark_run):
    row = benchmark_run["rows"][("repeated_list_files_same_path", "off")]

    assert row["repeated_tool_call_count"] == 1
    assert row["repeated_tool_call_detected_events"] == 0
    assert row["tool_rejected_repeated_call_events"] == 0
    assert row["passed"] is True


def test_warn_mode_does_not_block_execution(benchmark_run):
    rows = benchmark_run["rows"]
    off = rows[("warn_mode_continues_execution", "off")]
    warn = rows[("warn_mode_continues_execution", "warn")]

    assert warn["executed_tool_call_count"] == off["executed_tool_call_count"]
    assert warn["blocked_repeated_call_count"] == 0
    assert warn["final_status"] == "completed"
    assert warn["passed"] is True


def test_warn_mode_emits_repeated_tool_call_detected_event(benchmark_run):
    row = benchmark_run["rows"][("repeated_list_files_same_path", "warn")]

    assert row["repeated_tool_call_detected_events"] == 1
    assert row["observation_contains_warning"] is True
    assert row["passed"] is True


def test_block_mode_rejects_repeated_execution(benchmark_run):
    rows = benchmark_run["rows"]
    off = rows[("block_mode_rejects_execution", "off")]
    block = rows[("block_mode_rejects_execution", "block")]

    assert block["executed_tool_call_count"] < off["executed_tool_call_count"]
    assert block["repeated_tool_call_count"] == 0
    assert block["blocked_repeated_call_count"] == 1
    assert block["passed"] is True


def test_block_mode_emits_tool_rejected_repeated_call_event(benchmark_run):
    row = benchmark_run["rows"][("repeated_list_files_same_path", "block")]

    assert row["tool_rejected_repeated_call_events"] == 1
    assert row["observation_contains_warning"] is True
    assert row["passed"] is True


def test_windows_and_slash_paths_are_normalized(benchmark_run):
    rows = benchmark_run["rows"]
    windows = rows[("repeated_list_files_windows_path", "warn")]
    slash = rows[("repeated_list_files_slash_path", "warn")]

    assert windows["repeated_tool_call_count"] == 1
    assert slash["repeated_tool_call_count"] == 1
    assert '{"path":"pure"}' in windows["normalized_args_samples"]
    assert '{"path":"pure"}' in slash["normalized_args_samples"]


def test_different_args_are_not_repeated(benchmark_run):
    rows = benchmark_run["rows"]

    for mode in ("off", "warn", "block"):
        row = rows[("different_args_not_repeated", mode)]
        assert row["repeated_tool_call_count"] == 0
        assert row["blocked_repeated_call_count"] == 0
        assert row["passed"] is True


def test_window_expiry_allows_repeat(benchmark_run):
    rows = benchmark_run["rows"]

    for mode in ("off", "warn", "block"):
        row = rows[("window_expiry_allows_repeat", mode)]
        assert row["repeated_tool_call_count"] == 0
        assert row["blocked_repeated_call_count"] == 0
        assert row["passed"] is True


def test_non_semantic_args_are_ignored(benchmark_run):
    row = benchmark_run["rows"][("ignored_non_semantic_args", "warn")]

    assert row["repeated_tool_call_count"] == 1
    assert '{"path":"pure"}' in row["normalized_args_samples"]
    assert row["passed"] is True


def test_summary_metrics_are_derived_from_rows(benchmark_run):
    artifact = benchmark_run["artifact"]
    rows = artifact["rows"]
    summary = artifact["summary"]

    assert summary["case_count"] == len({row["case_id"] for row in rows})
    for mode in ("off", "warn", "block"):
        mode_rows = [row for row in rows if row["guard_mode"] == mode]
        mode_summary = summary["guard_modes"][mode]
        assert mode_summary["total_tool_calls"] == sum(row["total_tool_calls"] for row in mode_rows)
        assert mode_summary["repeated_tool_call_count"] == sum(row["repeated_tool_call_count"] for row in mode_rows)
        assert mode_summary["blocked_repeated_call_count"] == sum(row["blocked_repeated_call_count"] for row in mode_rows)


def test_benchmark_results_are_not_hardcoded(tmp_path, benchmark_module):
    case = next(case for case in benchmark_module.load_cases(CASES_PATH) if case["case_id"] == "different_args_not_repeated")
    custom_case = dict(case)
    custom_case["case_id"] = "different_args_custom"
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps({"cases": [custom_case]}), encoding="utf-8")

    artifact = benchmark_module.run_benchmark(
        cases_path=cases_path,
        results_path=tmp_path / "results.json",
        per_case_path=tmp_path / "per_case_results.jsonl",
        summary_path=tmp_path / "summary.md",
        workspace_root=tmp_path / "workspaces",
    )

    persisted = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (tmp_path / "per_case_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert persisted == artifact
    assert artifact["summary"]["case_count"] == 1
    assert {row["case_id"] for row in artifact["rows"]} == {"different_args_custom"}
    assert len(rows) == 3


def test_summary_documents_required_limitations(benchmark_run):
    summary_text = (benchmark_run["root"] / "summary.md").read_text(encoding="utf-8")

    assert "only addresses short-window repeated tool calls" in summary_text
    assert "cannot replace a planner" in summary_text
    assert "parameters differ but are semantically similar" in summary_text
    assert "does not represent real model overall success rate" in summary_text
