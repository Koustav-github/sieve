from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.config import settings
from app.departments.admin_api import router as departments_router

app = FastAPI(title="Sieve API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(departments_router)
