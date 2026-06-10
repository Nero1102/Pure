import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path("scripts/run_pure_benchmarks.py").resolve()
REPO_ROOT = Path.cwd()


def _run_runner(output: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args, "--output", str(output)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=360,
        check=False,
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def context_compression_run(tmp_path_factory):
    output = tmp_path_factory.mktemp("context_compression_run")
    result = _run_runner(output, "--suite", "context_compression")
    return output, result


@pytest.fixture(scope="module")
def tool_loop_run(tmp_path_factory):
    output = tmp_path_factory.mktemp("tool_loop_run")
    result = _run_runner(output, "--suite", "tool_loop")
    return output, result


@pytest.fixture(scope="module")
def all_run(tmp_path_factory):
    output = tmp_path_factory.mktemp("all_run")
    result = _run_runner(output, "--all")
    return output, result


def test_context_compression_suite_can_run(context_compression_run):
    output, result = context_compression_run
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _load_json(output / "summary.json")
    assert "context_compression" in summary["suite_results"]
    assert (output / "context_compression" / "results.json").exists()
    assert summary["suite_results"]["context_compression"]["model_provider"] == "FakeModelClient"


def test_tool_loop_suite_can_run(tool_loop_run):
    output, result = tool_loop_run
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _load_json(output / "summary.json")
    assert "tool_loop" in summary["suite_results"]
    assert (output / "tool_loop" / "per_case_results.jsonl").exists()
    assert summary["suite_results"]["tool_loop"]["model_provider"] == "FakeModelClient"


def test_all_suites_generate_summary_json(all_run):
    output, result = all_run
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _load_json(output / "summary.json")
    assert set(summary["suite_results"]) == {
        "context_compression",
        "tool_loop",
        "tool_governance",
        "checkpoint_resume",
        "evaluator_regression",
    }
    assert summary["failed_suites"] == []
    assert summary["no_real_llm_used"] is True


def test_summary_json_contains_required_fields(all_run):
    output, result = all_run
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _load_json(output / "summary.json")
    assert {
        "run_id",
        "timestamp",
        "python_version",
        "suite_results",
        "key_metrics",
    } <= set(summary)


def test_summary_markdown_contains_claim_boundaries(all_run):
    output, result = all_run
    assert result.returncode == 0, result.stdout + result.stderr
    summary_text = (output / "summary.md").read_text(encoding="utf-8")
    assert "## What Can Be Claimed" in summary_text
    assert "## What Cannot Be Claimed" in summary_text
    assert "Cannot claim real model success rate." in summary_text
    assert "Cannot claim SWE-bench performance." in summary_text


def test_readme_contains_benchmark_section_and_results_link():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "## Benchmarks" in readme
    assert "docs/benchmarks/pure-benchmark-results.md" in readme
    assert "FakeModelClient" in readme


def test_unified_runner_does_not_call_real_llm(all_run):
    output, result = all_run
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _load_json(output / "summary.json")
    assert summary["no_real_llm_used"] is True
    assert all(
        suite_result["model_provider"] == "FakeModelClient"
        for suite_result in summary["suite_results"].values()
    )


def test_summary_metrics_are_derived_from_suite_artifacts(all_run):
    output, result = all_run
    assert result.returncode == 0, result.stdout + result.stderr
    unified = _load_json(output / "summary.json")
    context_artifact = _load_json(output / "context_compression" / "results.json")
    governance_artifact = _load_json(output / "tool_governance" / "results.json")

    assert unified["key_metrics"]["context_compression"]["case_count"] == context_artifact["summary"]["case_count"]
    assert (
        unified["key_metrics"]["context_compression"]["avg_compression_rate"]
        == context_artifact["summary"]["avg_compression_rate"]
    )
    assert unified["key_metrics"]["tool_governance"]["policy_pass_rate"] == governance_artifact["summary"][
        "policy_pass_rate"
    ]
    assert unified["suite_results"]["tool_governance"]["results_path"].endswith("results.json")
