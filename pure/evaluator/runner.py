from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .cases import load_eval_cases
from .metrics import aggregate_metrics, calculate_case_metrics, infer_latency_ms
from .report import build_eval_report, write_eval_report


class EvaluatorRunner:
    def __init__(
        self,
        *,
        service,
        project_path: str | Path,
        cases_path: str | Path = "eval_cases.json",
        runtime_config: dict | None = None,
        dry_run: bool = True,
        eval_id: str | None = None,
    ):
        self.service = service
        self.project_path = Path(project_path).resolve()
        self.cases_path = Path(cases_path).resolve()
        self.runtime_config = dict(runtime_config or {})
        self.dry_run = bool(dry_run)
        self.eval_id = eval_id or f"eval_{uuid4().hex[:12]}"

    def run(self):
        cases = load_eval_cases(self.cases_path)
        project = self.service.create_project(
            name=f"eval-{self.eval_id}",
            root_path=str(self.project_path),
            description="Evaluator run project",
        )
        rows = [self._run_case(project["id"], case) for case in cases]
        summary = aggregate_metrics(rows)
        report = build_eval_report(
            eval_id=self.eval_id,
            project_path=str(self.project_path),
            cases_path=str(self.cases_path),
            dry_run=self.dry_run,
            rows=rows,
            summary=summary,
        )
        report_path = write_eval_report(report, self.project_path / ".pure" / "evals" / self.eval_id)
        return {"eval_id": self.eval_id, "report_path": str(report_path), "report": report}

    def _run_case(self, project_id: str, case):
        runtime_config = dict(self.runtime_config)
        runtime_config.update(dict(getattr(case, "runtime_config", {}) or {}))
        runtime_config["max_steps"] = int(case.max_steps)
        if getattr(case, "mock_outputs", None):
            runtime_config["mock_outputs"] = list(case.mock_outputs)
        task = self.service.create_task(
            project_id=project_id,
            title=f"eval:{case.id}",
            prompt=case.task,
            priority=0,
            runtime_config=runtime_config,
            dry_run=self.dry_run,
        )
        started = self.service.start_task(
            task_id=task["id"],
            runtime_config=runtime_config,
            dry_run=self.dry_run,
            dispatch=False,
        )
        job = self.service.task_jobs[task["id"]]
        error = ""
        try:
            self.service._run_task_job(task["id"], job.session_id, job.prompt, job.dry_run, job.run_id)
        except Exception as exc:
            error = str(exc)
        events = []
        report = {}
        try:
            events = self.service.get_trace(started["run_id"])["events"]
        except Exception:
            events = []
        try:
            report = self.service.get_report(started["run_id"])
        except Exception:
            report = {"status": "failed", "final_answer": "", "tool_steps": 0, "error": error}
        latency_ms = infer_latency_ms(events)
        metrics = calculate_case_metrics(case, report, events, latency_ms)
        failure_reasons = list(metrics.get("failure_reasons", []))
        if error:
            failure_reasons.append(f"runner error: {error}")
            metrics["failure_reasons"] = failure_reasons
            metrics["case_passed"] = False
        return {
            "id": case.id,
            "task_id": task["id"],
            "run_id": started["run_id"],
            "status": report.get("status", "failed"),
            "error": error,
            "case_passed": bool(metrics.get("case_passed")),
            "failure_reasons": failure_reasons,
            "expected_tools": list(case.expected_tools),
            "forbidden_tools": list(case.forbidden_tools),
            "success_keywords": list(case.success_keywords),
            "max_steps": case.max_steps,
            "expected_trace_events": list(case.expected_trace_events),
            "final_answer": report.get("final_answer", ""),
            "metrics": metrics,
        }
