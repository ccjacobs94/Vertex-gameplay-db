"""
Automated Multi-Platform Sync Engine + Metadata Search for Game Hub.
Supports:
  1. Steam (PC) via Official Steam Web API
  2. Xbox & PC Game Pass via OpenXBL API (xbl.io)
  3. PlayStation 4 / 5 via PSNAWP (NPSSO token)
  4. Keyless External Game Search (Steam Store API + Wikipedia Game Box Art + optional RAWG API)
     for adding Nintendo Switch or physical console games in 1 click.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from db import (
    DEFAULT_DB_PATH,
    get_connection,
    get_game_achievements,
    get_game_by_id,
    log_sync_event,
    normalize_game_title,
    upsert_game_achievements,
    upsert_synced_game,
)
from taxonomy import merge_lists, validate_genres

logger = logging.getLogger("gamehub.sync")

USER_AGENT = "GameHubApp/1.0 (https://localhost; admin@localhost.localdomain) httpx/0.27"


def _make_async_client(timeout: float = 20.0, extra_headers: Optional[Dict[str, str]] = None) -> httpx.AsyncClient:
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    return httpx.AsyncClient(timeout=timeout, headers=headers, transport=transport)


async def resolve_steam_vanity(client: httpx.AsyncClient, api_key: str, steam_id_or_vanity: str) -> str:
    val = steam_id_or_vanity.strip()
    if val.isdigit() and len(val) >= 16:
        return val
    resp = await client.get(
        "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/",
        params={"key": api_key, "vanityurl": val},
    )
    resp.raise_for_status()
    data = resp.json().get("response", {})
    if data.get("success") == 1 and data.get("steamid"):
        return str(data["steamid"])
    raise ValueError(f"Could not resolve Steam vanity URL '{val}' to a 64-bit SteamID.")


_steam_schema_cache: Dict[int, Dict[str, Any]] = {}


async def fetch_steam_game_schema(client: httpx.AsyncClient, api_key: str, appid: int) -> Dict[str, Dict[str, Any]]:
    if appid in _steam_schema_cache:
        return _steam_schema_cache[appid]
    try:
        r = await client.get(
            "https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/",
            params={"key": api_key, "appid": str(appid), "l": "english"},
            timeout=15.0,
        )
        if r.status_code == 200:
            schema_achs = r.json().get("game", {}).get("availableGameStats", {}).get("achievements", [])
            schema_map = {
                a["name"]: {
                    "displayName": a.get("displayName") or a["name"],
                    "description": a.get("description") or "",
                    "icon": a.get("icon"),
                    "icongray": a.get("icongray"),
                    "hidden": a.get("hidden", 0),
                }
                for a in schema_achs
                if a.get("name")
            }
            _steam_schema_cache[appid] = schema_map
            return schema_map
    except Exception as exc:
        logger.debug("Could not fetch Steam schema for appid %s: %s", appid, exc)
    _steam_schema_cache[appid] = {}
    return {}


async def sync_steam_achievements_for_game(
    client: httpx.AsyncClient,
    api_key: str,
    steam_id: str,
    game_id: int,
    appid: int,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get(
            "https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/",
            params={"key": api_key, "steamid": steam_id, "appid": str(appid), "l": "english"},
            timeout=15.0,
        )
        if r.status_code != 200:
            return None
        payload = r.json()
        playerstats = payload.get("playerstats", {})
        if not playerstats.get("success"):
            return None
        raw_achs = playerstats.get("achievements", [])
        if not raw_achs:
            return None

        schema_map = await fetch_steam_game_schema(client, api_key, appid)

        formatted = []
        for a in raw_achs:
            api_name = a.get("apiname")
            if not api_name:
                continue
            sch = schema_map.get(api_name, {})
            name = a.get("name") or sch.get("displayName") or api_name
            desc = a.get("description") or sch.get("description") or ""
            icon_url = sch.get("icon")
            icon_gray_url = sch.get("icongray")
            unlocked = 1 if a.get("achieved") == 1 else 0
            utime = a.get("unlocktime")
            unlock_time = (
                datetime.fromtimestamp(utime, tz=timezone.utc).isoformat()
                if utime and utime > 0
                else None
            )
            formatted.append({
                "external_ach_id": api_name,
                "name": name,
                "description": desc,
                "icon_url": icon_url,
                "icon_gray_url": icon_gray_url,
                "unlocked": unlocked,
                "unlock_time": unlock_time,
                "reward_type": None,
                "reward_value": None,
                "rarity_pct": None,
                "is_hidden": sch.get("hidden", 0),
            })

        if formatted:
            return upsert_game_achievements(
                game_id=game_id,
                source="Steam",
                platform="PC (Steam)",
                achievements=formatted,
                db_path=db_path,
            )
    except Exception as exc:
        logger.debug("Steam achievement fetch error for appid %s: %s", appid, exc)
    return None


async def sync_xbox_achievements_for_game(
    client: httpx.AsyncClient,
    xbl_key: str,
    game_id: int,
    title_id: str,
    platform: str = "Xbox",
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get(
            f"https://xbl.io/api/v2/achievements/title/{title_id}",
            headers={"X-Authorization": xbl_key, "Accept": "application/json"},
            timeout=20.0,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        raw_achs = (data.get("content") or {}).get("achievements") or data.get("achievements", [])
        if not raw_achs:
            return None

        formatted = []
        for a in raw_achs:
            ach_id = str(a.get("id") or "")
            name = a.get("name") or ach_id
            desc = a.get("description") or a.get("lockedDescription") or ""

            icon_url = None
            for m in a.get("mediaAssets") or []:
                if m.get("type") == "Icon" and m.get("url"):
                    icon_url = m["url"]
                    break

            unlocked = 1 if str(a.get("progressState") or "").lower() == "achieved" else 0

            prog = a.get("progression") or {}
            time_unl = prog.get("timeUnlocked")
            unlock_time = None
            if time_unl:
                try:
                    unlock_time = datetime.fromisoformat(time_unl.replace("Z", "+00:00")).isoformat()
                except Exception:
                    unlock_time = time_unl

            gamerscore = None
            for rew in a.get("rewards") or []:
                if rew.get("type") == "Gamerscore" and rew.get("value") is not None:
                    gamerscore = str(rew["value"])
                    break

            rarity_obj = a.get("rarity") or {}
            rarity_pct = None
            if rarity_obj.get("currentPercentage") is not None:
                try:
                    rarity_pct = float(rarity_obj["currentPercentage"])
                except Exception:
                    pass

            formatted.append({
                "external_ach_id": ach_id,
                "name": name,
                "description": desc,
                "icon_url": icon_url,
                "icon_gray_url": None,
                "unlocked": unlocked,
                "unlock_time": unlock_time,
                "reward_type": "Gamerscore" if gamerscore is not None else None,
                "reward_value": gamerscore,
                "rarity_pct": rarity_pct,
                "is_hidden": 1 if a.get("isSecret") else 0,
            })

        if formatted:
            return upsert_game_achievements(
                game_id=game_id,
                source="Xbox",
                platform=platform,
                achievements=formatted,
                db_path=db_path,
            )
    except Exception as exc:
        logger.debug("Xbox achievement fetch error for title %s: %s", title_id, exc)
    return None


async def sync_steam(db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    api_key = os.environ.get("STEAM_API_KEY", "").strip()
    steam_id_raw = os.environ.get("STEAM_ID", "").strip()

    if not api_key or not steam_id_raw:
        return {"source": "Steam", "status": "skipped", "games_synced": 0, "message": "STEAM_API_KEY or STEAM_ID not set"}

    try:
        async with _make_async_client(timeout=30.0) as client:
            steam_id = await resolve_steam_vanity(client, api_key, steam_id_raw)
            resp = await client.get(
                "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
                params={
                    "key": api_key,
                    "steamid": steam_id,
                    "include_appinfo": 1,
                    "include_played_free_games": 1,
                    "format": "json",
                },
            )
            resp.raise_for_status()
            games = resp.json().get("response", {}).get("games", [])

            count = 0
            upserted_steam_games = []
            for g in games:
                appid = g.get("appid")
                name = g.get("name")
                if not appid or not name:
                    continue

                playtime_mins = g.get("playtime_forever", 0)
                playtime_hours = round(playtime_mins / 60.0, 2)

                rtime = g.get("rtime_last_played", 0)
                last_played = (
                    datetime.fromtimestamp(rtime, tz=timezone.utc).isoformat()
                    if rtime and rtime > 0
                    else None
                )

                cover_url = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg"

                saved_game = upsert_synced_game(
                    external_id=f"steam:{appid}",
                    title=name,
                    platform="PC (Steam)",
                    source="Steam",
                    playtime_hours=playtime_hours,
                    last_played=last_played,
                    cover_url=cover_url,
                    db_path=db_path,
                )
                upserted_steam_games.append((saved_game["id"], appid, playtime_hours))
                count += 1

            # Fetch achievements for Steam games with playtime or that have not synced achievements
            sem = asyncio.Semaphore(6)
            async def _fetch_ach(g_db_id: int, a_id: int):
                async with sem:
                    await sync_steam_achievements_for_game(
                        client, api_key, steam_id, g_db_id, a_id, db_path=db_path
                    )

            ach_tasks = [
                _fetch_ach(gid, aid)
                for gid, aid, p_hours in upserted_steam_games
                if p_hours > 0
            ]
            if ach_tasks:
                await asyncio.gather(*ach_tasks, return_exceptions=True)

        log_sync_event("Steam", "success", count, f"Synced {count} Steam games", db_path=db_path)
        return {"source": "Steam", "status": "success", "games_synced": count, "message": f"Synced {count} Steam games"}

    except Exception as exc:
        msg = f"Steam sync failed: {exc}"
        logger.exception(msg)
        log_sync_event("Steam", "error", 0, str(exc), db_path=db_path)
        return {"source": "Steam", "status": "error", "games_synced": 0, "message": str(exc)}


async def sync_xbox(db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    xbl_key = os.environ.get("XBL_API_KEY", "").strip()
    if not xbl_key:
        return {"source": "Xbox", "status": "skipped", "games_synced": 0, "message": "XBL_API_KEY not set"}

    try:
        headers = {
            "X-Authorization": xbl_key,
            "Accept": "application/json",
            "Accept-Language": "en-US",
        }
        async with _make_async_client(timeout=40.0, extra_headers=headers) as client:
            titles: List[Dict[str, Any]] = []
            xuid = None

            # OpenXBL v2 /api/v2/achievements provides rich 'images' arrays containing official 2:3 'Poster' box art
            try:
                ach_resp = await client.get("https://xbl.io/api/v2/achievements")
                if ach_resp.status_code == 200:
                    ach_data = ach_resp.json()
                    if isinstance(ach_data, dict):
                        xuid = (ach_data.get("content") or {}).get("xuid") or ach_data.get("xuid")
                        titles = (ach_data.get("content") or {}).get("titles") or ach_data.get("titles", [])
                    elif isinstance(ach_data, list):
                        titles = ach_data
            except Exception as exc:
                logger.warning("Could not fetch Xbox achievements endpoint for posters: %s", exc)

            # Fallback to /api/v2/titles if achievements endpoint didn't return titles
            if not titles:
                resp = await client.get("https://xbl.io/api/v2/titles")
                resp.raise_for_status()
                payload = resp.json()
                
                # OpenXBL v2 wraps titles and xuid in payload['content']
                if isinstance(payload, dict):
                    if "content" in payload and isinstance(payload["content"], dict):
                        xuid = payload["content"].get("xuid")
                        titles = payload["content"].get("titles", [])
                    else:
                        xuid = payload.get("xuid")
                        titles = payload.get("titles", [])
                elif isinstance(payload, list):
                    titles = payload
                else:
                    titles = []

            if not xuid:
                try:
                    acc_resp = await client.get("https://xbl.io/api/v2/account")
                    if acc_resp.status_code == 200:
                        acc_data = acc_resp.json()
                        users = (acc_data.get("content") or {}).get("profileUsers") or []
                        if users and isinstance(users[0], dict):
                            xuid = users[0].get("id")
                except Exception as exc:
                    logger.warning("Could not fetch Xbox account XUID: %s", exc)

            # Bulk-fetch MinutesPlayed across all titleIds via POST /api/v2/player/stats
            playtime_map: Dict[str, float] = {}
            all_title_ids = [str(t.get("titleId")) for t in titles if t.get("titleId")]
            if xuid and all_title_ids:
                chunk_size = 250
                for idx in range(0, len(all_title_ids), chunk_size):
                    chunk = all_title_ids[idx : idx + chunk_size]
                    try:
                        stats_resp = await client.post(
                            "https://xbl.io/api/v2/player/stats",
                            json={
                                "xuids": [str(xuid)],
                                "stats": [{"name": "MinutesPlayed", "titleId": tid} for tid in chunk],
                            },
                        )
                        if stats_resp.status_code == 200:
                            stats_payload = stats_resp.json()
                            stat_collections = (stats_payload.get("content") or {}).get("statlistscollection") or []
                            for col in stat_collections:
                                for st in col.get("stats") or []:
                                    tid = str(st.get("titleid") or st.get("titleId") or "")
                                    val = st.get("value")
                                    if tid and val is not None and str(val).strip() != "":
                                        try:
                                            playtime_map[tid] = float(val)
                                        except (ValueError, TypeError):
                                            pass
                    except Exception as exc:
                        logger.warning("Could not fetch Xbox playtime stats for chunk: %s", exc)

            count = 0
            upserted_xbox_games = []
            for t in titles:
                title_id = t.get("titleId")
                name = t.get("name")
                if not title_id or not name:
                    continue

                # Filter out non-game media apps if type is specified
                title_type = (t.get("type") or "Game").lower()
                if title_type not in ("game", "dvr", ""):
                    continue

                devices = t.get("devices") or []
                if "Win32" in devices or "PC" in devices:
                    if any(x in devices for x in ("XboxSeries", "XboxOne", "Xbox360")):
                        platform = "Xbox / PC"
                    else:
                        platform = "PC (Xbox)"
                else:
                    platform = "Xbox"

                ach = t.get("achievement") or {}
                unlocked = int(ach.get("currentAchievements") or 0)
                total_ach = int(ach.get("totalAchievements") or 0)

                last_played_raw = t.get("titleHistory", {}).get("lastTimePlayed") or t.get("lastTimePlayed")
                last_played = None
                if last_played_raw:
                    try:
                        last_played = datetime.fromisoformat(last_played_raw.replace("Z", "+00:00")).isoformat()
                    except Exception:
                        last_played = None

                # Select cover art: Prioritize vertical 2:3 Poster (prevents logo clipping), then BoxArt, then displayImage
                poster_url = None
                boxart_url = None
                for img in (t.get("images") or []):
                    itype = img.get("type")
                    iurl = img.get("url")
                    if itype == "Poster" and iurl:
                        poster_url = iurl
                        break
                    elif itype == "BoxArt" and iurl and not boxart_url:
                        boxart_url = iurl

                cover_url = poster_url or boxart_url or t.get("displayImage") or None
                playtime_mins = playtime_map.get(str(title_id), float(t.get("minutesPlayed") or 0.0))
                playtime_hours = round(playtime_mins / 60.0, 2)

                saved_game = upsert_synced_game(
                    external_id=f"xbox:{title_id}",
                    title=name,
                    platform=platform,
                    source="Xbox",
                    playtime_hours=playtime_hours,
                    last_played=last_played,
                    achievements_unlocked=unlocked,
                    achievements_total=total_ach,
                    cover_url=cover_url,
                    db_path=db_path,
                )
                upserted_xbox_games.append((saved_game["id"], str(title_id), platform, playtime_hours, total_ach))
                count += 1

            # Sync detailed achievements for top played titles where totalAchievements is 0 or unpopulated (rate-limited)
            xbox_needing_ach = sorted(
                [g for g in upserted_xbox_games if g[4] == 0 and g[3] > 0],
                key=lambda x: x[3],
                reverse=True,
            )[:25]
            if xbox_needing_ach:
                sem_x = asyncio.Semaphore(4)
                async def _fetch_x_ach(g_id: int, t_id: str, p_form: str):
                    async with sem_x:
                        await sync_xbox_achievements_for_game(
                            client, xbl_key, g_id, t_id, platform=p_form, db_path=db_path
                        )
                await asyncio.gather(
                    *[_fetch_x_ach(g[0], g[1], g[2]) for g in xbox_needing_ach],
                    return_exceptions=True,
                )

        log_sync_event("Xbox", "success", count, f"Synced {count} Xbox / PC Game Pass titles", db_path=db_path)
        return {"source": "Xbox", "status": "success", "games_synced": count, "message": f"Synced {count} Xbox titles"}

    except Exception as exc:
        msg = f"Xbox sync failed: {exc}"
        logger.exception(msg)
        log_sync_event("Xbox", "error", 0, str(exc), db_path=db_path)
        return {"source": "Xbox", "status": "error", "games_synced": 0, "message": str(exc)}


def _sync_psn_blocking(npsso: str, db_path: str = DEFAULT_DB_PATH) -> int:
    from psnawp_api import PSNAWP

    psnawp = PSNAWP(npsso)
    client = psnawp.me()

    trophy_title_map: Dict[str, Any] = {}
    trophy_map: Dict[str, Dict[str, int]] = {}
    try:
        for tr in client.trophy_titles():
            t_name = (getattr(tr, "title_name", None) or "").strip().lower()
            defined = getattr(tr, "defined_trophies", None)
            earned = getattr(tr, "earned_trophies", None)
            if t_name and defined and earned:
                tot = sum(getattr(defined, k, 0) for k in ("bronze", "silver", "gold", "platinum"))
                unl = sum(getattr(earned, k, 0) for k in ("bronze", "silver", "gold", "platinum"))
                trophy_map[t_name] = {"unlocked": unl, "total": tot}
                trophy_title_map[t_name] = tr
    except Exception as exc:
        logger.warning("Could not fetch PSN trophy summary: %s", exc)

    count = 0
    for item in client.title_stats():
        title_id = getattr(item, "title_id", None)
        name = getattr(item, "name", None)
        if not title_id or not name:
            continue

        play_dur = getattr(item, "play_duration", None)
        playtime_hours = round(play_dur.total_seconds() / 3600.0, 2) if play_dur else 0.0

        last_dt = getattr(item, "last_played_date_time", None)
        last_played = last_dt.isoformat() if last_dt else None

        cat = str(getattr(item, "category", "")).upper()
        platform = "PlayStation 5" if "PS5" in cat else "PlayStation 4"

        cover_url = getattr(item, "image_url", None)
        tr_info = trophy_map.get(name.strip().lower(), {"unlocked": 0, "total": 0})

        game_dict = upsert_synced_game(
            external_id=f"psn:{title_id}",
            title=name,
            platform=platform,
            source="PSN",
            playtime_hours=playtime_hours,
            last_played=last_played,
            achievements_unlocked=tr_info["unlocked"],
            achievements_total=tr_info["total"],
            cover_url=cover_url,
            db_path=db_path,
        )
        count += 1

        tr_obj = trophy_title_map.get(name.strip().lower())
        if tr_obj and tr_info["total"] > 0:
            np_comm_id = getattr(tr_obj, "np_communication_id", None)
            plat_set = getattr(tr_obj, "title_platform", None) or []
            target_plat = next(iter(plat_set)) if plat_set else None
            if np_comm_id and target_plat:
                try:
                    trophies = list(client.trophies(np_comm_id, target_plat, include_progress=True))
                    formatted_trophies = []
                    for t in trophies:
                        tid = str(getattr(t, "trophy_id", ""))
                        tname = getattr(t, "trophy_name", "") or f"Trophy {tid}"
                        tdetail = getattr(t, "trophy_detail", "") or ""
                        ticon = getattr(t, "trophy_icon_url", None)
                        earned = bool(getattr(t, "earned", False))
                        edt = getattr(t, "earned_date_time", None)
                        unl_time = edt.isoformat() if edt else None
                        ttype = getattr(t, "trophy_type", None)
                        ttype_str = str(ttype).split(".")[-1].capitalize() if ttype else "Trophy"
                        earn_rate = getattr(t, "trophy_earn_rate", None)
                        rarity = float(earn_rate) if earn_rate is not None else None
                        formatted_trophies.append({
                            "external_ach_id": tid,
                            "name": tname,
                            "description": tdetail,
                            "icon_url": ticon,
                            "icon_gray_url": None,
                            "unlocked": 1 if earned else 0,
                            "unlock_time": unl_time,
                            "reward_type": ttype_str,
                            "reward_value": ttype_str.lower(),
                            "rarity_pct": rarity,
                            "is_hidden": 0,
                        })
                    if formatted_trophies:
                        upsert_game_achievements(
                            game_id=game_dict["id"],
                            source="PSN",
                            platform=platform,
                            achievements=formatted_trophies,
                            db_path=db_path,
                        )
                except Exception as exc:
                    logger.debug("Could not fetch detailed PSN trophies for %s: %s", name, exc)

    return count


def _sync_psn_achievements_for_single_game(
    npsso: str,
    game_id: int,
    ext_id: str,
    title: str,
    platform: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    from psnawp_api import PSNAWP
    psnawp = PSNAWP(npsso)
    client = psnawp.me()

    target_name = (title or "").strip().lower()
    target_norm = normalize_game_title(target_name)

    for tr in client.trophy_titles():
        tr_name = (getattr(tr, "title_name", None) or "").strip().lower()
        if tr_name == target_name or (target_norm and normalize_game_title(tr_name) == target_norm):
            np_comm_id = getattr(tr, "np_communication_id", None)
            plat_set = getattr(tr, "title_platform", None) or []
            target_plat = next(iter(plat_set)) if plat_set else None
            if np_comm_id and target_plat:
                try:
                    trophies = list(client.trophies(np_comm_id, target_plat, include_progress=True))
                    formatted_trophies = []
                    for t in trophies:
                        tid = str(getattr(t, "trophy_id", ""))
                        tname = getattr(t, "trophy_name", "") or f"Trophy {tid}"
                        tdetail = getattr(t, "trophy_detail", "") or ""
                        ticon = getattr(t, "trophy_icon_url", None)
                        earned = bool(getattr(t, "earned", False))
                        edt = getattr(t, "earned_date_time", None)
                        unl_time = edt.isoformat() if edt else None
                        ttype = getattr(t, "trophy_type", None)
                        ttype_str = str(ttype).split(".")[-1].capitalize() if ttype else "Trophy"
                        earn_rate = getattr(t, "trophy_earn_rate", None)
                        rarity = float(earn_rate) if earn_rate is not None else None
                        formatted_trophies.append({
                            "external_ach_id": tid,
                            "name": tname,
                            "description": tdetail,
                            "icon_url": ticon,
                            "icon_gray_url": None,
                            "unlocked": 1 if earned else 0,
                            "unlock_time": unl_time,
                            "reward_type": ttype_str,
                            "reward_value": ttype_str.lower(),
                            "rarity_pct": rarity,
                            "is_hidden": 0,
                        })
                    if formatted_trophies:
                        return upsert_game_achievements(
                            game_id=game_id,
                            source="PSN",
                            platform=platform,
                            achievements=formatted_trophies,
                            db_path=db_path,
                        )
                except Exception as exc:
                    logger.debug("PSN trophy fetch error for %s: %s", title, exc)
            break
    return None


async def sync_game_achievements_on_demand(
    game_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    On-demand live achievement sync for a game and its cross-platform linked copies.
    Fetches the latest achievements from Steam, Xbox, and/or PSN APIs and returns get_game_achievements().
    """
    game = get_game_by_id(game_id, db_path=db_path)
    if not game:
        return {
            "unified_summary": {"unlocked": 0, "total": 0, "percentage": 0.0, "overlap_count": 0},
            "per_platform_summary": [],
            "achievements": [],
        }

    linked_copies = game.get("linked_games") or [game]
    steam_key = os.environ.get("STEAM_API_KEY", "").strip()
    steam_id_raw = os.environ.get("STEAM_ID", "").strip()
    xbl_key = os.environ.get("XBL_API_KEY", "").strip()
    npsso = os.environ.get("PSN_NPSSO", "").strip()

    steam_id = None
    async with _make_async_client(timeout=25.0) as client:
        if steam_key and steam_id_raw:
            try:
                steam_id = await resolve_steam_vanity(client, steam_key, steam_id_raw)
            except Exception as exc:
                logger.debug("Steam vanity resolution error: %s", exc)

        for copy in linked_copies:
            ext_id = str(copy.get("external_id") or "")
            c_id = copy["id"]

            if ext_id.startswith("steam:") and steam_key and steam_id:
                appid_str = ext_id.split(":", 1)[1]
                if appid_str.isdigit():
                    await sync_steam_achievements_for_game(
                        client, steam_key, steam_id, c_id, int(appid_str), db_path=db_path
                    )

            elif ext_id.startswith("xbox:") and xbl_key:
                title_id = ext_id.split(":", 1)[1]
                await sync_xbox_achievements_for_game(
                    client, xbl_key, c_id, title_id, platform=copy.get("platform", "Xbox"), db_path=db_path
                )

            elif ext_id.startswith("psn:") and npsso:
                try:
                    await asyncio.to_thread(
                        _sync_psn_achievements_for_single_game,
                        npsso,
                        c_id,
                        ext_id,
                        copy.get("title", ""),
                        copy.get("platform", "PlayStation 5"),
                        db_path,
                    )
                except Exception as exc:
                    logger.debug("PSN on-demand achievement sync error: %s", exc)

    return get_game_achievements(game_id, db_path=db_path)


