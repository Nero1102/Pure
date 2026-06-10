import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path("benchmarks/pure/evaluator_regression/run_evaluator_regression_benchmark.py").resolve()
EVAL_CASES_PATH = Path("eval_cases.json").resolve()
SUPPLEMENTAL_CASES_PATH = Path("benchmarks/pure/evaluator_regression/cases.json").resolve()


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("evaluator_regression_benchmark", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def benchmark_module():
    return _load_benchmark_module()


@pytest.fixture(scope="module")
def benchmark_run(tmp_path_factory, benchmark_module):
    root = tmp_path_factory.mktemp("evaluator_regression_benchmark")
    artifact = benchmark_module.run_benchmark(
        eval_cases_path=EVAL_CASES_PATH,
        supplemental_cases_path=SUPPLEMENTAL_CASES_PATH,
        results_path=root / "results.json",
        per_case_path=root / "per_case_results.jsonl",
        summary_path=root / "summary.md",
        workspace_root=root / "workspaces",
    )
    return {"root": root, "artifact": artifact}


def test_evaluator_regression_benchmark_runner_can_run(benchmark_run):
    artifact = benchmark_run["artifact"]

    assert artifact["artifact_type"] == "evaluator-regression-benchmark"
    assert artifact["summary"]["case_count"] >= 10
    assert artifact["cases"]


def test_results_json_schema_is_correct(benchmark_run):
    artifact = benchmark_run["artifact"]
    persisted = json.loads((benchmark_run["root"] / "results.json").read_text(encoding="utf-8"))

    assert persisted == artifact
    assert artifact["schema_version"] == 1
    assert artifact["model_provider"] == "FakeModelClient"
    assert {
        "case_count",
        "case_pass_rate",
        "expected_trace_event_hit_rate",
        "failure_reason_counts",
        "failed_cases",
    } <= set(artifact["summary"])


def test_per_case_results_jsonl_is_generated(benchmark_run):
    rows_path = benchmark_run["root"] / "per_case_results.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]

    assert len(rows) == benchmark_run["artifact"]["summary"]["case_count"]
    assert all("case_id" in row for row in rows)
    assert all("trace_path" in row and "report_path" in row for row in rows)


def test_summary_markdown_is_generated(benchmark_run):
    summary_text = (benchmark_run["root"] / "summary.md").read_text(encoding="utf-8")

    assert "# Evaluator Regression Benchmark" in summary_text
    assert "## Trace Event Coverage" in summary_text
    assert "## Tool Policy Coverage" in summary_text
    assert "Uses FakeModelClient." in summary_text


def test_expected_trace_event_hit_rate_is_calculated(benchmark_run):
    rows = benchmark_run["artifact"]["cases"]
    summary = benchmark_run["artifact"]["summary"]

    expected = sum(1 for row in rows if row["expected_trace_event_hit"]) / len(rows)
    assert summary["expected_trace_event_hit_rate"] == pytest.approx(expected)


def test_forbidden_tool_violation_count_is_calculated(benchmark_run):
    rows = benchmark_run["artifact"]["cases"]
    summary = benchmark_run["artifact"]["summary"]

    assert summary["forbidden_tool_violation_count"] == sum(
        len(row["forbidden_tool_violations"]) for row in rows
    )
    violation_row = next(row for row in rows if row["case_id"] == "forbidden_tool_guard_violation")
    assert violation_row["forbidden_tool_violations"] == ["run_shell"]


def test_failure_reason_counts_are_generated(benchmark_run):
    counts = benchmark_run["artifact"]["summary"]["failure_reason_counts"]

    assert counts
    assert counts["forbidden tools used: run_shell"] == 1
    assert counts["run status was not completed"] == 1


def test_uses_fake_model_client_without_real_llm(benchmark_run):
    artifact = benchmark_run["artifact"]

    assert artifact["model_provider"] == "FakeModelClient"
    assert all(row["model_client_class"] == "FakeModelClient" for row in artifact["cases"])


def test_benchmark_results_are_not_hardcoded(tmp_path, benchmark_module):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "custom_eval_case",
                        "prompt": "Return a custom benchmark answer.",
                        "mock_outputs": ["<final>Custom benchmark answer.</final>"],
                        "expected_tools": [],
                        "forbidden_tools": ["run_shell", "write_file", "patch_file"],
                        "expected_trace_events": ["run_started", "run_completed"],
                        "success_keywords": ["Custom benchmark answer"],
                        "max_steps": 1,
                        "runtime_config": {},
                        "notes": "custom non-hardcoded case",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    artifact = benchmark_module.run_benchmark(
        eval_cases_path=cases_path,
        supplemental_cases_path=None,
        results_path=tmp_path / "results.json",
        per_case_path=tmp_path / "per_case_results.jsonl",
        summary_path=tmp_path / "summary.md",
        workspace_root=tmp_path / "workspaces",
    )

    assert artifact["summary"]["case_count"] == 1
    assert artifact["cases"][0]["case_id"] == "custom_eval_case"
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8")) == artifact


def test_runner_does_not_modify_eval_cases_to_improve_metrics(tmp_path, benchmark_module):
    before = EVAL_CASES_PATH.read_text(encoding="utf-8")

    benchmark_module.run_benchmark(
        eval_cases_path=EVAL_CASES_PATH,
        supplemental_cases_path=SUPPLEMENTAL_CASES_PATH,
        results_path=tmp_path / "results.json",
        per_case_path=tmp_path / "per_case_results.jsonl",
        summary_path=tmp_path / "summary.md",
        workspace_root=tmp_path / "workspaces",
    )

    assert EVAL_CASES_PATH.read_text(encoding="utf-8") == before
