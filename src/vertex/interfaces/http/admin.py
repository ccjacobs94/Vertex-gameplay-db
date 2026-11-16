"""Administrator routes for household users, invitations, and safe instance settings."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import AppServices, get_services, require_admin_scope
from vertex.interfaces.http.schemas import (
    AdminCreateUserRequest,
    AdminResetPasswordRequest,
    AdminUpdateUserRequest,
    CreatedInviteResponse,
    CreateInviteRequest,
    InviteResponse,
    UserAccountResponse,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[UserAccountResponse])
def list_users(
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> list[UserAccountResponse]:
    return [UserAccountResponse(**asdict(u)) for u in services.identity.list_users(scope)]


@router.post("/users", response_model=UserAccountResponse, status_code=201)
def create_user(
    body: AdminCreateUserRequest,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> UserAccountResponse:
    user = services.identity.admin_create_user(
        scope,
        username=body.username,
        password=body.password,
        role=body.role,
    )
    return UserAccountResponse(**asdict(user))


@router.patch("/users/{user_id}", response_model=UserAccountResponse)
def update_user(
    user_id: str,
    body: AdminUpdateUserRequest,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> UserAccountResponse:
    user = services.identity.admin_update_user(
        scope,
        user_id,
        role=body.role,
        status=body.status,
        status_reason=body.status_reason,
    )
    return UserAccountResponse(**asdict(user))


@router.post("/users/{user_id}/reset-password", response_model=UserAccountResponse)
def reset_password(
    user_id: str,
    body: AdminResetPasswordRequest,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> UserAccountResponse:
    user = services.identity.admin_reset_password(scope, user_id, new_password=body.new_password)
    return UserAccountResponse(**asdict(user))


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.identity.admin_delete_user(scope, user_id)


@router.get("/invites", response_model=list[InviteResponse])
def list_invites(
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> list[InviteResponse]:
    return [InviteResponse(**asdict(i)) for i in services.identity.list_invites(scope)]


@router.post("/invites", response_model=CreatedInviteResponse, status_code=201)
def create_invite(
    body: CreateInviteRequest,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> CreatedInviteResponse:
    created = services.identity.create_invite(
        scope,
        role=body.role,
        note=body.note,
        ttl_hours=body.ttl_hours,
    )
    return CreatedInviteResponse(
        code=created.code,
        record=InviteResponse(**asdict(created.record)),
    )


@router.delete("/invites/{code_hash}", status_code=204)
def revoke_invite(
    code_hash: str,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.identity.revoke_invite(scope, code_hash)


@router.get("/settings", response_model=dict[str, str])
def get_settings(
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, str]:
    return services.identity.get_instance_settings(scope)


@router.put("/settings", response_model=dict[str, str])
def update_settings(
    body: dict[str, str],
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, str]:
    return services.identity.update_instance_settings(scope, body)
