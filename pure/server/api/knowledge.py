from fastapi import APIRouter

from pure.server.schemas import (
    KnowledgeDocumentRequest,
    KnowledgeIndexRequest,
    KnowledgeIndexResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from pure.server.state import runtime_service

router = APIRouter()


@router.post("/knowledge/documents", response_model=KnowledgeIndexResponse)
def add_knowledge_documents(request: KnowledgeDocumentRequest):
    return runtime_service.add_knowledge_documents(request.project_id, request.paths)


@router.post("/knowledge/index", response_model=KnowledgeIndexResponse)
def index_knowledge(request: KnowledgeIndexRequest):
    return runtime_service.index_knowledge(request.project_id, request.paths or None, reset=request.reset)


@router.post("/knowledge/search", response_model=KnowledgeSearchResponse)
def search_knowledge(request: KnowledgeSearchRequest):
    return runtime_service.search_knowledge(
        request.project_id,
        query=request.query,
        top_k=request.top_k,
        budget_chars=request.budget_chars,
    )
