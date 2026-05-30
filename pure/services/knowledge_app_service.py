from fastapi import HTTPException

from pure.db.repositories import ProjectRepository
from pure.knowledge import KnowledgeService


class KnowledgeAppService:
    def __init__(self, db_getter):
        self._db = db_getter

    def add_knowledge_documents(self, project_id: str, paths: list[str]):
        service = self._knowledge_service_for_project(project_id)
        result = service.add_paths(paths)
        return {"project_id": project_id, **result}

    def index_knowledge(self, project_id: str, paths: list[str] | None = None, reset: bool = True):
        service = self._knowledge_service_for_project(project_id)
        result = service.index_project(paths=paths, reset=reset)
        return {"project_id": project_id, **result}

    def search_knowledge(self, project_id: str, query: str, top_k: int = 5, budget_chars: int = 1400):
        service = self._knowledge_service_for_project(project_id)
        results = service.retrieve(query, top_k=top_k)
        clipped = []
        remaining = max(0, int(budget_chars))
        for result in results:
            content = result.content
            if remaining <= 0:
                content = ""
            elif len(content) > remaining:
                content = content[: max(0, remaining - 3)] + "..."
            remaining -= len(content)
            clipped.append(
                {
                    "content": content,
                    "source": result.source,
                    "score": result.score,
                    "metadata": result.metadata,
                }
            )
        return {"project_id": project_id, "results": clipped}

    def _knowledge_service_for_project(self, project_id: str):
        with self._db().session() as db:
            project = ProjectRepository(db).get(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            return KnowledgeService(project.root_path)