async def sync_psn(db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    npsso = os.environ.get("PSN_NPSSO", "").strip()
    if not npsso:
        return {"source": "PSN", "status": "skipped", "games_synced": 0, "message": "PSN_NPSSO not set"}

    import asyncio

    try:
        count = await asyncio.to_thread(_sync_psn_blocking, npsso, db_path)
        log_sync_event("PSN", "success", count, f"Synced {count} PlayStation titles", db_path=db_path)
        return {"source": "PSN", "status": "success", "games_synced": count, "message": f"Synced {count} PlayStation titles"}
    except Exception as exc:
        logger.exception("PSN sync failed")
        log_sync_event("PSN", "error", 0, str(exc), db_path=db_path)
        return {"source": "PSN", "status": "error", "games_synced": 0, "message": str(exc)}


async def sync_all_platforms(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    results = []
    for fn in (sync_steam, sync_xbox, sync_psn):
        res = await fn(db_path=db_path)
        results.append(res)
    try:
        enrich_res = await enrich_missing_metadata(db_path=db_path, limit=500)
        results.append(enrich_res)
    except Exception as exc:
        logger.warning("Post-sync metadata enrichment error: %s", exc)
    return results


_WIKIDATA_GENRE_MAP = {
    "first-person shooter": "FPS",
    "third-person shooter": "Third-Person Shooter",
    "light gun shooter": "Shooter",
    "shoot 'em up": "Shooter",
    "shooter game": "Shooter",
    "tactical shooter": "Tactical Shooter",
    "hero shooter": "Shooter",
    "action role-playing game": "Action RPG",
    "action rpg": "Action RPG",
    "role-playing video game": "RPG",
    "role-playing game": "RPG",
    "rpg": "RPG",
    "fps": "FPS",
    "jrpg": "JRPG",
    "mmorpg": "MMORPG",
    "tactical role-playing game": "Tactical RPG",
    "tactical rpg": "Tactical RPG",
    "japanese role-playing game": "JRPG",
    "massively multiplayer online role-playing game": "MMORPG",
    "roguelike": "Roguelike",
    "roguelite": "Roguelike",
    "deck-building roguelike": "Roguelike",
    "roguelike deck-building game": "Roguelike",
    "action-adventure game": "Action-Adventure",
    "action game": "Action",
    "adventure game": "Adventure",
    "narrative adventure game": "Adventure",
    "graphic adventure": "Adventure",
    "point-and-click adventure": "Adventure",
    "interactive film": "Interactive Drama",
    "interactive drama": "Interactive Drama",
    "platformer": "Platformer",
    "platform game": "Platformer",
    "cinematic platformer": "Platformer",
    "puzzle-platformer": "Puzzle Platformer",
    "metroidvania": "Metroidvania",
    "puzzle video game": "Puzzle",
    "puzzle game": "Puzzle",
    "strategy video game": "Strategy",
    "real-time strategy": "Strategy",
    "turn-based strategy": "Turn-Based Strategy",
    "turn-based tactics": "Tactical",
    "real-time tactics": "Tactical",
    "grand strategy": "Strategy",
    "4x": "Strategy",
    "tower defense": "Tower Defense",
    "simulation video game": "Simulation",
    "life simulation game": "Simulation",
    "construction and management simulation": "Simulation",
    "vehicle simulation game": "Simulation",
    "space flight simulation game": "Simulation",
    "combat flight simulator": "Simulation",
    "sports video game": "Sports",
    "racing video game": "Racing",
    "sim racing": "Racing",
    "kart racing": "Racing",
    "fighting game": "Fighting",
    "beat 'em up": "Beat 'em up",
    "hack and slash": "Hack and Slash",
    "stealth game": "Stealth",
    "survival horror": "Horror",
    "psychological horror": "Horror",
    "horror video game": "Horror",
    "survival game": "Survival",
    "sandbox game": "Sandbox",
    "open world": "Open World",
    "battle royale game": "Battle Royale",
    "rhythm game": "Rhythm",
    "music video game": "Rhythm",
    "party game": "Party",
    "visual novel": "Visual Novel",
    "card battle game": "Card & Board",
    "digital collectible card game": "Card & Board",
    "soulslike": "Soulslike",
    "soulsvania": "Soulslike",
    "2d platform": "Platformer",
    "2d platformer": "Platformer",
    "3d platformer": "Platformer",
    "character-action": "Hack and Slash",
    "character action": "Hack and Slash",
    "city-building": "Simulation",
    "city building": "Simulation",
    "american football": "Sports",
    "association football": "Sports",
    "golf": "Sports",
    "basketball": "Sports",
    "baseball": "Sports",
    "ice hockey": "Sports",
    "tennis": "Sports",
    "skateboarding": "Sports",
    "snowboarding": "Sports",
    "wrestling": "Sports",
    "chess": "Strategy",
    "strategic": "Strategy",
    "twitch shooter": "FPS",
    "immersive sim": "Immersive Sim",
}


def _normalize_genre_label(raw_label: str) -> Optional[str]:
    if not raw_label:
        return None
    low = raw_label.strip().lower()
    if low in _WIKIDATA_GENRE_MAP:
        return _WIKIDATA_GENRE_MAP[low]
    # Ignore thematic settings that Wikidata sometimes mixes into P136 unless nothing else matches
    if any(
        skip in low
        for skip in (
            "science fiction",
            "fantasy",
            "post-apocalyptic",
            "zombie",
            "apocalyptic",
            "cyberpunk",
            "steampunk",
            "historical",
            "comedy",
            "crossover",
            "christmas",
            "eroge",
            "esports",
            "nonlinear",
            "multiplayer",
            "single-player",
            "cooperative",
            "lgbt",
            "character",
            "superhero",
            "retro-style",
            "game of skill",
            "art game",
            "christian",
            "educational",
        )
    ):
        return None
    cleaned = re.sub(r"\s*video game\s*$", "", low, flags=re.I)
    cleaned = re.sub(r"\s*game\s*$", "", cleaned, flags=re.I).strip()
    if not cleaned:
        return None
    if cleaned in _WIKIDATA_GENRE_MAP:
        return _WIKIDATA_GENRE_MAP[cleaned]
    return cleaned.title()


def _extract_genres_from_description(desc: str) -> List[str]:
    if not desc:
        return []
    low = desc.lower()
    found: List[str] = []
    # Check longest keys first
    for phrase, mapped in sorted(_WIKIDATA_GENRE_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if phrase in low and mapped not in found:
            found.append(mapped)
    return found[:4]


async def fetch_steam_app_details(
    client: httpx.AsyncClient, appid: int
) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": str(appid), "l": "english"},
        )
        if r.status_code != 200:
            return None
        payload = r.json()
        app_entry = (payload or {}).get(str(appid))
        if not isinstance(app_entry, dict) or not app_entry.get("success"):
            return None
        data = app_entry.get("data") or {}
        genres = [
            g["description"].strip()
            for g in (data.get("genres") or [])
            if isinstance(g, dict) and g.get("description")
        ]
        # Deduplicate while preserving order
        unique_genres: List[str] = []
        for g in genres:
            if g not in unique_genres:
                unique_genres.append(g)

        rel_date = (data.get("release_date") or {}).get("date") or ""
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", rel_date)
        release_year = int(year_match.group(1)) if year_match else None

        mc = (data.get("metacritic") or {}).get("score")
        critic_score = int(mc) if mc is not None and str(mc).isdigit() else None

        return {
            "genres": unique_genres,
            "release_year": release_year,
            "critic_score": critic_score,
        }
    except Exception as exc:
        logger.debug("Steam appdetails error for %s: %s", appid, exc)
        return None


def _clean_search_query(title: str) -> str:
    clean_q = re.sub(r"[™®©]", " ", title)
    clean_q = re.sub(r"\bTC's\b", "Tom Clancy's", clean_q, flags=re.I)
    clean_q = re.sub(r"\bRainbowSix\b", "Rainbow Six", clean_q, flags=re.I)
    clean_q = re.sub(r"\s+-\s+(?:Demo|The Lockdown|Base Game)\b.*$", "", clean_q, flags=re.I)
    clean_q = re.sub(
        r"\b(for xbox.*?|xbox one|xbox series.*?|windows 10|pc|base game|definitive edition|ultimate edition|demo)\b",
        " ",
        clean_q,
        flags=re.I,
    )
    clean_q = re.sub(r"\s{2,}", " ", clean_q).strip(" -:")
    return clean_q or title


async def fetch_wikidata_metadata(
    client: httpx.AsyncClient, title: str
) -> Optional[Dict[str, Any]]:
    """
    Query Wikipedia + Wikidata (keyless, CC0 open data) for video game genres (P136) and release year (P577).
    """
    clean_q = _clean_search_query(title)

    try:
        r = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"{clean_q} video game",
                "gsrlimit": 3,
                "prop": "pageprops|description",
                "ppprop": "wikibase_item",
                "format": "json",
            },
        )
        if r.status_code != 200:
            return None
        pages = r.json().get("query", {}).get("pages", {})
        if not pages:
            return None

        sorted_pages = sorted(pages.values(), key=lambda p: p.get("index", 99))
        target_norm = normalize_game_title(clean_q)

        chosen_page = None
        for p in sorted_pages:
            raw_t = re.sub(r"\s*\(.*?video game.*?\)\s*", "", p.get("title", ""), flags=re.I)
            if normalize_game_title(raw_t) == target_norm:
                chosen_page = p
                break
        if not chosen_page:
            chosen_page = sorted_pages[0]

        desc = chosen_page.get("description") or ""
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", desc)
        release_year = int(year_match.group(1)) if year_match else None
        desc_genres = _extract_genres_from_description(desc)

        qid = (chosen_page.get("pageprops") or {}).get("wikibase_item")
        if not qid:
            return {"genres": desc_genres, "release_year": release_year, "critic_score": None} if desc_genres else None

        r_claims = await client.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": qid,
                "props": "claims",
                "format": "json",
            },
        )
        if r_claims.status_code != 200:
            return {"genres": desc_genres, "release_year": release_year, "critic_score": None} if desc_genres else None

        entity = r_claims.json().get("entities", {}).get(qid, {})
        claims = entity.get("claims", {})

        # Extract P577 (publication date) if year not already found
        if not release_year and "P577" in claims:
            for c in claims["P577"]:
                time_val = (
                    c.get("mainsnak", {})
                    .get("datavalue", {})
                    .get("value", {})
                    .get("time", "")
                )
                ym = re.search(r"(19\d{2}|20\d{2})", str(time_val))
                if ym:
                    release_year = int(ym.group(1))
                    break

        genre_qids: List[str] = []
        for c in claims.get("P136", []):
            gid = (
                c.get("mainsnak", {})
                .get("datavalue", {})
                .get("value", {})
                .get("id")
            )
            if gid and gid not in genre_qids:
                genre_qids.append(gid)

        genres: List[str] = []
        if genre_qids:
            r_labels = await client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(genre_qids[:8]),
                    "props": "labels",
                    "languages": "en",
                    "format": "json",
                },
            )
            if r_labels.status_code == 200:
                ents = r_labels.json().get("entities", {})
                for gq in genre_qids[:8]:
                    lbl = (
                        ents.get(gq, {})
                        .get("labels", {})
                        .get("en", {})
                        .get("value")
                    )
                    if lbl:
                        norm_lbl = _normalize_genre_label(lbl)
                        if norm_lbl and norm_lbl not in genres:
                            genres.append(norm_lbl)

        for dg in desc_genres:
            if dg not in genres:
                genres.append(dg)

        if not genres:
            return None

        return {
            "genres": genres[:5],
            "release_year": release_year,
            "critic_score": None,
        }
    except Exception as exc:
        logger.debug("Wikidata lookup error for '%s': %s", title, exc)
        return None


