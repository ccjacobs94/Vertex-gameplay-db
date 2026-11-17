"""Authentication, one-time local bootstrap, and single-use invite acceptance routers."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, Response

from vertex.interfaces.http.deps import AppServices, get_services, resolve_client_ip
from vertex.interfaces.http.schemas import (
    AcceptInviteRequest,
    AuthCredentialsRequest,
    AuthResponse,
    BootstrapStatusResponse,
    UserAccountResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, services: AppServices, token: str) -> None:
    response.set_cookie(
        key=services.settings.cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=services.settings.cookie_secure,
        max_age=services.settings.session_ttl_seconds,
        path="/",
    )


@router.get("/bootstrap", response_model=BootstrapStatusResponse)
def bootstrap_status(services: AppServices = Depends(get_services)) -> BootstrapStatusResponse:
    return BootstrapStatusResponse(bootstrap_open=services.identity.is_bootstrap_open())


@router.post("/bootstrap", response_model=AuthResponse, status_code=201)
def bootstrap_admin(
    body: AuthCredentialsRequest,
    request: Request,
    response: Response,
    services: AppServices = Depends(get_services),
) -> AuthResponse:
    account, token = services.identity.bootstrap_admin(
        username=body.username,
        password=body.password,
        user_agent=request.headers.get("user-agent", ""),
    )
    _set_session_cookie(response, services, token)
    return AuthResponse(account=UserAccountResponse(**asdict(account)))


@router.post("/login", response_model=AuthResponse)
def login(
    body: AuthCredentialsRequest,
    request: Request,
    response: Response,
    services: AppServices = Depends(get_services),
) -> AuthResponse:
    scope, token = services.identity.login(
        username=body.username,
        password=body.password,
        client_ip=resolve_client_ip(request, services.settings),
        user_agent=request.headers.get("user-agent", ""),
    )
    account = services.identity.get_account(scope)
    _set_session_cookie(response, services, token)
    return AuthResponse(account=UserAccountResponse(**asdict(account)))


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    services: AppServices = Depends(get_services),
) -> Response:
    token = request.cookies.get(services.settings.cookie_name)
    services.identity.logout_session(token)
    response.delete_cookie(
        services.settings.cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=services.settings.cookie_secure,
    )
    response.headers["Clear-Site-Data"] = '"cache"'
    response.status_code = 204
    return response


@router.post("/invites/accept", response_model=AuthResponse, status_code=201)
def accept_invite(
    body: AcceptInviteRequest,
    request: Request,
    response: Response,
    services: AppServices = Depends(get_services),
) -> AuthResponse:
    account, token = services.identity.accept_invite(
        invite_code=body.invite_code,
        username=body.username,
        password=body.password,
        user_agent=request.headers.get("user-agent", ""),
    )
    _set_session_cookie(response, services, token)
    return AuthResponse(account=UserAccountResponse(**asdict(account)))
