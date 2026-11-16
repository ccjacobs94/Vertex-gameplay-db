"""Optional Web Push / VAPID HTTP endpoints (disabled by default)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from vertex.identity.models import UserScope
from vertex.integrations.push.web_push import WebPushDisabledError, WebPushError
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_read_scope,
    require_write_scope,
)
from vertex.interfaces.http.schemas import (
    PushSubscribeRequest,
    PushSubscribeResponse,
    PushTestResponse,
    PushUnsubscribeRequest,
    PushVapidKeyResponse,
)

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/vapid-public-key", response_model=PushVapidKeyResponse)
def get_vapid_public_key(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> PushVapidKeyResponse:
    del scope
    if not services.web_push.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web Push notifications are disabled on this instance",
        )
    pub = services.web_push.get_public_key()
    return PushVapidKeyResponse(enabled=True, public_key=pub)


@router.post("/subscribe", response_model=PushSubscribeResponse, status_code=status.HTTP_201_CREATED)
def subscribe_push(
    body: PushSubscribeRequest,
    request: Request,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> PushSubscribeResponse:
    if not services.web_push.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web Push notifications are disabled on this instance",
        )
    p256dh = (body.keys.p256dh if body.keys else body.p256dh) or ""
    auth = (body.keys.auth if body.keys else body.auth) or ""
    ua = request.headers.get("user-agent", "")
    try:
        sub = services.web_push.subscribe(
            scope,
            endpoint=body.endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=ua,
        )
    except WebPushDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WebPushError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PushSubscribeResponse(id=sub.id, endpoint=sub.endpoint, created_at=sub.created_at)


@router.delete("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe_push(
    body: PushUnsubscribeRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> None:
    if not services.web_push.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web Push notifications are disabled on this instance",
        )
    services.web_push.unsubscribe(scope, endpoint=body.endpoint)


@router.post("/test", response_model=PushTestResponse)
async def send_test_push(
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> PushTestResponse:
    if not services.web_push.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web Push notifications are disabled on this instance",
        )
    result = await services.web_push.send_test_notification(scope)
    return PushTestResponse(**result)