async def _find_steam_appid_by_title(
    client: httpx.AsyncClient, title: str
) -> Optional[int]:
    """
    Search Steam Store for a non-Steam game title and return its matching appid if the normalized title matches.
    """
    clean_q = _clean_search_query(title)
    target_norm = normalize_game_title(clean_q)
    if not target_norm:
        return None

    try:
        r = await client.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": clean_q, "l": "english", "cc": "US"},
        )
        if r.status_code != 200:
            return None
        items = r.json().get("items") or []
        for item in items[:5]:
            appid = item.get("id")
            name = item.get("name") or ""
            if not appid or not name:
                continue
            cand_norm = normalize_game_title(name)
            if cand_norm == target_norm:
                return int(appid)
        # Second pass: check prefix/substring match on normalized words if reasonably specific
        target_words = target_norm.split()
        if len(target_words) >= 2:
            for item in items[:3]:
                appid = item.get("id")
                name = item.get("name") or ""
                cand_norm = normalize_game_title(name)
                if cand_norm and (cand_norm.startswith(target_norm) or target_norm.startswith(cand_norm)):
                    return int(appid)
    except Exception as exc:
        logger.debug("Steam storesearch match error for '%s': %s", title, exc)
    return None


async def enrich_missing_metadata(
    db_path: str = DEFAULT_DB_PATH,
    limit: int = 500,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Automatically enrich games that have empty genres ('[]') using free, keyless sources:
      1. Steam Store appdetails API (direct appid lookup for Steam games + normalized title lookup for Xbox/PSN/Switch)
      2. Optional RAWG API (if RAWG_API_KEY is set)
      3. Wikidata P136 & Wikipedia API fallback (for console exclusives not on Steam)
    Propagates discovered genres across linked/reconciled games and normalized title siblings.
    Merge, never replace: discovered genres are added to a game's
    existing genres (validated against taxonomy.GENRES first).
    Existing genres are never dropped, even with force=True.
    """
    with get_connection(db_path) as conn:
        all_rows = [dict(r) for r in conn.execute("SELECT * FROM games ORDER BY playtime_hours DESC").fetchall()]

    # Build caches of already-known metadata by normalized title and by Steam appid
    norm_to_meta: Dict[str, Dict[str, Any]] = {}
    steam_appid_cache: Dict[int, Dict[str, Any]] = {}

    for r in all_rows:
        try:
            g_list = json.loads(r.get("genres") or "[]")
        except Exception:
            g_list = []
        if g_list and not force:
            norm = normalize_game_title(r["title"])
            if norm and norm not in norm_to_meta:
                norm_to_meta[norm] = {
                    "genres": g_list,
                    "release_year": r.get("release_year"),
                    "critic_score": r.get("critic_score"),
                }

    # Filter rows needing enrichment
    targets: List[Dict[str, Any]] = []
    for r in all_rows:
        try:
            g_list = json.loads(r.get("genres") or "[]")
        except Exception:
            g_list = []
        if force or not g_list:
            targets.append(r)

    if not targets:
        return {
            "source": "Metadata Enricher",
            "status": "success",
            "games_enriched": 0,
            "remaining": 0,
            "message": "All games already have genre metadata",
        }

    # Process Steam games first so their appdetails populate norm_to_meta for Xbox/console siblings
    targets.sort(
        key=lambda r: (
            1 if str(r.get("external_id", "")).startswith("steam:") else 0,
            float(r.get("playtime_hours") or 0.0),
        ),
        reverse=True,
    )
    batch = targets[:limit]
    enriched_count = 0
    now = datetime.now(timezone.utc).isoformat()

    async with _make_async_client(timeout=15.0) as client:
        for row in batch:
            game_id = row["id"]
            title = row["title"]
            ext_id = str(row.get("external_id") or "")
            norm = normalize_game_title(title)

            meta: Optional[Dict[str, Any]] = None

            # 1. Reuse already-discovered metadata for the same normalized title
            if norm and norm in norm_to_meta:
                meta = norm_to_meta[norm]

            # 2. Direct Steam appid lookup
            if not meta and ext_id.startswith("steam:"):
                appid_str = ext_id.split(":", 1)[1]
                if appid_str.isdigit():
                    appid = int(appid_str)
                    if appid in steam_appid_cache:
                        meta = steam_appid_cache[appid]
                    else:
                        meta = await fetch_steam_app_details(client, appid)
                        if meta and meta.get("genres"):
                            steam_appid_cache[appid] = meta
                        await asyncio.sleep(0.18)

            # 3. Search Steam catalog by title for Xbox / PlayStation / Switch games
            if (not meta or not meta.get("genres")) and not ext_id.startswith("steam:"):
                matched_appid = await _find_steam_appid_by_title(client, title)
                if matched_appid:
                    if matched_appid in steam_appid_cache:
                        meta = steam_appid_cache[matched_appid]
                    else:
                        meta = await fetch_steam_app_details(client, matched_appid)
                        if meta and meta.get("genres"):
                            steam_appid_cache[matched_appid] = meta
                        await asyncio.sleep(0.18)

            # 4. Fallback to Wikidata (P136 genres) + Wikipedia description
            if not meta or not meta.get("genres"):
                meta = await fetch_wikidata_metadata(client, title)
                await asyncio.sleep(0.1)

            if meta and meta.get("genres"):
                # Validate against the controlled taxonomy first: Steam
                # and Wikidata can return labels that are not genres
                # ("Free To Play", "Casual") or that this library does
                # not use. Unknown/retired labels are dropped, never
                # written. See taxonomy.py.
                discovered = validate_genres(meta["genres"])
                if not discovered:
                    continue
                rel_year = meta.get("release_year")
                c_score = meta.get("critic_score")
                if norm:
                    norm_to_meta[norm] = meta

                # Merge, never replace. The discovered genres are added
                # to whatever the game already has; existing genres are
                # never dropped. This is the fix for the 2026-10-27
                # audit, where a force run replaced Portal 2's
                # Platformer/Puzzle and Blacklist's Stealth with
                # Steam's coarse Action/Adventure list.
                with get_connection(db_path) as conn:
                    cid = row.get("canonical_id")
                    if cid is not None:
                        group_rows = conn.execute(
                            "SELECT id, genres, release_year, critic_score FROM games WHERE canonical_id = ?",
                            (cid,),
                        ).fetchall()
                    else:
                        group_rows = [
                            conn.execute(
                                "SELECT id, genres, release_year, critic_score FROM games WHERE id = ?",
                                (game_id,),
                            ).fetchone()
                        ]
                    for grow in group_rows:
                        try:
                            existing_genres = json.loads(grow["genres"] or "[]")
                        except Exception:
                            existing_genres = []
                        merged = merge_lists(existing_genres, discovered)
                        changed = (
                            merged != existing_genres
                            or (rel_year is not None and grow["release_year"] is None)
                            or (c_score is not None and grow["critic_score"] is None)
                        )
                        if not changed:
                            continue
                        conn.execute(
                            """
                            UPDATE games SET
                                genres = ?,
                                release_year = COALESCE(release_year, ?),
                                critic_score = COALESCE(critic_score, ?),
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (json.dumps(merged), rel_year, c_score, now, grow["id"]),
                        )
                        enriched_count += 1

    with get_connection(db_path) as conn:
        rem_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM games WHERE genres IS NULL OR genres = '[]' OR genres = ''"
        ).fetchone()
        remaining = int(rem_row["cnt"]) if rem_row else 0

    msg = f"Enriched genres for {enriched_count} games ({remaining} remaining without genres)"
    log_sync_event("Metadata Enricher", "success", enriched_count, msg, db_path=db_path)
    return {
        "source": "Metadata Enricher",
        "status": "success",
        "games_enriched": enriched_count,
        "remaining": remaining,
        "message": msg,
    }


