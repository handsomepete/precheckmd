"""Database session factories."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.config import settings


# Async engine used by the FastAPI app
async_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(
    async_engine, expire_on_commit=False, class_=AsyncSession
)

# Sync engine used by RQ workers (which are not async)
def _sync_url(url: str) -> str:
    """Convert asyncpg URL to psycopg2 URL for sync usage."""
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


sync_engine = create_engine(_sync_url(settings.database_url), pool_pre_ping=True)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False)
