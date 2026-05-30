import json
from pathlib import Path

from fastapi import HTTPException

from pure.evaluator.runner import EvaluatorRunner


class EvaluatorAppService:
    def __init__(self, runtime_service, eval_reports: dict):
        self._runtime = runtime_service
        self.eval_reports = eval_reports

    def run_evaluation(
        self,
        project_path: str,
        cases_path: str = "eval_cases.json",
        runtime_config: dict | None = None,
        dry_run: bool = True,
    ):
        runner = EvaluatorRunner(
            service=self._runtime,
            project_path=project_path,
            cases_path=cases_path,
            runtime_config=runtime_config,
            dry_run=dry_run,
        )
        result = runner.run()
        self.eval_reports[result["eval_id"]] = result["report_path"]
        return {
            "eval_id": result["eval_id"],
            "status": "completed",
            "report_path": result["report_path"],
            "summary": result["report"]["summary"],
        }

    def get_eval_report(self, eval_id: str):
        path_text = self.eval_reports.get(eval_id)
        if not path_text:
            raise HTTPException(status_code=404, detail="eval report not found")
        path = Path(path_text)
        if not path.exists():
            raise HTTPException(status_code=404, detail="eval report not found")
        return json.loads(path.read_text(encoding="utf-8"))