async def search_external_metadata(query: str) -> List[Dict[str, Any]]:
    """
    Search external game catalogs for cover art, release year, critic score, and genres.
    Uses:
      1. RAWG API (if RAWG_API_KEY is configured)
      2. Steam Store Search API (keyless, fast, high-res vertical covers)
      3. Wikipedia Video Game Search API (keyless, great for Nintendo Switch & console exclusives)
    """
    q = query.strip()
    if not q:
        return []

    results: List[Dict[str, Any]] = []
    seen_titles = set()

    async with _make_async_client(timeout=12.0) as client:
        # 1. Optional RAWG API if user configured a key
        rawg_key = os.environ.get("RAWG_API_KEY", "").strip()
        if rawg_key:
            try:
                r = await client.get(
                    "https://api.rawg.io/api/games",
                    params={"key": rawg_key, "search": q, "page_size": 6},
                )
                if r.status_code == 200:
                    for item in r.json().get("results", []):
                        name = item.get("name")
                        if not name or name.lower() in seen_titles:
                            continue
                        seen_titles.add(name.lower())
                        released = item.get("released") or ""
                        year = int(released[:4]) if len(released) >= 4 and released[:4].isdigit() else None
                        genres = [g["name"] for g in (item.get("genres") or []) if g.get("name")]
                        results.append(
                            {
                                "title": name,
                                "release_year": year,
                                "critic_score": item.get("metacritic"),
                                "genres": genres,
                                "cover_url": item.get("background_image"),
                                "provider": "RAWG",
                            }
                        )
            except Exception as exc:
                logger.debug("RAWG lookup error: %s", exc)

        # 2. Keyless Steam Store Search API
        try:
            r = await client.get(
                "https://store.steampowered.com/api/storesearch/",
                params={"term": q, "l": "english", "cc": "US"},
            )
            if r.status_code == 200:
                steam_items = r.json().get("items", [])[:5]
                for idx, item in enumerate(steam_items):
                    appid = item.get("id")
                    name = item.get("name")
                    if not appid or not name or name.lower() in seen_titles:
                        continue
                    seen_titles.add(name.lower())
                    mc = item.get("metascore")
                    critic_score = int(mc) if mc and str(mc).isdigit() else None
                    item_genres: List[str] = []
                    rel_year: Optional[int] = None
                    if idx < 3:
                        details = await fetch_steam_app_details(client, int(appid))
                        if details:
                            item_genres = details.get("genres") or []
                            rel_year = details.get("release_year")
                            if not critic_score and details.get("critic_score"):
                                critic_score = details["critic_score"]
                    results.append(
                        {
                            "title": name,
                            "release_year": rel_year,
                            "critic_score": critic_score,
                            "genres": item_genres,
                            "cover_url": f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg",
                            "provider": "Steam Catalog",
                        }
                    )
        except Exception as exc:
            logger.debug("Steam search error: %s", exc)

        # 3. Keyless Wikipedia Search (for Nintendo Switch / PS4 / Console exclusives)
        try:
            r = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"{q} video game",
                    "gsrlimit": 5,
                    "prop": "pageimages|description",
                    "piprop": "thumbnail",
                    "pilicense": "any",
                    "pithumbsize": 600,
                    "format": "json",
                },
            )
            if r.status_code == 200:
                pages = r.json().get("query", {}).get("pages", {})
                sorted_pages = sorted(pages.values(), key=lambda p: p.get("index", 99))
                for page in sorted_pages:
                    raw_title = page.get("title", "")
                    clean_title = re.sub(r"\s*\(.*?video game.*?\)\s*", "", raw_title, flags=re.I).strip()
                    if not clean_title or clean_title.lower() in seen_titles:
                        continue
                    thumb = (page.get("thumbnail") or {}).get("source")
                    if not thumb:
                        continue
                    seen_titles.add(clean_title.lower())
                    desc = page.get("description") or ""
                    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", desc)
                    year = int(year_match.group(1)) if year_match else None
                    wiki_genres = _extract_genres_from_description(desc)
                    results.append(
                        {
                            "title": clean_title,
                            "release_year": year,
                            "critic_score": None,
                            "genres": wiki_genres,
                            "cover_url": thumb,
                            "provider": "Wikipedia",
                        }
                    )
        except Exception as exc:
            logger.debug("Wikipedia search error: %s", exc)

    return results[:8]
