"""Nox Agent Runtime - FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes.financial import router as financial_router
from api.routes.health import router as health_router
from api.routes.jobs import router as jobs_router
from api.routes.operational import router as operational_router
from api.routes.physical import router as physical_router
from api.routes.recipes import router as recipes_router
from api.routes.system import router as system_router
from api.ui.routes import router as ui_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Nox Agent Runtime",
    description="Agent-as-a-service platform for multi-step Claude-powered workflows.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(physical_router)
app.include_router(recipes_router)
app.include_router(financial_router)
app.include_router(operational_router)
app.include_router(system_router)
app.include_router(ui_router)
