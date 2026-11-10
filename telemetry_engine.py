"""
Telemetry, Session Reconstruction & Daily Metrics Engine for Game Hub.
Implements:
1. Mode A: OpenXBL Real-Time Activity (RTA) WebSocket listener (~1 call/session)
2. Mode B: 15-Minute Adaptive Presence Heartbeat (4 calls/hour active, 1-2 calls/hour quiet)
3. Targeted Session-Close Stat Reconciliation (45s cloud flush delay + 1 stat call + targeted achievements)
4. Daily 4:00 AM Reconciliation Sweep (2 calls/day for <12 min and offline handheld sessions)
5. Session state transitions for Webhooks (Playnite, Home Assistant, local agents)
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import httpx

import db
import system_db
from db import (
    commit_session_to_database,
    get_connection,
    get_game_by_id,
    normalize_game_title,
    normalize_platform_family,
    parse_iso_datetime,
    record_reconciled_session,
    start_session,
    touch_session,
    upsert_synced_game,
)

logger = logging.getLogger("gamehub.telemetry")

# Section 6: Known system / non-game applications to filter out
SYSTEM_APPS: Set[str] = {
    "750323071",   # Xbox Home / Dashboard
    "328178078",   # Microsoft Edge
    "1683392974",  # Blu-ray Player
}

SYSTEM_APP_NAMES: Set[str] = {
    "xbox home",
    "home",
    "dashboard",
    "microsoft edge",
    "edge",
    "blu-ray player",
    "netflix",
    "youtube",
    "spotify",
    "disney+",
    "hulu",
    "prime video",
    "twitch",
    "settings",
    "microsoft store",
    "xbox accessories",
    "captures",
    "media player",
    "tv & movies",
}


def is_system_app(title_id: Any, title_name: Optional[str] = None) -> bool:
    tid_str = str(title_id or "").strip()
    if tid_str in SYSTEM_APPS:
        return True
    if title_name:
        clean_name = title_name.strip().lower()
        if clean_name in SYSTEM_APP_NAMES or any(clean_name.startswith(app) for app in SYSTEM_APP_NAMES):
            return True
    return False


class XboxSessionMonitor:
    """
    Manages Xbox Live session tracking via 15-minute adaptive heartbeat (Mode B)
    or RTA WebSocket events (Mode A), executing targeted 45-second cloud flush
    reconciliations upon game exit.
    """

    def __init__(
        self,
        api_key: str,
        target_xuid: str,
        db_path: Optional[str] = None,
        flush_delay_seconds: float = 45.0,
        user_timezone: str = "auto",
        user_timezone_eras: Optional[List[Dict[str, Any]]] = None,
    ):
        self.api_key = (api_key or "").strip()
        self.target_xuid = str(target_xuid or "").strip()
        self.db_path = db_path
        self.flush_delay_seconds = flush_delay_seconds
        self.user_timezone = user_timezone
        self.user_timezone_eras = user_timezone_eras or []
        self.active_session: Optional[Dict[str, Any]] = None

    def extract_active_game_title(self, presence_payload: Any) -> Optional[Dict[str, str]]:
        """
        Parse presence payload (from /api/v2/presence or /api/v2/presence/{xuid})
        and find the currently active game title, filtering out system apps.
        """
        if not presence_payload:
            return None

        # Unwrap {"content": ...} envelope if returned by OpenXBL
        if isinstance(presence_payload, dict) and "content" in presence_payload:
            presence_payload = presence_payload["content"]

        records = presence_payload if isinstance(presence_payload, list) else [presence_payload]

        for entry in records:
            if not isinstance(entry, dict):
                continue

            # Unwrap inner content if nested
            if "content" in entry and isinstance(entry["content"], dict):
                entry = entry["content"]

            # If user state is explicitly Offline, no active game title
            if str(entry.get("state") or "").lower() == "offline":
                continue

            # Check devices[] format
            devices = entry.get("devices") or []
            for device in devices:
                if not isinstance(device, dict):
                    continue
                for title in device.get("titles") or []:
                    if not isinstance(title, dict):
                        continue
                    state = str(title.get("state") or title.get("activityState") or "").capitalize()
                    t_id = str(title.get("id") or title.get("titleId") or "")
                    t_name = str(title.get("name") or title.get("titleName") or "").strip()
                    if state == "Active" and t_id and not is_system_app(t_id, t_name):
                        return {"titleId": t_id, "name": t_name or f"Xbox Title {t_id}"}

            # Check direct titles[] format
            for title in entry.get("titles") or []:
                if not isinstance(title, dict):
                    continue
                state = str(title.get("state") or title.get("activityState") or "").capitalize()
                t_id = str(title.get("id") or title.get("titleId") or "")
                t_name = str(title.get("name") or title.get("titleName") or "").strip()
                if state == "Active" and t_id and not is_system_app(t_id, t_name):
                    return {"titleId": t_id, "name": t_name or f"Xbox Title {t_id}"}

            # Check player summary format
            title_id = entry.get("titleId")
            title_name = entry.get("titleName") or entry.get("name")
            status = str(entry.get("state") or entry.get("userPresence") or "").lower()
            if title_id and status in ("online", "active") and not is_system_app(title_id, title_name):
                return {"titleId": str(title_id), "name": str(title_name or f"Xbox Title {title_id}")}

        return None

    def parse_minutes_played(self, stats_payload: Any, title_id: str, fallback: float) -> float:
        """Parse MinutesPlayed stat from OpenXBL response."""
        if not stats_payload or not isinstance(stats_payload, dict):
            return fallback

        tid_int = int(title_id) if str(title_id).isdigit() else None
        tid_str = str(title_id)

        # Look in top-level or under "content"
        colls = (
            (stats_payload.get("content") or {}).get("statlistscollection")
            or stats_payload.get("statlistscollection")
            or []
        )
        if isinstance(colls, dict):
            colls = [colls]

        for coll in colls:
            if not isinstance(coll, dict):
                continue
            for s in coll.get("stats") or []:
                if not isinstance(s, dict):
                    continue
                s_tid = s.get("titleid") or s.get("titleId")
                s_name = s.get("name")
                if (s_tid == tid_int or str(s_tid) == tid_str) and str(s_name).lower() == "minutesplayed":
                    val = s.get("value")
                    if val is not None and str(val).strip() != "":
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            pass

        return fallback

    async def _fetch_presence(self) -> Any:
        headers = {
            "X-Authorization": self.api_key,
            "Accept": "application/json",
            "Accept-Language": "en-US",
        }
        endpoints = []
        if self.target_xuid:
            endpoints.append(f"https://api.xbl.io/v2/presence/{self.target_xuid}")
            endpoints.append(f"https://xbl.io/api/v2/presence/{self.target_xuid}")
        endpoints.append("https://api.xbl.io/v2/presence")
        endpoints.append("https://xbl.io/api/v2/presence")

        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            for ep in endpoints:
                try:
                    resp = await client.get(ep)
                    if resp.status_code == 200:
                        return resp.json()
                except Exception as exc:
                    logger.debug("OpenXBL presence fetch failed at %s: %s", ep, exc)
        return None

    async def _fetch_targeted_stats(self, title_id: str) -> Any:
        headers = {
            "X-Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        clean_tid = str(title_id).strip()
        body = {
            "titleIds": [int(clean_tid) if clean_tid.isdigit() else clean_tid]
        }
        endpoints = []
        if self.target_xuid:
            endpoints.append("https://xbl.io/api/v2/player/stats")
            endpoints.append(f"https://xbl.io/api/v2/achievements/stats/{self.target_xuid}")
            endpoints.append(f"https://api.xbl.io/v2/achievements/stats/{self.target_xuid}")
        else:
            endpoints.append("https://xbl.io/api/v2/player/stats")

        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            for ep in endpoints:
                try:
                    if ep.endswith("/player/stats"):
                        r = await client.post(
                            ep,
                            json={"xuids": [str(self.target_xuid)] if self.target_xuid else [], "stats": [{"name": "MinutesPlayed", "titleId": clean_tid}]},
                        )
                    else:
                        r = await client.post(ep, json=body)
                    if r.status_code == 200:
                        return r.json()
                except Exception as exc:
                    logger.debug("OpenXBL targeted stat fetch failed at %s: %s", ep, exc)
        return None

    async def _check_targeted_achievements(
        self,
        game_id: int,
        title_id: str,
        started_at: datetime,
        ended_at: datetime,
    ) -> int:
        """Fetch targeted achievement unlocks and count achievements unlocked in session window."""
        try:
            from sync_engine import _make_async_client, sync_xbox_achievements_for_game

            async with _make_async_client(timeout=15.0) as client:
                res = await sync_xbox_achievements_for_game(
                    client=client,
                    xbl_key=self.api_key,
                    game_id=game_id,
                    title_id=title_id,
                    platform="Xbox",
                    db_path=self.db_path,
                    xuid=self.target_xuid,
                )
                if not res:
                    return 0

            # Count achievements unlocked between started_at and ended_at + 2 mins
            window_end = ended_at + timedelta(minutes=2)
            earned = 0
            with get_connection(self.db_path) as conn:
                achs = conn.execute(
                    "SELECT unlock_time FROM game_achievements WHERE game_id = ? AND unlocked = 1 AND unlock_time IS NOT NULL",
                    (game_id,),
                ).fetchall()
                for a in achs:
                    ut = parse_iso_datetime(a["unlock_time"])
                    if ut and started_at <= ut <= window_end:
                        earned += 1
            return earned
        except Exception as exc:
            logger.debug("Failed checking targeted Xbox achievements: %s", exc)
            return 0

    async def start_session(self, title_id: str, title_name: str) -> Dict[str, Any]:
        """Lookup baseline playtime from local games table (0 API calls) and insert active row into game_sessions."""
        # 1. Lookup or upsert game in games table
        with get_connection(self.db_path) as conn:
            game_row = conn.execute(
                "SELECT * FROM games WHERE external_id = ?", (f"xbox:{title_id}",)
            ).fetchone()

        if not game_row:
            saved_game = upsert_synced_game(
                external_id=f"xbox:{title_id}",
                title=title_name,
                platform="Xbox",
                source="Xbox",
                playtime_hours=0.0,
                db_path=self.db_path,
            )
            game_id = saved_game["id"]
            baseline_minutes = 0.0
            baseline_achs = 0
        else:
            game_id = game_row["id"]
            baseline_minutes = round(float(game_row["playtime_hours"] or 0.0) * 60.0, 2)
            baseline_achs = int(game_row["achievements_unlocked"] or 0)

        started_dt = datetime.now(timezone.utc)
        started_iso = started_dt.isoformat()

        session_record = start_session(
            game_id=game_id,
            external_title_id=str(title_id),
            started_at=started_iso,
            detection_method="presence_poll",
            source="Xbox",
            platform="Xbox",
            db_path=self.db_path,
        )

        self.active_session = {
            "sessionId": session_record["id"],
            "gameId": game_id,
            "titleId": str(title_id),
            "titleName": title_name,
            "startedAt": started_dt,
            "lastSeenAt": started_dt,
            "baselineMinutes": baseline_minutes,
            "baselineAchievements": baseline_achs,
        }
        logger.info("Xbox session started for '%s' (titleId=%s, id=%d)", title_name, title_id, session_record["id"])
        return session_record

    async def touch_session(self, session_id: int) -> None:
        """Update last_seen_at in game_sessions."""
        touch_session(session_id, db_path=self.db_path)
        if self.active_session:
            self.active_session["lastSeenAt"] = datetime.now(timezone.utc)

    async def close_and_reconcile_session(self, session: Dict[str, Any]) -> None:
        """Targeted Session Close Reconciliation (Waits 45s for Xbox cloud flush, then 1 stat call)."""
        ended_at = datetime.now(timezone.utc)
        start_time = session.get("startedAt") or ended_at
        wall_clock_minutes = max(0.0, round((ended_at - start_time).total_seconds() / 60.0, 2))

        # Wait 45s for Xbox Live servers to commit final MinutesPlayed
        if self.flush_delay_seconds > 0:
            logger.info("Waiting %.1fs cloud flush buffer for title %s...", self.flush_delay_seconds, session["titleId"])
            await asyncio.sleep(self.flush_delay_seconds)

        # 1 API Call: Fetch updated MinutesPlayed for ONLY this titleId
        stats_data = await self._fetch_targeted_stats(session["titleId"])
        baseline_mins = float(session.get("baselineMinutes") or 0.0)
        updated_minutes = self.parse_minutes_played(stats_data, session["titleId"], fallback=baseline_mins)

        cloud_delta = max(0.0, round(updated_minutes - baseline_mins, 2))
        active_minutes = cloud_delta if cloud_delta > 0 else wall_clock_minutes
        idle_minutes = max(0.0, round(wall_clock_minutes - active_minutes, 2))

        # Targeted achievement check
        achievements_earned = await self._check_targeted_achievements(
            session["gameId"], session["titleId"], start_time, ended_at
        )

        commit_session_to_database(
            session_id=session["sessionId"],
            ended_at=ended_at.isoformat(),
            wall_clock_minutes=wall_clock_minutes,
            active_minutes=active_minutes,
            idle_minutes=idle_minutes,
            achievements_earned=achievements_earned,
            status="completed",
            user_timezone=self.user_timezone,
            user_timezone_eras=self.user_timezone_eras,
            db_path=self.db_path,
        )

        # Update playtime_hours on games table if cloud minutes increased
        if cloud_delta > 0:
            new_total_hours = round(updated_minutes / 60.0, 2)
            upsert_synced_game(
                external_id=f"xbox:{session['titleId']}",
                title=session["titleName"],
                platform="Xbox",
                source="Xbox",
                playtime_hours=new_total_hours,
                last_played=ended_at.isoformat(),
                achievements_unlocked=session.get("baselineAchievements", 0) + achievements_earned,
                db_path=self.db_path,
            )

        logger.info(
            "Xbox session closed for '%s': wall=%.1fm, active=%.1fm, idle=%.1fm, achs=%d",
            session["titleName"],
            wall_clock_minutes,
            active_minutes,
            idle_minutes,
            achievements_earned,
        )

    async def poll_presence_heartbeat(self) -> None:
        """15-Minute Adaptive Heartbeat (Uses 4 calls/hr during active hours)."""
        presence_data = await self._fetch_presence()
        active_title = self.extract_active_game_title(presence_data)

        if active_title:
            if not self.active_session:
                await self.start_session(active_title["titleId"], active_title["name"])
            elif self.active_session["titleId"] == active_title["titleId"]:
                await self.touch_session(self.active_session["sessionId"])
            else:
                # User switched directly from Game A to Game B
                old_session = self.active_session
                self.active_session = None
                await self.close_and_reconcile_session(old_session)
                await self.start_session(active_title["titleId"], active_title["name"])
        elif self.active_session:
            # User went Offline or returned to Xbox Dashboard
            closing_session = self.active_session
            self.active_session = None
            await self.close_and_reconcile_session(closing_session)


async def run_daily_reconciliation_sweep(
    api_key: str,
    target_xuid: str,
    db_path: Optional[str] = None,
    user_timezone: str = "auto",
    user_timezone_eras: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Daily 4:00 AM Reconciliation Sweep (Section 2.4).
    Catches ultra-short sessions (< 12 minutes) that started and ended between heartbeats,
    or offline handheld sessions synced later:
    1. Calls GET /api/v2/achievements/player/{xuid} (1 call) and filters for titles played within last 24h.
    2. Batch-requests MinutesPlayed via POST /api/v2/achievements/stats/{xuid} (1 call).
    3. Any unrecorded positive delta between games.playtime_hours and recorded sessions is inserted as [Reconciled].
    """
    clean_key = (api_key or "").strip()
    clean_xuid = str(target_xuid or "").strip()
    if not clean_key or not clean_xuid:
        return {"status": "skipped", "message": "Missing XBL API key or XUID"}

    headers = {
        "X-Authorization": clean_key,
        "Accept": "application/json",
        "Accept-Language": "en-US",
    }

    recent_titles: List[Dict[str, Any]] = []
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    # 1. Fetch player achievements summary
    async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
        try:
            r = await client.get(f"https://api.xbl.io/v2/achievements/player/{clean_xuid}")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    titles = data
                elif isinstance(data, dict):
                    titles = (data.get("content") or {}).get("titles") or data.get("titles") or []
                else:
                    titles = []
                for t in titles:
                    last_time = t.get("lastTimePlayed") or (t.get("titleHistory") or {}).get("lastTimePlayed")
                    t_id = str(t.get("titleId") or "")
                    if last_time and t_id:
                        dt = parse_iso_datetime(last_time)
                        if dt and dt >= cutoff_24h and not is_system_app(t_id, t.get("name")):
                            recent_titles.append({
                                "titleId": t_id,
                                "name": t.get("name") or f"Xbox Title {t_id}",
                                "lastTimePlayed": last_time,
                                "currentAchievements": int((t.get("achievement") or {}).get("currentAchievements") or 0),
                            })
        except Exception as exc:
            logger.warning("Daily sweep player achievements call failed: %s", exc)

    if not recent_titles:
        return {"status": "ok", "reconciled_count": 0, "titles_checked": 0}

    # 2. Batch-request MinutesPlayed for those specific titleIds (1 call)
    title_ids = [str(t["titleId"]) for t in recent_titles]
    playtime_map: Dict[str, float] = {}

    endpoints = [
        "https://xbl.io/api/v2/player/stats",
        f"https://xbl.io/api/v2/achievements/stats/{clean_xuid}",
        f"https://api.xbl.io/v2/achievements/stats/{clean_xuid}",
    ]

    async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
        for ep in endpoints:
            try:
                if ep.endswith("/player/stats"):
                    r = await client.post(
                        ep,
                        json={
                            "xuids": [clean_xuid],
                            "stats": [{"name": "MinutesPlayed", "titleId": tid} for tid in title_ids],
                        },
                    )
                else:
                    r = await client.post(
                        ep,
                        json={"titleIds": [int(tid) if tid.isdigit() else tid for tid in title_ids]},
                    )

                if r.status_code == 200:
                    s_payload = r.json()
                    colls = (
                        (s_payload.get("content") or {}).get("statlistscollection")
                        or s_payload.get("statlistscollection")
                        or []
                    )
                    if isinstance(colls, dict):
                        colls = [colls]
                    for coll in colls:
                        if not isinstance(coll, dict):
                            continue
                        for s in coll.get("stats") or []:
                            if not isinstance(s, dict):
                                continue
                            s_tid = str(s.get("titleid") or s.get("titleId") or "")
                            s_name = str(s.get("name") or "").lower()
                            if s_name == "minutesplayed" and s.get("value") is not None:
                                try:
                                    playtime_map[s_tid] = float(s["value"])
                                except (ValueError, TypeError):
                                    pass
                    if playtime_map:
                        break
            except Exception as exc:
                logger.debug("Daily sweep stats fetch failed at %s: %s", ep, exc)

    reconciled_count = 0
    with get_connection(db_path) as conn:
        for t_info in recent_titles:
            tid = t_info["titleId"]
            name = t_info["name"]
            last_played_iso = t_info["lastTimePlayed"]
            achs_earned = int(t_info.get("currentAchievements") or 0)
            new_minutes = playtime_map.get(tid)
            if new_minutes is None:
                continue

            game_row = conn.execute(
                "SELECT id, playtime_hours FROM games WHERE external_id = ?",
                (f"xbox:{tid}",),
            ).fetchone()
            if not game_row:
                continue

            g_id = game_row["id"]
            # Sum recorded active_minutes for this game in game_sessions
            sess_sum_row = conn.execute(
                "SELECT SUM(active_minutes) as tot, COUNT(*) as cnt FROM game_sessions WHERE game_id = ? AND status IN ('completed', 'reconciled')",
                (g_id,),
            ).fetchone()
            recorded_session_mins = float(sess_sum_row["tot"] or 0.0)
            session_count = int(sess_sum_row["cnt"] or 0)

            if session_count > 0:
                unrecorded_delta = round(new_minutes - recorded_session_mins, 2)
            else:
                unrecorded_delta = round(min(new_minutes, 720.0), 2)

            if unrecorded_delta >= 1.0:
                record_reconciled_session(
                    game_id=g_id,
                    delta_minutes=unrecorded_delta,
                    last_time_played_iso=last_played_iso,
                    detection_method="daily_reconciliation",
                    achievements_earned=achs_earned,
                    user_timezone=user_timezone,
                    user_timezone_eras=user_timezone_eras,
                    db_path=db_path,
                )
                reconciled_count += 1
                logger.info(
                    "Daily sweep reconciled +%.1fm for '%s' anchored at %s",
                    unrecorded_delta,
                    name,
                    last_played_iso,
                )

    return {
        "status": "ok",
        "reconciled_count": reconciled_count,
        "titles_checked": len(recent_titles),
    }


