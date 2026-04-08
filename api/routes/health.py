"""Health check endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    """Return service liveness and database reachability."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}


@router.get("/")
async def root() -> dict:
    return {"service": "nox-agent-runtime", "version": "0.1.0"}
