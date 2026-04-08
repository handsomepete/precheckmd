"""API key authentication dependency."""

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from api.config import settings

_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str | None = Security(_header_scheme)) -> str:
    """Validate the X-API-Key header. Returns the key on success."""
    if not api_key or api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key
