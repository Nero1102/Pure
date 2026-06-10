import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path("benchmarks/pure/context_compression/run_context_compression_benchmark.py").resolve()


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("context_compression_benchmark", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_context_compression_cases_include_required_minimum_cases():
    benchmark = _load_benchmark_module()

    cases = benchmark.load_cases(Path("benchmarks/pure/context_compression/cases.json"))

    case_ids = {case["case_id"] for case in cases}
    assert len(cases) >= 12
    assert {
        "short_history_no_reduction",
        "long_history_budget_pressure",
        "repeated_tool_outputs",
        "knowledge_heavy_prompt",
        "current_request_must_preserve",
        "mixed_sections_pressure",
        "checkpoint_context_case",
        "tool_observation_heavy_case",
        "final_answer_case",
        "malformed_then_retry_case",
        "no_compression_expected_case",
        "negative_compression_allowed_case",
    } <= case_ids


def test_context_compression_benchmark_runner_outputs_schema_and_real_metrics(tmp_path):
    benchmark = _load_benchmark_module()
    all_cases = benchmark.load_cases(Path("benchmarks/pure/context_compression/cases.json"))
    selected = [
        next(case for case in all_cases if case["case_id"] == "short_history_no_reduction"),
        next(case for case in all_cases if case["case_id"] == "negative_compression_allowed_case"),
    ]
    cases_path = tmp_path / "cases.json"
    results_path = tmp_path / "results.json"
    per_case_path = tmp_path / "per_case_results.jsonl"
    summary_path = tmp_path / "summary.md"
    cases_path.write_text(json.dumps({"cases": selected}), encoding="utf-8")

    artifact = benchmark.run_benchmark(
        cases_path=cases_path,
        results_path=results_path,
        per_case_path=per_case_path,
        summary_path=summary_path,
        workspace_root=tmp_path / "workspaces",
    )

    assert results_path.exists()
    assert per_case_path.exists()
    assert summary_path.exists()
    persisted = json.loads(results_path.read_text(encoding="utf-8"))
    assert persisted == artifact
    assert artifact["schema_version"] == 1
    assert artifact["artifact_type"] == "context-compression-benchmark"
    assert artifact["model_provider"] == "FakeModelClient"
    assert artifact["metric_unit"] == "characters"
    assert artifact["summary"]["case_count"] == 2

    rows = [json.loads(line) for line in per_case_path.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert all(row["baseline_prompt_chars"] > 0 for row in rows)
    assert all("current_request_preserved" in row for row in rows)
    for row in rows:
        expected = (row["baseline_prompt_chars"] - row["reduced_prompt_chars"]) / row["baseline_prompt_chars"]
        assert row["compression_rate"] == pytest.approx(expected)

    negative_rows = [row for row in rows if row["case_id"] == "negative_compression_allowed_case"]
    assert negative_rows
    assert negative_rows[-1]["compression_rate"] < 0
    assert artifact["summary"]["negative_compression_case_count"] == 1
    assert artifact["summary"]["verifier_pass_rate"] == 1.0

    case_rates = [case["compression_rate"] for case in artifact["cases"]]
    assert artifact["summary"]["avg_compression_rate"] == pytest.approx(sum(case_rates) / len(case_rates))

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "compression_rate = (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars" in summary_text
    assert "It cannot claim token cost reduction." in summary_text
    assert "It cannot claim real model capability improvement." in summary_text
