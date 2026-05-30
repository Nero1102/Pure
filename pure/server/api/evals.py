from fastapi import APIRouter

from pure.server.schemas import EvalReportResponse, EvalRunRequest, EvalRunResponse
from pure.server.state import runtime_service

router = APIRouter()


@router.post("/eval/run", response_model=EvalRunResponse)
def run_eval(request: EvalRunRequest):
    return runtime_service.run_evaluation(
        project_path=request.project_path,
        cases_path=request.cases_path,
        runtime_config=request.runtime_config,
        dry_run=request.dry_run,
    )


@router.get("/eval/{eval_id}/report", response_model=EvalReportResponse)
def get_eval_report(eval_id: str):
    return runtime_service.get_eval_report(eval_id)
