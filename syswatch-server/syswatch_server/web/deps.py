from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException, Request

from .auth import ACCESS_COOKIE, REFRESH_COOKIE, AuthManager

logger = logging.getLogger(__name__)


async def get_db_pool(request: Request) -> Any:
    return request.app.state.db_pool


async def get_ca(request: Request) -> Any:
    return request.app.state.ca


async def get_servicer(request: Request) -> Any:
    return request.app.state.servicer


async def get_auth(request: Request) -> AuthManager:
    return request.app.state.auth


async def require_auth_api(
    request: Request,
    auth: AuthManager = Depends(get_auth),
) -> None:
    access_token = request.cookies.get(ACCESS_COOKIE)

    if access_token and auth.verify_token(access_token, "access"):
        return

    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if refresh_token and auth.verify_token(refresh_token, "refresh"):
        logger.debug("Access token expired but refresh token valid — allowing request")
        return

    logger.debug(
        "require_auth_api: no valid token for %s %s",
        request.method,
        request.url.path,
    )
    raise HTTPException(status_code=401, detail="Not authenticated")


def check_auth_cookie(request: Request) -> bool:
    auth: AuthManager = request.app.state.auth

    access_token = request.cookies.get(ACCESS_COOKIE)
    if access_token and auth.verify_token(access_token, "access"):
        return True

    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if refresh_token and auth.verify_token(refresh_token, "refresh"):
        return True

    return False


def needs_token_refresh(request: Request) -> bool:
    auth: AuthManager = request.app.state.auth
    access_token = request.cookies.get(ACCESS_COOKIE)

    if access_token and auth.verify_token(access_token, "access"):
        return False

    refresh_token = request.cookies.get(REFRESH_COOKIE)
    return bool(refresh_token and auth.verify_token(refresh_token, "refresh"))
