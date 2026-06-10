import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path("benchmarks/pure/checkpoint_resume/run_checkpoint_resume_benchmark.py").resolve()
CASES_PATH = Path("benchmarks/pure/checkpoint_resume/cases.json").resolve()
REPO_ROOT = Path.cwd()


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("checkpoint_resume_benchmark", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def benchmark_module():
    return _load_benchmark_module()


@pytest.fixture(scope="module")
def benchmark_run(tmp_path_factory, benchmark_module):
    root = tmp_path_factory.mktemp("checkpoint_resume_benchmark")
    artifact = benchmark_module.run_benchmark(
        cases_path=CASES_PATH,
        results_path=root / "results.json",
        per_case_path=root / "per_case_results.jsonl",
        summary_path=root / "summary.md",
        workspace_root=root / "workspaces",
    )
    rows = {row["case_id"]: row for row in artifact["cases"]}
    return {"root": root, "artifact": artifact, "rows": rows}


def test_checkpoint_resume_cases_include_required_minimum_cases(benchmark_module):
    cases = benchmark_module.load_cases(CASES_PATH)

    case_ids = {case["case_id"] for case in cases}
    assert len(cases) >= 10
    assert {
        "clean_resume",
        "partial_stale_single_file_change",
        "workspace_mismatch_many_files",
        "workspace_mismatch_repo_root_changed",
        "schema_mismatch",
        "runtime_identity_model_changed",
        "runtime_identity_approval_changed",
        "tool_signature_changed",
        "context_reduction_checkpoint",
        "workspace_mismatch_checkpoint_created",
    } <= case_ids


def test_clean_resume_is_not_wrongly_rejected(benchmark_run):
    row = benchmark_run["rows"]["clean_resume"]

    assert row["actual_resume_status"] == "full-valid"
    assert row["actual_allowed_to_continue"] is True
    assert row["false_reject"] is False
    assert row["passed"] is True


def test_single_file_change_is_detected_as_partial_stale(benchmark_run):
    row = benchmark_run["rows"]["partial_stale_single_file_change"]

    assert row["actual_resume_status"] == "partial-stale"
    assert row["checkpoint_trigger"] == "freshness_mismatch"
    assert "runtime.py" in row["stale_paths"]
    assert row["passed"] is True


def test_large_workspace_change_is_detected_as_mismatch(benchmark_run):
    row = benchmark_run["rows"]["workspace_mismatch_many_files"]

    assert row["actual_resume_status"] == "workspace-mismatch"
    assert row["checkpoint_trigger"] == "workspace_mismatch"
    assert "runtime_identity_mismatch" in row["actual_trace_events"]
    assert row["passed"] is True


def test_schema_mismatch_is_detected(benchmark_run):
    row = benchmark_run["rows"]["schema_mismatch"]

    assert row["schema_version_before"] == "phase1-v1"
    assert row["schema_version_after"] == "legacy-v0"
    assert row["actual_resume_status"] == "schema-mismatch"
    assert row["passed"] is True


def test_model_provider_change_triggers_runtime_identity_mismatch(benchmark_run):
    row = benchmark_run["rows"]["runtime_identity_model_changed"]

    assert row["actual_resume_status"] == "workspace-mismatch"
    assert row["runtime_identity_match"] is False
    assert "model" in row["runtime_identity_mismatch_fields"]
    assert row["passed"] is True


def test_approval_change_triggers_identity_mismatch(benchmark_run):
    row = benchmark_run["rows"]["runtime_identity_approval_changed"]

    assert row["actual_resume_status"] == "workspace-mismatch"
    assert row["runtime_identity_match"] is False
    assert "approval_policy" in row["runtime_identity_mismatch_fields"]
    assert row["passed"] is True


def test_context_reduction_produces_checkpoint_created(benchmark_run):
    row = benchmark_run["rows"]["context_reduction_checkpoint"]

    assert row["actual_resume_status"] == "full-valid"
    assert row["checkpoint_created"] is True
    assert row["checkpoint_trigger"] == "context_reduction"
    assert "checkpoint_created" in row["actual_trace_events"]
    assert row["passed"] is True


def test_summary_metrics_are_derived_from_rows(benchmark_run):
    artifact = benchmark_run["artifact"]
    rows = artifact["cases"]
    summary = artifact["summary"]

    assert summary["case_count"] == len(rows)
    assert summary["resume_status_accuracy"] == pytest.approx(
        sum(1 for row in rows if row["actual_resume_status"] == row["expected_resume_status"]) / len(rows)
    )
    mismatch_rows = [row for row in rows if row["expected_resume_status"] not in {"full-valid", "no-checkpoint"}]
    assert summary["mismatch_detection_rate"] == pytest.approx(
        sum(1 for row in mismatch_rows if row["actual_resume_status"] == row["expected_resume_status"])
        / len(mismatch_rows)
    )
    assert summary["false_accept_count"] == sum(1 for row in rows if row["false_accept"])
    assert summary["false_reject_count"] == sum(1 for row in rows if row["false_reject"])


def test_benchmark_does_not_pollute_real_project_workspace(benchmark_run):
    del benchmark_run

    assert not (REPO_ROOT / "bulk").exists()
    assert not (REPO_ROOT / "changed_0.txt").exists()
    assert not (REPO_ROOT / "checkpoint_resume_fixture.txt").exists()


def test_benchmark_results_are_not_hardcoded(tmp_path, benchmark_module):
    case = next(case for case in benchmark_module.load_cases(CASES_PATH) if case["case_id"] == "clean_resume")
    custom_case = dict(case)
    custom_case["case_id"] = "clean_resume_custom"
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
    assert artifact["cases"][0]["case_id"] == "clean_resume_custom"
    assert rows[0]["case_id"] == "clean_resume_custom"


def test_summary_documents_required_limitations(benchmark_run):
    summary_text = (benchmark_run["root"] / "summary.md").read_text(encoding="utf-8")

    assert "not production-grade transactional recovery" in summary_text
    assert "cannot guarantee that all file semantics are unchanged" in summary_text
    assert "current project's metadata and workspace state" in summary_text
    assert "not equivalent to distributed task recovery" in summary_text
