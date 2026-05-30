import json

from fastapi.testclient import TestClient

from pure.evaluator.cases import load_eval_cases
from pure.evaluator.runner import EvaluatorRunner
from pure.server.main import app
from pure.server.state import RuntimeService, runtime_service


def _write_cases(path):
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "case_one",
                        "task": "Inspect without a real model.",
                        "expected_tools": [],
                        "forbidden_tools": ["run_shell", "write_file", "patch_file"],
                        "success_keywords": ["Dry run"],
                        "max_steps": 2,
                        "expected_trace_events": ["run_started", "knowledge_retrieved", "run_completed"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_eval_cases_schema_loader(tmp_path):
    cases_path = tmp_path / "eval_cases.json"
    _write_cases(cases_path)

    cases = load_eval_cases(cases_path)

    assert len(cases) == 1
    assert cases[0].id == "case_one"
    assert cases[0].max_steps == 2
    assert cases[0].expected_trace_events == ["run_started", "knowledge_retrieved", "run_completed"]


def test_evaluator_runner_dry_run_writes_report(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    cases_path = tmp_path / "eval_cases.json"
    _write_cases(cases_path)
    service = RuntimeService(database_url=f"sqlite:///{tmp_path / 'pure.db'}")

    result = EvaluatorRunner(
        service=service,
        project_path=tmp_path,
        cases_path=cases_path,
        dry_run=True,
    ).run()

    report_path = tmp_path / ".pure" / "evals" / result["eval_id"] / "report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["dry_run"] is True
    assert report["summary"]["case_count"] == 1
    assert report["summary"]["case_pass_rate"] == 1.0
    assert report["summary"]["task_success"] == 1.0
    assert report["summary"]["forbidden_tool_count"] == 0
    assert report["summary"]["trace_event_success"] == 1.0
    assert report["rows"][0]["metrics"]["checkpoint_created"] is True
    assert report["rows"][0]["failure_reasons"] == []


def test_eval_api_run_and_report_dry_run(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    runtime_service.configure_database(f"sqlite:///{tmp_path / 'pure.db'}")
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    cases_path = tmp_path / "eval_cases.json"
    _write_cases(cases_path)
    client = TestClient(app)

    response = client.post(
        "/eval/run",
        json={"project_path": str(tmp_path), "cases_path": str(cases_path), "dry_run": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["summary"]["case_count"] == 1

    report_response = client.get(f"/eval/{payload['eval_id']}/report")
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["eval_id"] == payload["eval_id"]
    assert report["dry_run"] is True


def test_evaluator_checks_expected_trace_events_and_failure_reasons(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    cases_path = tmp_path / "eval_cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "missing_trace",
                        "task": "Finish without tools.",
                        "expected_tools": [],
                        "forbidden_tools": [],
                        "success_keywords": ["Dry run"],
                        "max_steps": 2,
                        "expected_trace_events": ["tool_executed"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    service = RuntimeService(database_url=f"sqlite:///{tmp_path / 'pure.db'}")

    result = EvaluatorRunner(
        service=service,
        project_path=tmp_path,
        cases_path=cases_path,
        dry_run=True,
    ).run()

    row = result["report"]["rows"][0]
    assert row["case_passed"] is False
    assert row["metrics"]["trace_event_success"] is False
    assert row["metrics"]["missing_expected_trace_events"] == ["tool_executed"]
    assert row["failure_reasons"] == ["missing expected trace events: tool_executed"]
    assert result["report"]["summary"]["case_pass_rate"] == 0.0


def test_evaluator_dry_run_can_use_case_mock_outputs_without_real_model(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    cases_path = tmp_path / "eval_cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "mock_read",
                        "task": "Read README with a scripted fake model.",
                        "expected_tools": ["read_file"],
                        "forbidden_tools": ["run_shell", "write_file"],
                        "success_keywords": ["Mock read"],
                        "max_steps": 3,
                        "expected_trace_events": [
                            {
                                "event_type": "tool_executed",
                                "payload": {"name": "read_file", "tool_status": "ok"},
                            },
                            {"event_type": "checkpoint_created", "payload": {"trigger": "tool_executed"}},
                            "run_completed",
                        ],
                        "mock_outputs": [
                            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
                            "<final>Mock read completed without a real model.</final>",
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    service = RuntimeService(database_url=f"sqlite:///{tmp_path / 'pure.db'}")

    result = EvaluatorRunner(
        service=service,
        project_path=tmp_path,
        cases_path=cases_path,
        dry_run=True,
    ).run()

    row = result["report"]["rows"][0]
    assert row["case_passed"] is True
    assert row["failure_reasons"] == []
    assert row["metrics"]["tools_used"] == ["read_file"]
    assert row["metrics"]["expected_trace_event_hit_rate"] == 1.0
    assert result["report"]["dry_run"] is True
