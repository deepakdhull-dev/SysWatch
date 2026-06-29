from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ...db import queries
from ..auth import ACCESS_COOKIE, REFRESH_COOKIE
from ..deps import get_ca, get_db_pool, get_servicer, require_auth_api

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class ProvisionRequest(BaseModel):
    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9\-_]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$",
    )
    services: list[str] = Field(default_factory=list)
    server_host: str = Field(
        default="localhost",
        description="Hostname or IP the agent should connect to via gRPC.",
    )


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request):
    auth = request.app.state.auth

    if not auth.verify_login(body.username, body.password):
        logger.warning(
            "Failed login: username=%r from %s",
            body.username,
            request.client.host if request.client else "?",
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    response = JSONResponse({"ok": True})
    auth.set_auth_cookies(response)
    logger.info(
        "Admin logged in from %s", request.client.host if request.client else "?"
    )
    return response


@router.post("/auth/logout")
async def logout(request: Request):
    response = JSONResponse({"ok": True})
    request.app.state.auth.delete_auth_cookies(response)
    logger.info("Admin logged out")
    return response


@router.get("/me")
async def me(request: Request):
    auth = request.app.state.auth

    access_token = request.cookies.get(ACCESS_COOKIE)
    refresh_token = request.cookies.get(REFRESH_COOKIE)

    if access_token and auth.verify_token(access_token, "access"):
        return {"authenticated": True}

    if refresh_token and auth.verify_token(refresh_token, "refresh"):
        response = JSONResponse({"authenticated": True})
        auth.set_auth_cookies(response)
        return response

    return JSONResponse({"authenticated": False}, status_code=401)


@router.get("/config")
async def get_config(request: Request, _=Depends(require_auth_api)):
    cfg = request.app.state.cfg
    grafana = getattr(cfg, "grafana", None)
    grpc_cfg = getattr(cfg, "grpc", None)

    grafana_url = None
    if grafana:
        uid = getattr(grafana, "dashboard_uid", "syswatch-main")
        url = getattr(grafana, "url", "http://localhost:3000")
        grafana_url = f"{url}/d/{uid}?orgId=1&kiosk&theme=dark&from=now-1h&to=now"

    return {
        "grafana_url": grafana_url,
        "default_server_host": getattr(grpc_cfg, "host", "localhost"),
        "grpc_port": getattr(grpc_cfg, "port", 50051),
    }


@router.get("/health")
async def health():
    return {"status": "ok", "service": "syswatch-server"}


@router.get("/agents")
async def list_agents(
    request: Request,
    db_pool=Depends(get_db_pool),
    servicer=Depends(get_servicer),
    _=Depends(require_auth_api),
):
    agents = await queries.get_all_agents(db_pool)
    connected: set[str] = set(servicer.connected_agents().keys()) if servicer else set()
    for agent in agents:
        agent["online"] = agent["agent_id"] in connected
    return agents


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    db_pool=Depends(get_db_pool),
    _=Depends(require_auth_api),
):
    agent = await queries.get_agent(db_pool, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
    return agent


@router.get("/agents/{agent_id}/metrics")
async def agent_metrics(
    agent_id: str,
    hours: int = 24,
    bucket_minutes: int = 5,
    db_pool=Depends(get_db_pool),
    _=Depends(require_auth_api),
):
    hours = min(hours, 168)
    return await queries.get_agent_metrics(
        db_pool, agent_id, hours=hours, bucket_minutes=bucket_minutes
    )


@router.get("/agents/{agent_id}/disks")
async def agent_disks(
    agent_id: str,
    hours: int = 1,
    bucket_minutes: int = 5,
    db_pool=Depends(get_db_pool),
    _=Depends(require_auth_api),
):
    return await queries.get_agent_disks(
        db_pool, agent_id, hours=hours, bucket_minutes=bucket_minutes
    )


@router.get("/agents/{agent_id}/network")
async def agent_network(
    agent_id: str,
    hours: int = 1,
    bucket_minutes: int = 5,
    db_pool=Depends(get_db_pool),
    _=Depends(require_auth_api),
):
    return await queries.get_agent_network(
        db_pool, agent_id, hours=hours, bucket_minutes=bucket_minutes
    )


@router.get("/agents/{agent_id}/services")
async def agent_services(
    agent_id: str,
    db_pool=Depends(get_db_pool),
    _=Depends(require_auth_api),
):
    return await queries.get_agent_services_latest(db_pool, agent_id)


@router.get("/dashboard/summary")
async def dashboard_summary(
    db_pool=Depends(get_db_pool),
    _=Depends(require_auth_api),
):
    return await queries.get_dashboard_summary(db_pool)


@router.post("/agents/provision")
async def provision_agent(
    body: ProvisionRequest,
    request: Request,
    ca=Depends(get_ca),
    _=Depends(require_auth_api),
):
    if ca is None:
        raise HTTPException(status_code=503, detail="CA not available.")

    cfg = request.app.state.cfg
    grpc_cfg = getattr(cfg, "grpc", None)

    logger.info(
        "Provisioning agent_id=%r server_host=%r services=%s",
        body.agent_id,
        body.server_host,
        body.services,
    )

    try:
        bundle_bytes = ca.build_bundle(
            agent_id=body.agent_id,
            server_host=body.server_host,
            server_port=getattr(grpc_cfg, "port", 50051),
            services=body.services,
        )
    except Exception as exc:
        logger.error("Bundle generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return Response(
        content=bundle_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{body.agent_id}_bundle.zip"'
        },
    )
