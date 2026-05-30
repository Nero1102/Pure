from fastapi import APIRouter

from pure.server.schemas import ToolResponse
from pure.server.state import runtime_service

router = APIRouter()


@router.get("/tools", response_model=list[ToolResponse], response_model_by_alias=True)
def list_tools():
    return runtime_service.list_tools()
