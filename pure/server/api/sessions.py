from fastapi import APIRouter

from pure.server.schemas import AskRequest, AskResponse, SessionCreateRequest, SessionCreateResponse, SessionResponse
from pure.server.state import runtime_service

router = APIRouter()


@router.post("/sessions", response_model=SessionCreateResponse)
def create_session(request: SessionCreateRequest):
    return runtime_service.create_session(
        project_path=request.project_path,
        runtime_config=request.runtime_config,
        dry_run=request.dry_run,
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str):
    return runtime_service.get_session(session_id)


@router.post("/sessions/{session_id}/ask", response_model=AskResponse)
def ask_session(session_id: str, request: AskRequest):
    return runtime_service.run_task(session_id=session_id, prompt=request.prompt, dry_run=request.dry_run)

