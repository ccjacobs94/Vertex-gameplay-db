"""HTTP client for pulling entity history from a Home Assistant instance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from vertex.infrastructure.errors import ValidationError


class HttpHomeAssistantHistoryProvider:
    """Queries Home Assistant's `/api/history/period/...` REST endpoint via httpx."""

    def __init__(
        self,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=30.0))

    async def fetch_history(
        self,
        *,
        ha_url: str,
        ha_token: str,
        entity_id: str,
        start_time: datetime,
        end_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        clean_url = ha_url.strip().rstrip("/")
        clean_token = ha_token.strip()
        clean_entity = entity_id.strip()
        if not clean_url or not clean_token or not clean_entity:
            raise ValidationError("Home Assistant URL, token, and entity_id are all required")

        start_iso = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{clean_url}/api/history/period/{start_iso}"
        params: dict[str, str] = {"filter_entity_id": clean_entity}
        if end_time is not None:
            params["end_time"] = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        headers = {
            "Authorization": f"Bearer {clean_token}",
            "Content-Type": "application/json",
        }
        async with self.client_factory() as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code in (401, 403):
                raise ValidationError(f"Home Assistant rejected access token (HTTP {resp.status_code})")
            if resp.status_code >= 400:
                raise ValidationError(f"Home Assistant history request failed (HTTP {resp.status_code})")
            data = resp.json()

        if isinstance(data, list) and data:
            if isinstance(data[0], list):
                return [row for row in data[0] if isinstance(row, dict)]
            return [row for row in data if isinstance(row, dict)]
        return []
