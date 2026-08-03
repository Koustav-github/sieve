from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str


@router.get("/health")
def get_health() -> HealthResponse:
    return HealthResponse(status="ok")