class XboxRtaWebSocketClient:
    """
    Mode A: OpenXBL Real-Time Activity (RTA) WebSocket stream.
    Zero-poll listener maintaining persistent connection to wss://rta.xboxlive.com.
    """

    def __init__(
        self,
        watcher_api_key: str,
        target_xuid: str,
        session_monitor: XboxSessionMonitor,
    ):
        self.watcher_api_key = watcher_api_key
        self.target_xuid = target_xuid
        self.monitor = session_monitor
        self._running = False

    async def get_rta_nonce(self) -> Optional[str]:
        headers = {
            "X-Authorization": self.watcher_api_key,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                r = await client.get("https://api.xbl.io/v2/rta")
                if r.status_code == 200:
                    data = r.json()
                    return data.get("nonce") or (data.get("content") or {}).get("nonce")
        except Exception as exc:
            logger.warning("Failed to obtain OpenXBL RTA nonce: %s", exc)
        return None

    async def run(self) -> None:
        """Connect to Xbox Live RTA WebSocket and listen for presence events."""
        try:
            import websockets
        except ImportError:
            logger.info("websockets library not installed; running in Mode B adaptive presence heartbeat mode.")
            return

        self._running = True
        while self._running:
            nonce = await self.get_rta_nonce()
            if not nonce:
                logger.warning("Could not get RTA nonce; backing off 60s...")
                await asyncio.sleep(60)
                continue

            ws_url = f"wss://rta.xboxlive.com/connect?nonce={nonce}"
            try:
                async with websockets.connect(
                    ws_url,
                    subprotocols=["rta.xboxlive.com.V2"],
                    ping_interval=30,
                    ping_timeout=15,
                ) as ws:
                    logger.info("Connected to Xbox Live RTA WebSocket stream.")
                    # Subscribe to target XUID presence
                    sub_msg = [1, 1, f"https://userpresence.xboxlive.com/users/xuid({self.target_xuid})"]
                    await ws.send(json.dumps(sub_msg))

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            packet = json.loads(message)
                            # Packet format: [type, sequence, uri, payload]
                            if isinstance(packet, list) and len(packet) >= 4:
                                payload = packet[3]
                                active_title = self.monitor.extract_active_game_title(payload)
                                if active_title:
                                    if not self.monitor.active_session:
                                        await self.monitor.start_session(active_title["titleId"], active_title["name"])
                                    elif self.monitor.active_session["titleId"] == active_title["titleId"]:
                                        await self.monitor.touch_session(self.monitor.active_session["sessionId"])
                                    else:
                                        old_session = self.monitor.active_session
                                        self.monitor.active_session = None
                                        await self.monitor.close_and_reconcile_session(old_session)
                                        await self.monitor.start_session(active_title["titleId"], active_title["name"])
                                elif self.monitor.active_session:
                                    closing_session = self.monitor.active_session
                                    self.monitor.active_session = None
                                    await self.monitor.close_and_reconcile_session(closing_session)
                        except Exception as p_err:
                            logger.debug("RTA packet parse error: %s", p_err)
            except Exception as ws_err:
                logger.warning("Xbox Live RTA WebSocket connection closed: %s. Reconnecting in 30s...", ws_err)
                await asyncio.sleep(30)

    def stop(self) -> None:
        self._running = False
