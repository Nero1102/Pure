import asyncio

from fastapi import APIRouter

from pure.server.schemas import (
    CheckpointListResponse,
    TaskCancelResponse,
    TaskCreateRequest,
    TaskResponse,
    TaskResumeRequest,
    TaskRunRequest,
    TaskRunResponse,
    TaskStatusResponse,
)
from pure.server.state import runtime_service

router = APIRouter()


@router.post("/tasks", response_model=TaskResponse)
def create_task(request: TaskCreateRequest):
    return runtime_service.create_task(
        project_id=request.project_id,
        title=request.title,
        prompt=request.prompt,
        priority=request.priority,
        runtime_config=request.runtime_config,
        dry_run=request.dry_run,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str):
    return runtime_service.get_task(task_id)


@router.post("/tasks/{task_id}/run", response_model=TaskRunResponse)
async def run_task(task_id: str, request: TaskRunRequest):
    response = runtime_service.start_task(
        task_id=task_id,
        runtime_config=request.runtime_config,
        dry_run=request.dry_run,
        dispatch=False,
    )
    runtime_service.dispatch_task_asyncio(task_id, asyncio.get_running_loop())
    return response


@router.get("/tasks/{task_id}/status", response_model=TaskStatusResponse)
def get_task_status(task_id: str):
    return runtime_service.get_task_status(task_id)


@router.post("/tasks/{task_id}/cancel", response_model=TaskCancelResponse)
def cancel_task(task_id: str):
    return runtime_service.cancel_task(task_id)


@router.get("/tasks/{task_id}/checkpoints", response_model=CheckpointListResponse)
def list_task_checkpoints(task_id: str):
    return runtime_service.list_task_checkpoints(task_id)


@router.post("/tasks/{task_id}/resume", response_model=TaskRunResponse)
async def resume_task(task_id: str, request: TaskResumeRequest):
    response = runtime_service.resume_task(
        task_id=task_id,
        checkpoint_id=request.checkpoint_id,
        runtime_config=request.runtime_config,
        dry_run=request.dry_run,
        dispatch=False,
    )
    runtime_service.dispatch_task_asyncio(task_id, asyncio.get_running_loop())
    return response
