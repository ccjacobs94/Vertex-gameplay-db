"""Personal account, password change, timezone, and scoped PAT management."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_read_scope,
    require_session_scope,
)
from vertex.interfaces.http.schemas import (
    CreatedPatResponse,
    CreatePatRequest,
    PasswordChangeRequest,
    PatResponse,
    TimezoneUpdateRequest,
    UserAccountResponse,
)

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("", response_model=UserAccountResponse)
def get_account(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> UserAccountResponse:
    return UserAccountResponse(**asdict(services.identity.get_account(scope)))


@router.post("/onboarding/complete", response_model=UserAccountResponse)
def complete_onboarding(
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> UserAccountResponse:
    return UserAccountResponse(**asdict(services.identity.complete_onboarding(scope)))


@router.patch("/timezone", response_model=UserAccountResponse)
def update_timezone(
    body: TimezoneUpdateRequest,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> UserAccountResponse:
    account = services.identity.update_timezone(
        scope,
        timezone_name=body.timezone,
        timezone_eras=body.timezone_eras,
    )
    return UserAccountResponse(**asdict(account))


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.identity.change_own_password(
        scope,
        current_password=body.current_password,
        new_password=body.new_password,
        current_session_token=request.cookies.get(services.settings.cookie_name),
    )


@router.get("/tokens", response_model=list[PatResponse])
def list_tokens(
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> list[PatResponse]:
    return [
        PatResponse(
            id=t.id,
            name=t.name,
            token_prefix=t.token_prefix,
            scopes=list(t.scopes),
            created_at=t.created_at,
            last_used_at=t.last_used_at,
            expires_at=t.expires_at,
            revoked_at=t.revoked_at,
        )
        for t in services.identity.list_pats(scope)
    ]


@router.post("/tokens", response_model=CreatedPatResponse, status_code=201)
def create_token(
    body: CreatePatRequest,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> CreatedPatResponse:
    created = services.identity.create_pat(
        scope,
        name=body.name,
        scopes=body.scopes,
        ttl_days=body.ttl_days,
    )
    rec = created.record
    return CreatedPatResponse(
        token=created.token,
        record=PatResponse(
            id=rec.id,
            name=rec.name,
            token_prefix=rec.token_prefix,
            scopes=list(rec.scopes),
            created_at=rec.created_at,
            last_used_at=rec.last_used_at,
            expires_at=rec.expires_at,
            revoked_at=rec.revoked_at,
        ),
    )


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: str,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.identity.revoke_pat(scope, token_id)
