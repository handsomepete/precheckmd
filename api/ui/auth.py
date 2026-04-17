"""Cookie-based API-key auth for UI routes.

The UI reuses the single API key. A successful /ui/login sets an HttpOnly
cookie signed with the API key itself; UI endpoints enforce the cookie via
``require_ui_session``. Non-UI /api/* routes continue to require the
``X-API-Key`` header as before.
"""

from __future__ import annotations

from fastapi import Cookie, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from api.config import settings

COOKIE_NAME = "homeos_session"


def is_authenticated(session_cookie: str | None) -> bool:
    return bool(session_cookie) and session_cookie == settings.api_key


async def require_ui_session(
    request: Request,
    homeos_session: str | None = Cookie(default=None),
) -> str:
    if is_authenticated(homeos_session):
        return homeos_session  # type: ignore[return-value]
    # For HTMX partial requests, 401 so the client can handle re-auth.
    if request.headers.get("HX-Request") == "true":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired",
        )
    # Regular browser nav: bounce to login with a return path.
    target = request.url.path
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        detail="login required",
        headers={"Location": f"/ui/login?next={target}"},
    )


def set_session_cookie(response: RedirectResponse, api_key: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=api_key,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 days
        path="/",
    )


def clear_session_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")
