"""Optional outbound catalog metadata lookup provider (Steam Store + Wikipedia, fixture-testable)."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

import httpx

from vertex.catalog.taxonomy import GENRES, validate_genres, validate_tags

logger = logging.getLogger("vertex.integrations.catalog_lookup")

_WIKIPEDIA_GENRE_PHRASES: list[tuple[str, str]] = sorted(
    [
        *((g.lower(), g) for g in GENRES),
        ("action-adventure", "Action-Adventure"),
        ("action role-playing", "Action RPG"),
        ("tactical role-playing", "Tactical RPG"),
        ("role-playing", "RPG"),
        ("first-person shooter", "FPS"),
        ("third-person shooter", "Third-Person Shooter"),
        ("turn-based strategy", "Turn-Based Strategy"),
        ("puzzle-platform", "Puzzle Platformer"),
        ("puzzle platformer", "Puzzle Platformer"),
        ("platform", "Platformer"),
        ("survival horror", "Horror"),
        ("hack-and-slash", "Hack and Slash"),
        ("hack and slash", "Hack and Slash"),
        ("roguelite", "Roguelike"),
        ("visual novel", "Visual Novel"),
    ],
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def _extract_genres_from_description(desc: str) -> list[str]:
    low = (desc or "").lower()
    if not low:
        return []
    found: list[str] = []
    for phrase, canonical in _WIKIPEDIA_GENRE_PHRASES:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", low) and canonical not in found:
            found.append(canonical)
    return validate_genres(found[:4])


class HttpCatalogLookupProvider:
    """Implements CatalogLookupProvider using keyless public Steam Store and Wikipedia APIs."""

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=12.0))

    async def _fetch_steam_appdetails(
        self,
        client: httpx.AsyncClient,
        appid: int,
    ) -> dict[str, Any] | None:
        resp = await client.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": str(appid), "l": "english", "cc": "US"},
        )
        if resp.status_code != 200:
            return None
        payload = resp.json().get(str(appid), {})
        if not payload.get("success") or not isinstance(payload.get("data"), dict):
            return None
        data = payload["data"]
        raw_genres = [g.get("description", "") for g in (data.get("genres") or []) if isinstance(g, dict)]
        raw_cats = [c.get("description", "") for c in (data.get("categories") or []) if isinstance(c, dict)]
        genres = validate_genres(raw_genres)
        tags = validate_tags(raw_genres + raw_cats)

        rel_year: int | None = None
        rel_date = str((data.get("release_date") or {}).get("date") or "")
        yr_match = re.search(r"\b(19\d{2}|20\d{2})\b", rel_date)
        if yr_match:
            rel_year = int(yr_match.group(1))

        mc_score = (data.get("metacritic") or {}).get("score")
        critic_score = int(mc_score) if isinstance(mc_score, int) else None

        return {
            "title": str(data.get("name") or "").strip(),
            "steam_appid": str(appid),
            "genres": genres,
            "tags": tags,
            "release_year": rel_year,
            "critic_score": critic_score,
            "cover_url": f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg",
            "source": "Steam Store",
        }

    async def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        capped = max(1, min(20, int(limit)))
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        async with self.client_factory() as client:
            try:
                r = await client.get(
                    "https://store.steampowered.com/api/storesearch/",
                    params={"term": q, "l": "english", "cc": "US"},
                )
                if r.status_code == 200:
                    for idx, item in enumerate((r.json().get("items") or [])[:capped]):
                        appid = item.get("id")
                        name = str(item.get("name") or "").strip()
                        if not appid or not name or name.lower() in seen:
                            continue
                        seen.add(name.lower())
                        details: dict[str, Any] | None = None
                        if idx < 3:
                            try:
                                details = await self._fetch_steam_appdetails(client, int(appid))
                            except Exception:
                                details = None
                        mc = item.get("metascore")
                        critic = int(mc) if mc and str(mc).isdigit() else None
                        results.append(
                            {
                                "title": (details or {}).get("title") or name,
                                "steam_appid": str(appid),
                                "genres": (details or {}).get("genres") or [],
                                "tags": (details or {}).get("tags") or [],
                                "release_year": (details or {}).get("release_year"),
                                "critic_score": (details or {}).get("critic_score") or critic,
                                "cover_url": f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg",
                                "source": "Steam Store",
                            }
                        )
            except Exception as exc:
                logger.debug("Steam catalog search error: %s", exc)

            if len(results) < capped:
                try:
                    rw = await client.get(
                        "https://en.wikipedia.org/w/api.php",
                        params={
                            "action": "query",
                            "generator": "search",
                            "gsrsearch": f"{q} video game",
                            "gsrlimit": str(min(5, capped)),
                            "prop": "pageimages|description",
                            "piprop": "thumbnail",
                            "pilicense": "any",
                            "pithumbsize": "600",
                            "format": "json",
                        },
                    )
                    if rw.status_code == 200:
                        pages = (rw.json().get("query") or {}).get("pages") or {}
                        sorted_pages = sorted(pages.values(), key=lambda p: int(p.get("index") or 99))
                        for page in sorted_pages:
                            raw_title = str(page.get("title") or "")
                            clean_title = re.sub(r"\s*\(.*?video game.*?\)\s*", "", raw_title, flags=re.I).strip()
                            if not clean_title or clean_title.lower() in seen:
                                continue
                            seen.add(clean_title.lower())
                            desc = str(page.get("description") or "")
                            yr_m = re.search(r"\b(19[7-9]\d|20[0-3]\d)\b", desc)
                            thumb = (page.get("thumbnail") or {}).get("source")
                            results.append(
                                {
                                    "title": clean_title,
                                    "steam_appid": None,
                                    "genres": _extract_genres_from_description(desc),
                                    "tags": [],
                                    "release_year": int(yr_m.group(1)) if yr_m else None,
                                    "critic_score": None,
                                    "cover_url": thumb,
                                    "source": "Wikipedia",
                                }
                            )
                except Exception as exc:
                    logger.debug("Wikipedia catalog search error: %s", exc)

        return results[:capped]

    async def lookup_game(
        self,
        *,
        title: str,
        external_id: str | None = None,
    ) -> dict[str, Any] | None:
        ext = str(external_id or "")
        async with self.client_factory() as client:
            if ext.startswith("steam:"):
                appid_part = ext.split(":", 1)[1]
                if appid_part.isdigit():
                    try:
                        details = await self._fetch_steam_appdetails(client, int(appid_part))
                        if details:
                            return details
                    except Exception as exc:
                        logger.debug("Steam appdetails lookup failed for %s: %s", ext, exc)
        matches = await self.search(title, limit=3)
        if not matches:
            return None
        lower_t = title.strip().lower()
        for m in matches:
            if str(m.get("title") or "").strip().lower() == lower_t:
                return m
        return matches[0]
