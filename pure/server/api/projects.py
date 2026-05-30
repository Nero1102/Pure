from fastapi import APIRouter

from pure.server.schemas import ProjectCreateRequest, ProjectResponse
from pure.server.state import runtime_service

router = APIRouter()


@router.post("/projects", response_model=ProjectResponse)
def create_project(request: ProjectCreateRequest):
    return runtime_service.create_project(
        name=request.name,
        root_path=request.root_path,
        description=request.description,
    )


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str):
    return runtime_service.get_project(project_id)

