from fastapi import APIRouter

from pure.server.state import runtime_service
from pure.server.schemas import RunReportResponse, RunResponse, TraceResponse

router = APIRouter()


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: str):
    return runtime_service.get_run(run_id)


@router.get("/runs/{run_id}/trace", response_model=TraceResponse)
def get_run_trace(run_id: str):
    return runtime_service.get_trace(run_id)


@router.get("/runs/{run_id}/report", response_model=RunReportResponse)
def get_run_report(run_id: str):
    return runtime_service.get_report(run_id)
