import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path("benchmarks/pure/tool_governance/run_tool_governance_benchmark.py").resolve()
CASES_PATH = Path("benchmarks/pure/tool_governance/cases.json").resolve()
REPO_ROOT = Path.cwd()


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("tool_governance_benchmark", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def benchmark_module():
    return _load_benchmark_module()


@pytest.fixture(scope="module")
def benchmark_run(tmp_path_factory, benchmark_module):
    root = tmp_path_factory.mktemp("tool_governance_benchmark")
    artifact = benchmark_module.run_benchmark(
        cases_path=CASES_PATH,
        results_path=root / "results.json",
        per_case_path=root / "per_case_results.jsonl",
        summary_path=root / "summary.md",
        workspace_root=root / "workspaces",
    )
    rows = {row["case_id"]: row for row in artifact["cases"]}
    return {"root": root, "artifact": artifact, "rows": rows}


def test_tool_governance_cases_include_required_minimum_cases(benchmark_module):
    cases = benchmark_module.load_cases(CASES_PATH)

    case_ids = {case["case_id"] for case in cases}
    assert len(cases) >= 15
    assert {
        "safe_read_allowed",
        "safe_list_allowed",
        "safe_search_allowed",
        "readonly_blocks_write",
        "readonly_blocks_patch",
        "readonly_blocks_shell",
        "workspace_escape_read_parent",
        "workspace_escape_write_parent",
        "workspace_escape_absolute_path",
        "invalid_tool_name",
        "missing_required_arg",
        "wrong_arg_type",
        "risky_write_audit_diff",
        "risky_patch_audit_diff",
        "shell_no_workspace_change",
    } <= case_ids


def test_readonly_allows_safe_read_list_search(benchmark_run):
    rows = benchmark_run["rows"]

    for case_id in ("safe_read_allowed", "safe_list_allowed", "safe_search_allowed"):
        row = rows[case_id]
        assert row["actual_allowed"] is True
        assert row["tool_status"] == "ok"
        assert row["approval_mode"] == "readonly"
        assert row["passed"] is True


def test_readonly_blocks_write_patch_and_shell(benchmark_run):
    rows = benchmark_run["rows"]

    for case_id in ("readonly_blocks_write", "readonly_blocks_patch", "readonly_blocks_shell"):
        row = rows[case_id]
        assert row["actual_allowed"] is False
        assert row["tool_status"] == "rejected"
        assert row["tool_error_code"] == "readonly_block"
        assert row["security_event_type"] == "read_only_block"
        assert row["passed"] is True


def test_parent_path_escape_is_rejected(benchmark_run):
    rows = benchmark_run["rows"]

    for case_id in ("workspace_escape_read_parent", "workspace_escape_write_parent"):
        row = rows[case_id]
        assert row["actual_allowed"] is False
        assert row["tool_error_code"] == "invalid_arguments"
        assert row["security_event_type"] == "path_escape"
        assert row["path_within_workspace"] is False
        assert row["passed"] is True


def test_absolute_path_escape_is_rejected(benchmark_run):
    row = benchmark_run["rows"]["workspace_escape_absolute_path"]

    assert row["actual_allowed"] is False
    assert row["tool_error_code"] == "invalid_arguments"
    assert row["security_event_type"] == "path_escape"
    assert row["path_within_workspace"] is False
    assert row["passed"] is True


def test_invalid_tool_name_is_rejected(benchmark_run):
    row = benchmark_run["rows"]["invalid_tool_name"]

    assert row["actual_allowed"] is False
    assert row["tool_error_code"] == "unknown_tool"
    assert row["approval_decision"] == "denied_unknown_tool"
    assert row["passed"] is True


def test_missing_arg_is_rejected(benchmark_run):
    row = benchmark_run["rows"]["missing_required_arg"]

    assert row["actual_allowed"] is False
    assert row["tool_error_code"] == "invalid_arguments"
    assert row["args_valid"] is False
    assert row["passed"] is True


def test_wrong_arg_type_is_rejected(benchmark_run):
    row = benchmark_run["rows"]["wrong_arg_type"]

    assert row["actual_allowed"] is False
    assert row["tool_error_code"] == "invalid_arguments"
    assert row["args_valid"] is False
    assert row["passed"] is True


def test_risky_write_records_affected_paths(benchmark_run):
    row = benchmark_run["rows"]["risky_write_audit_diff"]

    assert row["actual_allowed"] is True
    assert row["workspace_changed"] is True
    assert row["affected_paths"] == ["created.txt"]
    assert row["audit_fields_present"] is True
    assert row["passed"] is True


def test_risky_patch_records_workspace_changed(benchmark_run):
    row = benchmark_run["rows"]["risky_patch_audit_diff"]

    assert row["actual_allowed"] is True
    assert row["workspace_changed"] is True
    assert row["affected_paths"] == ["patch_target.txt"]
    assert row["audit_fields_present"] is True
    assert row["passed"] is True


def test_summary_metrics_are_derived_from_rows(benchmark_run):
    artifact = benchmark_run["artifact"]
    rows = artifact["cases"]
    summary = artifact["summary"]

    assert summary["case_count"] == len(rows)
    assert summary["policy_pass_rate"] == pytest.approx(sum(1 for row in rows if row["passed"]) / len(rows))

    unsafe = [row for row in rows if not row["expected_allowed"]]
    assert summary["unsafe_rejection_rate"] == pytest.approx(
        sum(1 for row in unsafe if not row["actual_allowed"]) / len(unsafe)
    )

    workspace_escape = [row for row in rows if row["category"] == "workspace_escape"]
    assert summary["workspace_escape_block_rate"] == pytest.approx(
        sum(1 for row in workspace_escape if row["security_event_type"] == "path_escape" and not row["actual_allowed"])
        / len(workspace_escape)
    )

    assert summary["false_allow_count"] == sum(
        1 for row in rows if not row["expected_allowed"] and row["actual_allowed"]
    )
    assert summary["false_reject_count"] == sum(
        1 for row in rows if row["expected_allowed"] and not row["actual_allowed"]
    )


def test_benchmark_does_not_pollute_real_project_workspace(benchmark_run):
    del benchmark_run

    assert not (REPO_ROOT / "created.txt").exists()
    assert not (REPO_ROOT / "patch_target.txt").exists()
    assert not (REPO_ROOT / "outside.txt").exists()


def test_benchmark_results_are_not_hardcoded(tmp_path, benchmark_module):
    case = next(case for case in benchmark_module.load_cases(CASES_PATH) if case["case_id"] == "safe_read_allowed")
    custom_case = dict(case)
    custom_case["case_id"] = "safe_read_allowed_custom"
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
    assert artifact["cases"][0]["case_id"] == "safe_read_allowed_custom"
    assert rows[0]["case_id"] == "safe_read_allowed_custom"


def test_summary_documents_required_limitations(benchmark_run):
    summary_text = (benchmark_run["root"] / "summary.md").read_text(encoding="utf-8")

    assert "not a production-grade sandbox" in summary_text
    assert "cannot defend against every command risk" in summary_text
    assert "local workspace boundary checks and policy governance" in summary_text
    assert "does not represent an enterprise security system" in summary_text
