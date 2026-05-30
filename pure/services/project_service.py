from pathlib import Path

from fastapi import HTTPException

from pure.core.workspace import WorkspaceContext
from pure.db.repositories import ProjectRepository


class ProjectService:
    def __init__(self, db_getter):
        self._db = db_getter

    def create_project(self, name: str, root_path: str, description: str = ""):
        workspace = WorkspaceContext.build(root_path)
        with self._db().session() as db:
            project = ProjectRepository(db).create(
                name=name,
                root_path=workspace.repo_root,
                description=description,
            )
            return self._project_payload(project)

    def get_project(self, project_id: str):
        with self._db().session() as db:
            project = ProjectRepository(db).get(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            return self._project_payload(project)

    @staticmethod
    def _project_payload(project):
        return {
            "id": project.id,
            "name": project.name,
            "root_path": project.root_path,
            "description": project.description,
            "created_at": _dt(project.created_at),
            "updated_at": _dt(project.updated_at),
        }


def _dt(value):
    return value.isoformat() if value else None
