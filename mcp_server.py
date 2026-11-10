"""
Model Context Protocol (MCP) Server for Unified Game Hub.
Allows AI agents to inspect your gaming history, playtime metrics, platform breakdowns,
and backlog across PC (Steam), Xbox, PlayStation, and Nintendo Switch.
"""

import json
import logging
from typing import Any, Dict, List, Optional

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        class FastMCP:
            def __init__(self, *args, **kwargs):
                pass
            def tool(self, *args, **kwargs):
                def decorator(fn):
                    return fn
                return decorator
            def resource(self, *args, **kwargs):
                def decorator(fn):
                    return fn
                return decorator
            def run(self, *args, **kwargs):
                pass

import wishlist_sync

from db import (
    add_manual_game,
    add_wishlist_item,
    check_library_ownership_conflicts,
    delete_wishlist_item,
    evaluate_wishlist_deals,
    get_canonical_game_by_title,
    get_connection,
    get_library_stats,
    get_pending_playtime_prompts,
    get_play_session_analytics,
    get_play_sessions as db_get_play_sessions,
    get_reconcile_candidates,
    get_sessions_analytics,
    get_sku_price_history,
    get_wishlist_item,
    get_wishlist_item_by_title,
    infer_edition_tier,
    init_db,
    list_game_editions,
    list_wishlist_items,
    mark_wishlist_item_purchased,
    normalize_game_title,
    normalize_ownership_status,
    normalize_platform_family,
    query_games,
    reconcile_games,
    record_sku_price,
    resolve_playtime_prompt,
    update_game,
    update_play_session,
    update_wishlist_item,
    update_wishlist_platforms as db_update_wishlist_platforms,
    upsert_canonical_game,
    upsert_edition_sku,
    upsert_game_edition,
)
from taxonomy import (
    merge_lists,
    remove_from_list,
    validate_genres,
    validate_tags,
)

from sync_engine import (
    enrich_missing_metadata,
    pick_best_cover_art,
    search_external_metadata,
    sync_game_achievements_on_demand,
)

logger = logging.getLogger("gamehub.mcp")

try:
    init_db(DEFAULT_DB_PATH)
except Exception:
    pass


def get_default_db_path() -> Optional[str]:
    """Helper to retrieve current user database path (or None to use current_user_id context)."""
    return None

# Keys that may carry credentials or internal diagnostics; never exposed to AI clients.
_SENSITIVE_KEY_PARTS = (
    "token", "secret", "password", "passwd", "api_key", "apikey", "npsso",
    "credential", "authorization", "cookie",
)


def _sanitize_output(value: Any) -> Any:
    """Recursively drop credential-like keys and internal sync-log messages from tool output."""
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, val in value.items():
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS):
                continue
            if key == "last_syncs" and isinstance(val, list):
                # Keep status metadata only; sync_logs.message holds raw internal log/error text.
                val = [
                    {k: v for k, v in row.items() if k != "message"} if isinstance(row, dict) else row
                    for row in val
                ]
            cleaned[key] = _sanitize_output(val)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_output(v) for v in value]
    return value


mcp = FastMCP("GameHub-Library-Assistant")


@mcp.tool()
def get_gaming_profile_and_metrics() -> Dict[str, Any]:
    """
    Retrieve high-level gaming metrics, platform distribution (PC, Xbox, PS4/PS5, Switch),
    completion status breakdown, top genres, top 10 most-played games, and recently played titles.
    Use this first to understand the user's gaming habits and history.
    """
    return _sanitize_output(get_library_stats())


@mcp.tool()
def search_game_library(
    query: Optional[str] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    genre: Optional[str] = None,
    tag: Optional[str] = None,
    ownership_status: Optional[str] = None,
    is_game_pass: Optional[bool] = None,
    is_play_anywhere: Optional[bool] = None,
    favorite_only: bool = False,
    sort_by: str = "playtime_desc",
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """
    Search and filter the user's unified game library.
    - platform: e.g. 'Steam', 'Xbox', 'PlayStation', 'Switch'
    - status: 'Not Played', 'Plan to Play', 'Playing', 'Beaten', 'Completed', 'Dropped'
    - ownership_status: 'Owned', 'Subscription', 'Borrowed', 'None' (self-reported)
    - is_game_pass: true to filter for titles currently available on Xbox Game Pass
    - is_play_anywhere: true to filter for Xbox Play Anywhere titles
    - sort_by: 'playtime_desc', 'last_played_desc', 'rating_desc', 'title_asc'
    - tag: e.g. 'Stealth', 'Open World', 'Co-op'. Matches tags and genres.
    """
    return query_games(
        search=query,
        platform=platform,
        status=status,
        genre=genre,
        tag=tag,
        ownership=ownership_status,
        is_game_pass=is_game_pass,
        is_play_anywhere=is_play_anywhere,
        favorite_only=favorite_only,
        sort_by=sort_by,
        limit=limit,
    )


@mcp.tool()
def get_what_to_play_recommendations(
    platform: Optional[str] = None,
    genre: Optional[str] = None,
    tag: Optional[str] = None,
    ownership_status: Optional[str] = None,
    is_game_pass: Optional[bool] = None,
    is_play_anywhere: Optional[bool] = None,
    max_candidates: int = 20,
) -> Dict[str, Any]:
    """
    Returns taste-profile context (the user's highest-played and favorite games) alongside
    candidate games from their backlog ('Plan to Play' or 'Not Played' with < 2.0h playtime)
    and currently 'Playing' games so the AI can recommend what to play next.
    - platform: e.g. 'Steam', 'Xbox', 'PlayStation', 'Switch'
    - genre: filter backlog by broad category (e.g. 'RPG', 'Shooter')
    - tag: filter backlog by mechanic/mode/theme (e.g. 'Stealth', 'Co-op', 'Open World')
    - ownership_status: optional filter ('Owned', 'Subscription', 'Borrowed', 'None')
    - is_game_pass: optional filter for titles currently on Xbox Game Pass
    - is_play_anywhere: optional filter for Xbox Play Anywhere titles
    """
    all_games = query_games(limit=2000)

    favorites_and_top = sorted(
        all_games,
        key=lambda g: (1 if g.get("favorite") else 0, g.get("user_rating") or 0, g.get("playtime_hours") or 0),
        reverse=True,
    )[:12]

    o_q = normalize_ownership_status(ownership_status).lower() if ownership_status else None

    currently_playing = [
        g for g in all_games
        if g.get("completion_status") == "Playing"
        and (not platform or platform.lower() in g.get("platform", "").lower())
        and (is_game_pass is None or bool(g.get("is_game_pass")) == bool(is_game_pass))
        and (is_play_anywhere is None or bool(g.get("is_play_anywhere")) == bool(is_play_anywhere))
        and (
            not o_q
            or (g.get("ownership_status") or "Owned").lower() == o_q
            or (o_q in [s.lower() for s in (g.get("ownership_statuses") or [])])
        )
    ]

    t_q = tag.lower().strip() if tag else None
    g_q = genre.lower().strip() if genre else None

    backlog = [
        g for g in all_games
        if g.get("completion_status") in ("Plan to Play", "Not Played")
        and (g.get("playtime_hours") or 0) < 2.0
        and (not platform or platform.lower() in g.get("platform", "").lower())
        and (is_game_pass is None or bool(g.get("is_game_pass")) == bool(is_game_pass))
        and (is_play_anywhere is None or bool(g.get("is_play_anywhere")) == bool(is_play_anywhere))
        and (
            not o_q
            or (g.get("ownership_status") or "Owned").lower() == o_q
            or (o_q in [s.lower() for s in (g.get("ownership_statuses") or [])])
        )
        and (not g_q or any(g_q in gen.lower() for gen in (g.get("genres") or [])))
        and (
            not t_q
            or any(t_q in t.lower() for t in (g.get("tags") or []))
            or any(t_q in gen.lower() for gen in (g.get("genres") or []))
        )
    ]

    # Prioritize 'Plan to Play' and higher critic scores
    backlog.sort(
        key=lambda g: (
            1 if g.get("completion_status") == "Plan to Play" else 0,
            g.get("critic_score") or 0,
        ),
        reverse=True,
    )

    return {
        "taste_profile_top_games": [
            {
                "title": g["title"],
                "platform": g["platform"],
                "ownership_status": g.get("ownership_status", "Owned"),
                "is_game_pass": bool(g.get("is_game_pass")),
                "playtime_hours": g["playtime_hours"],
                "user_rating": g["user_rating"],
                "genres": g["genres"],
                "tags": g.get("tags") or [],
                "favorite": g["favorite"],
            }
            for g in favorites_and_top
        ],
        "currently_playing": [
            {
                "title": g["title"],
                "platform": g["platform"],
                "ownership_status": g.get("ownership_status", "Owned"),
                "is_game_pass": bool(g.get("is_game_pass")),
                "playtime_hours": g["playtime_hours"],
                "last_played": g["last_played"],
                "notes": g["notes"],
            }
            for g in currently_playing[:10]
        ],
        "backlog_candidates": [
            {
                "title": g["title"],
                "platform": g["platform"],
                "ownership_status": g.get("ownership_status", "Owned"),
                "is_game_pass": bool(g.get("is_game_pass")),
                "status": g["completion_status"],
                "critic_score": g["critic_score"],
                "genres": g["genres"],
                "tags": g.get("tags") or [],
                "notes": g["notes"],
            }
            for g in backlog[:max_candidates]
        ],
    }


@mcp.tool()
def update_game_genres(
    title: str,
    genres: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    mode: str = "merge",
    remove_genres: Optional[List[str]] = None,
    remove_tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Update a game's genres and/or tags by matching its title.
    Genres are broad gameplay categories (1-3 per game, e.g. 'Shooter',
    'RPG', 'Puzzle'). Tags are mechanics, modes, themes, and settings
    (e.g. 'Stealth', 'Open World', 'Co-op', 'Survival Horror').

    mode defaults to 'merge': the given genres/tags are added to the
    game's existing lists and nothing is removed. This is the safe
    default, enrichment and syncs must never drop a manual tag.
    Use mode='replace' only for an explicit full replacement of the
    named field. Labels outside the controlled taxonomy in taxonomy.py
    are ignored and reported in 'ignored'.
    """
    matches = query_games(search=title, limit=5)
    if not matches:
        return {"error": f"No game found matching '{title}'"}

    # Pick exact match first, otherwise top match
    target = next((m for m in matches if m["title"].lower() == title.strip().lower()), matches[0])

    if mode not in ("merge", "replace"):
        return {"error": "mode must be 'merge' or 'replace'"}

    ignored: List[str] = []
    for label in genres or []:
        if not validate_genres([label]):
            ignored.append(label)
    for label in tags or []:
        if not validate_tags([label]):
            ignored.append(label)

    valid_genres = validate_genres(genres)
    valid_tags = validate_tags(tags)
    # For removals, allow any string so users/agents can clean up
    # retired, legacy, or custom labels without taxonomy restriction
    remove_genres_to_apply = [str(r).strip() for r in (remove_genres or []) if r]
    remove_tags_to_apply = [str(r).strip() for r in (remove_tags or []) if r]

    updates: Dict[str, Any] = {}
    if genres is not None:
        current = target.get("genres") or []
        if mode == "merge":
            updates["genres"] = merge_lists(current, valid_genres)
        else:
            updates["genres"] = valid_genres
        if remove_genres_to_apply:
            updates["genres"] = remove_from_list(updates["genres"], remove_genres_to_apply)
    elif remove_genres_to_apply:
        updates["genres"] = remove_from_list(target.get("genres") or [], remove_genres_to_apply)

    if tags is not None:
        current_tags = target.get("tags") or []
        if mode == "merge":
            updates["tags"] = merge_lists(current_tags, valid_tags)
        else:
            updates["tags"] = valid_tags
        if remove_tags_to_apply:
            updates["tags"] = remove_from_list(updates["tags"], remove_tags_to_apply)
    elif remove_tags_to_apply:
        updates["tags"] = remove_from_list(target.get("tags") or [], remove_tags_to_apply)

    if not updates:
        return {"error": "Nothing to update: pass genres, tags, remove_genres, or remove_tags"}

    updated = update_game(target["id"], updates)
    return {
        "status": "updated",
        "mode": mode,
        "game": updated,
        "ignored": ignored,
    }


@mcp.tool()
def update_game_progress(
    title: str,
    completion_status: Optional[str] = None,
    ownership_status: Optional[str] = None,
    is_game_pass: Optional[bool] = None,
    is_play_anywhere: Optional[bool] = None,
    user_rating: Optional[int] = None,
    notes: Optional[str] = None,
    favorite: Optional[bool] = None,
    cover_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update a game's completion status ('Not Played', 'Plan to Play', 'Playing', 'Beaten', 'Completed', 'Dropped'),
    ownership_status ('Owned', 'Subscription', 'Borrowed', 'None'),
    is_game_pass (true/false), is_play_anywhere (true/false), user rating (1-10), personal notes, favorite flag, or cover art URL by matching its title.
    """
    matches = query_games(search=title, limit=5)
    if not matches:
        return {"error": f"No game found matching '{title}'"}

    # Pick exact match first, otherwise top match
    target = next((m for m in matches if m["title"].lower() == title.strip().lower()), matches[0])
    updates: Dict[str, Any] = {}
    if completion_status is not None:
        updates["completion_status"] = completion_status
    if ownership_status is not None:
        updates["ownership_status"] = normalize_ownership_status(ownership_status)
    if is_game_pass is not None:
        updates["is_game_pass"] = bool(is_game_pass)
    if is_play_anywhere is not None:
        updates["is_play_anywhere"] = bool(is_play_anywhere)
    if user_rating is not None:
        updates["user_rating"] = user_rating
    if notes is not None:
        updates["notes"] = notes
    if favorite is not None:
        updates["favorite"] = favorite
    if cover_url is not None:
        trimmed_cover = cover_url.strip()
        if trimmed_cover.startswith("http://") or trimmed_cover.startswith("https://"):
            updates["cover_url"] = trimmed_cover

    updated = update_game(target["id"], updates)
    return {"status": "updated", "game": updated}


@mcp.tool()
def update_game_ownership(
    title: Optional[str] = None,
    ownership_status: str = "Owned",
    is_game_pass: Optional[bool] = None,
    is_play_anywhere: Optional[bool] = None,
    platform: Optional[str] = None,
    titles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Update and lock the ownership/access status ('Owned', 'Subscription', 'Borrowed', 'None')
    and/or toggle the Game Pass catalog flag for a single game title or a batch list of titles.
    - title: Single game title to update
    - titles: Optional batch list of game titles to update with the same ownership_status & platform
    - ownership_status: 'Owned', 'Subscription', 'Borrowed', or 'None'
    - is_game_pass: Optional boolean flag indicating whether the title is available on Xbox Game Pass
    - platform: Optional platform filter (e.g. 'Xbox', 'Steam', 'PlayStation') to target a specific
      platform copy when a game is reconciled across multiple platforms.
    """
    norm_own = normalize_ownership_status(ownership_status)
    target_titles: List[str] = []
    if title and title.strip():
        target_titles.append(title.strip())
    for t in titles or []:
        if t and t.strip() and t.strip() not in target_titles:
            target_titles.append(t.strip())

    if not target_titles:
        return {"error": "Provide 'title' or 'titles' to update ownership."}

    updated_games: List[Dict[str, Any]] = []
    not_found: List[str] = []

    for t_query in target_titles:
        matches = query_games(search=t_query, limit=5)
        if not matches:
            not_found.append(t_query)
            continue

        target = next((m for m in matches if m["title"].lower() == t_query.lower()), matches[0])
        target_id = target["id"]

        # If platform is specified and game has linked_games, target the matching platform copy
        if platform and target.get("linked_games"):
            p_lower = platform.strip().lower()
            plat_copy = next(
                (
                    lg
                    for lg in target["linked_games"]
                    if p_lower in (lg.get("platform") or "").lower()
                    or p_lower in (lg.get("source") or "").lower()
                ),
                None,
            )
            if plat_copy:
                target_id = plat_copy["id"]
        elif not platform and target.get("linked_games") and len(target["linked_games"]) > 1:
            if is_game_pass or norm_own == "Subscription":
                xbox_copy = next(
                    (
                        lg
                        for lg in target["linked_games"]
                        if "xbox" in (lg.get("platform") or "").lower()
                        or lg.get("source") == "Xbox"
                    ),
                    None,
                )
                if xbox_copy:
                    target_id = xbox_copy["id"]

        patch_dict: Dict[str, Any] = {"ownership_status": norm_own}
        if is_game_pass is not None:
            patch_dict["is_game_pass"] = bool(is_game_pass)
        if is_play_anywhere is not None:
            patch_dict["is_play_anywhere"] = bool(is_play_anywhere)

        res = update_game(target_id, patch_dict)
        if res:
            updated_games.append(
                {
                    "id": res["id"],
                    "title": res["title"],
                    "platform": res["platform"],
                    "ownership_status": res.get("ownership_status"),
                    "is_game_pass": res.get("is_game_pass"),
                    "is_play_anywhere": res.get("is_play_anywhere"),
                    "linked_games": res.get("linked_games"),
                }
            )

    return {
        "status": "updated",
        "ownership_status": norm_own,
        "is_game_pass": is_game_pass,
        "is_play_anywhere": is_play_anywhere,
        "updated_count": len(updated_games),
        "updated_games": updated_games,
        "not_found": not_found,
    }


@mcp.tool()
async def add_game_to_library(
    title: str,
    platform: str = "Nintendo Switch",
    completion_status: str = "Plan to Play",
    ownership_status: str = "Owned",
    is_game_pass: bool = False,
    is_play_anywhere: bool = False,
    playtime_hours: float = 0.0,
    user_rating: Optional[int] = None,
    genres: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    notes: str = "",
    cover_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Add a new game (such as a Nintendo Switch, PS4, Xbox, or PC title) to the library.
    - ownership_status: 'Owned', 'Subscription', 'Borrowed', or 'None'
    - is_game_pass: boolean flag for Xbox Game Pass availability
    - is_play_anywhere: boolean flag for Xbox Play Anywhere capability
    - cover_url: optional http(s) URL for cover art; auto-fills from external search if omitted
    """
    ignored: List[str] = []
    for label in genres or []:
        if not validate_genres([label]):
            ignored.append(label)
    for label in tags or []:
        if not validate_tags([label]):
            ignored.append(label)

    valid_cover_url: Optional[str] = None
    if cover_url is not None:
        trimmed = cover_url.strip()
        if trimmed and (trimmed.startswith("http://") or trimmed.startswith("https://")):
            valid_cover_url = trimmed

    if valid_cover_url is None and (cover_url is None or not cover_url.strip()):
        try:
            candidates = await search_external_metadata(title)
            valid_cover_url = pick_best_cover_art(candidates, title)
        except Exception as exc:
            logger.debug("Auto-fill cover search failed for '%s': %s", title, exc)
            valid_cover_url = None

    game = add_manual_game(
        title=title,
        platform=platform,
        completion_status=completion_status,
        ownership_status=normalize_ownership_status(ownership_status),
        is_game_pass=is_game_pass,
        is_play_anywhere=is_play_anywhere,
        playtime_hours=playtime_hours,
        user_rating=user_rating,
        genres=validate_genres(genres),
        tags=validate_tags(tags),
        cover_url=valid_cover_url,
        notes=notes,
    )
    res: Dict[str, Any] = {"status": "added", "game": game}
    if ignored:
        res["ignored"] = ignored
    return res


@mcp.tool()
def find_duplicate_cross_platform_games(cross_platform_only: bool = False) -> Dict[str, Any]:
    """
    Find candidate groups of games across Steam, Xbox, PlayStation, or Switch that appear to be the same game
    so their playtimes and statuses can be reconciled.
    """
    candidates = get_reconcile_candidates(cross_platform_only=cross_platform_only)
    return {"count": len(candidates), "candidates": candidates}


@mcp.tool()
def reconcile_cross_platform_games(
    game_ids: List[int],
    custom_title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Confirm that 2 or more game IDs across different platforms/editions are the same game,
    consolidating their playtime hours and unifying their completion status and metadata.
    """
    reconciled = reconcile_games(game_ids=game_ids, custom_title=custom_title)
    return {"status": "reconciled", "game": reconciled}


@mcp.tool()
async def enrich_library_genres(limit: int = 500, force: bool = False) -> Dict[str, Any]:
    """
    Automatically enrich games missing genres, release years, or critic scores using keyless
    Steam Store appdetails and Wikidata/Wikipedia fallback.
    Merge, never replace: discovered genres are added to a game's
    existing genres; existing genres are never dropped, even with force=True.
    """
    return _sanitize_output(await enrich_missing_metadata(limit=limit, force=force))


@mcp.tool()
async def get_game_achievements_detail(title: str) -> Dict[str, Any]:
    """
    Retrieve full achievement and trophy details for a game, including cross-platform non-overlapping
    unlocked counts and which platform each individual achievement was unlocked on (Steam, Xbox, PSN).
    """
    matches = query_games(search=title, limit=5)
    if not matches:
        return {"error": f"No game found matching '{title}'"}
    target = next((m for m in matches if m["title"].lower() == title.strip().lower()), matches[0])
    res = await sync_game_achievements_on_demand(target["id"])
    res["game_title"] = target["title"]
    res["game_id"] = target["id"]
    res["platform"] = target["platform"]
    return _sanitize_output(res)


@mcp.tool()
def respond_to_playtime_prompt(
    prompt_id: int,
    action: str = "accept",
) -> Dict[str, Any]:
    """
    Resolve a pending playtime increase prompt detected during library synchronization.
    - prompt_id: ID from get_gaming_profile_and_metrics()['playtime_prompts']
    - action: 'accept' (marks game as 'Playing'), 'dismiss' (keep current status for now),
              or 'mute' (never ask again for this game)
    """
    if action not in ("accept", "dismiss", "mute"):
        return {"error": "action must be 'accept', 'dismiss', or 'mute'"}
    res = resolve_playtime_prompt(prompt_id=prompt_id, action=action)
    if not res:
        return {"error": f"No pending playtime prompt found with id {prompt_id}"}
    return {"status": "resolved", "result": res}


# ==============================================================================
# Multi-Platform Wishlist, Price Monitoring & Smart Upgrade Tools & Resources
# ==============================================================================

@mcp.tool()
async def search_game_candidates(query: str, include_editions: bool = True) -> Dict[str, Any]:
    """
    Search across local database and external game catalogs for candidates to add to the wishlist.
    Returns visual metadata (cover, release year, developer/publisher, genres), discovered editions,
    available platforms, and library ownership conflict status (already owned/played).
    """
    db_path = get_default_db_path()
    q = (query or "").strip()
    if not q:
        return {"query": query, "candidates": []}

    seen_norm = set()
    candidates: List[Dict[str, Any]] = []

    # 1. Search local canonical games
    try:
        with get_connection(db_path) as conn:
            local_rows = conn.execute(
                """
                SELECT * FROM canonical_games
                WHERE title LIKE ? OR normalized_title LIKE ?
                LIMIT 5
                """,
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
            for r in local_rows:
                norm = r["normalized_title"]
                if norm not in seen_norm:
                    seen_norm.add(norm)
                    c_dict = dict(r)
                    c_dict["genres"] = json.loads(c_dict.get("genres") or "[]")
                    editions = list_game_editions(c_dict["id"], db_path=db_path) if include_editions else []
                    conflict = check_library_ownership_conflicts(c_dict["title"], canonical_game_id=c_dict["id"], db_path=db_path)
                    candidates.append({
                        "title": c_dict["title"],
                        "normalized_title": norm,
                        "release_year": c_dict.get("release_year"),
                        "developer": c_dict.get("developer") or "",
                        "publisher": c_dict.get("publisher") or "",
                        "cover_url": c_dict.get("cover_url"),
                        "genres": c_dict.get("genres", []),
                        "critic_score": c_dict.get("critic_score"),
                        "editions": editions,
                        "available_platforms": ["PC", "Xbox", "PlayStation"],
                        "ownership_conflict": conflict,
                        "source": "local_catalog",
                    })
    except Exception as exc:
        logger.debug("Local canonical games search error: %s", exc)

    # 2. Search external metadata
    try:
        external_results = await search_external_metadata(q)
        for ext in external_results:
            title = ext.get("title") or ""
            norm = normalize_game_title(title)
            if norm and norm not in seen_norm:
                seen_norm.add(norm)
                conflict = check_library_ownership_conflicts(title, db_path=db_path)
                std_tier, std_rank = infer_edition_tier("Standard Edition")
                editions = [
                    {
                        "edition_name": "Standard Edition",
                        "edition_tier": std_tier,
                        "tier_rank": std_rank,
                        "is_base_edition": True,
                    }
                ] if include_editions else []
                candidates.append({
                    "title": title,
                    "normalized_title": norm,
                    "release_year": ext.get("release_year"),
                    "developer": ext.get("developer", ""),
                    "publisher": ext.get("publisher", ""),
                    "cover_url": ext.get("cover_url"),
                    "genres": ext.get("genres", []),
                    "critic_score": ext.get("critic_score"),
                    "editions": editions,
                    "available_platforms": ["PC", "Xbox", "PlayStation"],
                    "ownership_conflict": conflict,
                    "source": ext.get("provider", "external"),
                })
    except Exception as exc:
        logger.warning("External metadata search failed for query '%s': %s", q, exc)

    return _sanitize_output({"query": q, "candidates": candidates[:10]})


@mcp.tool()
async def add_to_wishlist(
    title: str,
    platform_targets: List[Dict[str, Any]],
    release_year: Optional[int] = None,
    developer: str = "",
    publisher: str = "",
    cover_url: Optional[str] = None,
    allow_smart_upgrade: bool = True,
    editions: Optional[List[Dict[str, Any]]] = None,
    notes: str = "",
    force_track_owned: bool = False,
) -> Dict[str, Any]:
    """
    Add a game title to the multi-platform wishlist with granular per-platform USD target thresholds.
    Cross-checks the user's library and automatically mutes tracking on platforms already owned
    or played unless force_track_owned=True.
    """
    db_path = get_default_db_path()
    item = add_wishlist_item(
        title=title,
        platform_targets=platform_targets,
        release_year=release_year,
        developer=developer,
        publisher=publisher,
        cover_url=cover_url,
        allow_smart_upgrade=allow_smart_upgrade,
        notes=notes,
        added_via="mcp",
        force_track_owned=force_track_owned,
        db_path=db_path,
    )

    if editions and item and item.get("canonical_game"):
        c_id = item["canonical_game"]["id"]
        for ed in editions:
            ed_name = ed.get("edition_name") or "Standard Edition"
            ed_obj = upsert_game_edition(
                canonical_game_id=c_id,
                edition_name=ed_name,
                edition_tier=ed.get("edition_tier"),
                tier_rank=ed.get("tier_rank"),
                is_base_edition=ed.get("is_base_edition"),
                cover_url=ed.get("cover_url"),
                db_path=db_path,
            )
            for sku in ed.get("skus") or []:
                upsert_edition_sku(
                    edition_id=ed_obj["id"],
                    platform_family=sku.get("platform_family") or "PC",
                    platform_label=sku.get("platform_label") or "PC",
                    storefront=sku.get("storefront") or "steam",
                    store_sku_id=sku.get("store_sku_id") or "sku",
                    store_url=sku.get("store_url") or "",
                    current_price_usd=sku.get("current_price_usd"),
                    regular_price_usd=sku.get("regular_price_usd"),
                    db_path=db_path,
                )
        item = get_wishlist_item(item["id"], db_path=db_path)
    else:
        try:
            await wishlist_sync.discover_skus_for_item(item["id"], db_path=db_path)
            refreshed = get_wishlist_item(item["id"], db_path=db_path)
            if refreshed:
                item = refreshed
        except Exception:
            pass

    deals = evaluate_wishlist_deals(wishlist_item_id=item["id"], db_path=db_path) if item else []
    return _sanitize_output({
        "status": "added",
        "wishlist_item": item,
        "deals": deals,
    })


@mcp.tool()
def update_wishlist_platforms(
    wishlist_item_id: Optional[int] = None,
    title: Optional[str] = None,
    platform_targets: Optional[List[Dict[str, Any]]] = None,
    remove_platforms: Optional[List[str]] = None,
    allow_smart_upgrade: Optional[bool] = None,
    force_track_owned: bool = False,
) -> Dict[str, Any]:
    """
    Update monitored platforms, target USD prices, or toggle Smart Upgrade detection
    for a wishlist title.
    """
    db_path = get_default_db_path()
    item_id = wishlist_item_id
    if item_id is None and title:
        existing = get_wishlist_item_by_title(title, db_path=db_path)
        if existing:
            item_id = existing["id"]

    if item_id is None:
        return {"error": "Must provide either wishlist_item_id or valid title"}

    updated = db_update_wishlist_platforms(
        wishlist_item_id=item_id,
        platform_targets=platform_targets,
        remove_platforms=remove_platforms,
        allow_smart_upgrade=allow_smart_upgrade,
        force_track_owned=force_track_owned,
        db_path=db_path,
    )
    deals = evaluate_wishlist_deals(wishlist_item_id=item_id, db_path=db_path)
    return _sanitize_output({"status": "updated", "wishlist_item": updated, "deals": deals})


@mcp.tool()
def update_item_price(
    price_usd: float,
    platform: str,
    storefront: str = "manual",
    wishlist_item_id: Optional[int] = None,
    title: Optional[str] = None,
    edition_name: str = "Standard Edition",
    edition_tier: str = "base",
    regular_price_usd: Optional[float] = None,
    store_url: str = "",
    store_sku_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Report or record a price for a wishlist item edition on a specific storefront/platform.
    Updates the edition SKU, appends to price history, checks for All-Time Lows (ATL),
    and evaluates Smart Upgrade deals.
    """
    db_path = get_default_db_path()
    item_id = wishlist_item_id
    if item_id is None and title:
        existing = get_wishlist_item_by_title(title, db_path=db_path)
        if existing:
            item_id = existing["id"]

    if item_id is None:
        return {"error": "Must provide either wishlist_item_id or valid title"}

    item = get_wishlist_item(item_id, db_path=db_path)
    if not item or not item.get("canonical_game"):
        return {"error": f"Wishlist item {item_id} not found"}

    c_id = item["canonical_game"]["id"]
    ed = upsert_game_edition(
        canonical_game_id=c_id,
        edition_name=edition_name,
        edition_tier=edition_tier,
        db_path=db_path,
    )

    fam = normalize_platform_family(platform)
    sku_id_str = store_sku_id or f"{storefront}:{ed['id']}:{fam.lower()}"
    sku = upsert_edition_sku(
        edition_id=ed["id"],
        platform_family=fam,
        platform_label=platform,
        storefront=storefront,
        store_sku_id=sku_id_str,
        store_url=store_url,
        current_price_usd=price_usd,
        regular_price_usd=regular_price_usd,
        db_path=db_path,
    )

    price_rec = record_sku_price(
        sku_id=sku["id"],
        price_usd=price_usd,
        regular_price_usd=regular_price_usd,
        source="mcp_agent",
        db_path=db_path,
    )

    # Refresh sku from DB so current_price_usd, historical_low, etc. are reflected
    with get_connection(db_path) as conn:
        updated_row = conn.execute("SELECT * FROM edition_platform_skus WHERE id = ?", (sku["id"],)).fetchone()
        if updated_row:
            sku = dict(updated_row)
            sku["is_authorized_seller"] = bool(sku.get("is_authorized_seller"))
            sku["is_drm_free"] = bool(sku.get("is_drm_free"))

    deals = evaluate_wishlist_deals(wishlist_item_id=item_id, include_above_target=True, db_path=db_path)
    return _sanitize_output({
        "status": "price_recorded",
        "sku": sku,
        "is_all_time_low": price_rec["is_all_time_low"],
        "historical_low_usd": price_rec["historical_low_usd"],
        "deals": deals,
    })


@mcp.tool()
def get_wishlist_deals(
    platform: Optional[str] = None,
    smart_upgrades_only: bool = False,
    include_above_target: bool = False,
) -> Dict[str, Any]:
    """
    Retrieve active wishlist deals meeting target USD thresholds.
    Highlights Smart Upgrades where higher-tier editions (Deluxe, Ultimate, Complete)
    drop to <= the user's base edition target price, and All-Time Lows (ATL).
    """
    db_path = get_default_db_path()
    deals = evaluate_wishlist_deals(include_above_target=include_above_target, db_path=db_path)

    if platform:
        fam = normalize_platform_family(platform)
        deals = [d for d in deals if d.get("platform_family") == fam or platform.lower() in (d.get("platform_label") or "").lower()]

    if smart_upgrades_only:
        deals = [d for d in deals if d.get("is_smart_upgrade")]

    smart_upgrades_count = sum(1 for d in deals if d.get("is_smart_upgrade"))
    atl_count = sum(1 for d in deals if d.get("is_all_time_low"))

    return _sanitize_output({
        "total_deals": len(deals),
        "smart_upgrades_count": smart_upgrades_count,
        "all_time_lows_count": atl_count,
        "deals": deals,
    })


@mcp.tool()
def mark_as_purchased(
    platform: str,
    wishlist_item_id: Optional[int] = None,
    title: Optional[str] = None,
    edition_name: Optional[str] = None,
    price_paid_usd: Optional[float] = None,
    completion_status: str = "Plan to Play",
) -> Dict[str, Any]:
    """
    Mark a wishlist item as purchased and migrate it directly into the user's active library
    with purchase metadata recorded in notes.
    """
    db_path = get_default_db_path()
    item_id = wishlist_item_id
    if item_id is None and title:
        existing = get_wishlist_item_by_title(title, db_path=db_path)
        if existing:
            item_id = existing["id"]

    if item_id is None:
        return {"error": "Must provide either wishlist_item_id or valid title"}

    res = mark_wishlist_item_purchased(
        wishlist_item_id=item_id,
        platform=platform,
        edition_name=edition_name,
        price_paid_usd=price_paid_usd,
        completion_status=completion_status,
        db_path=db_path,
    )
    return _sanitize_output({
        "status": "purchased",
        "wishlist_item_id": item_id,
        "library_game_id": res.get("library_game_id"),
        "platform": platform,
        "details": res,
    })


@mcp.tool()
def get_session_history(
    title: Optional[str] = None,
    platform: Optional[str] = None,
    days: int = 30,
    limit: int = 50,
) -> Dict[str, Any]:
    """
    Expose granular game sessions and daily playtime telemetry ledger to AI agents.
    Answers queries about exact play session timestamps, session duration sweet spots,
    streaks, AFK idle tax, and historical playtime breakdown by day or title.
    """
    db_path = get_default_db_path()
    analytics = get_sessions_analytics(
        db_path=db_path,
        days=days,
        limit=limit,
        title=title,
        platform=platform,
    )
    return _sanitize_output({
        "status": "ok",
        "filter": {
            "title": title,
            "platform": platform,
            "days": days,
            "limit": limit,
        },
        "kpis": analytics.get("kpis", {}),
        "session_histogram": analytics.get("session_histogram", {}),
        "circadian": analytics.get("circadian", {}),
        "recent_sessions": analytics.get("recent_sessions", []),
        "daily_heatmap_sample": (analytics.get("heatmap_365") or [])[-days:],
        "rotation_gantt": analytics.get("rotation_gantt", []),
        "pagination": analytics.get("pagination", {}),
    })


@mcp.tool()
def get_play_sessions(
    game_title: Optional[str] = None,
    platform: Optional[str] = None,
    days_back: int = 30,
    limit: int = 25,
) -> Dict[str, Any]:
    """
    Retrieve granular recorded play sessions across synced platforms.
    Filter by game title, platform (Steam, Xbox, PlayStation), and time window (days_back).
    Returns session timestamps, duration in minutes, achievements unlocked during session, and notes.
    """
    db_path = get_default_db_path()
    game_id = None
    if game_title:
        with get_connection(db_path) as conn:
            match = conn.execute(
                "SELECT id FROM games WHERE LOWER(title) = ? LIMIT 1",
                (game_title.strip().lower(),),
            ).fetchone()
            if not match:
                match = conn.execute(
                    "SELECT id FROM games WHERE LOWER(title) LIKE ? LIMIT 1",
                    (f"%{game_title.strip().lower()}%",),
                ).fetchone()
            if match:
                game_id = match["id"]
            else:
                return _sanitize_output({"sessions": [], "total": 0, "message": f"Game '{game_title}' not found in library"})

    res = db_get_play_sessions(
        game_id=game_id,
        platform=platform,
        days=days_back,
        limit=limit,
        db_path=db_path,
    )
    return _sanitize_output(res)


@mcp.tool()
def get_session_analytics(
    days_back: int = 30,
) -> Dict[str, Any]:
    """
    Retrieve aggregated play session analytics: playtime velocity, active gaming days,
    average session duration, and top most-played games in the specified window.
    """
    db_path = get_default_db_path()
    return _sanitize_output(get_play_session_analytics(days_back=days_back, db_path=db_path))


@mcp.tool()
def update_session_notes(
    session_id: int,
    notes: str,
) -> Dict[str, Any]:
    """
    Append or update user journal notes on a synced play session.
    Automatically locks the session (is_locked = 1) to prevent automated sync coalescence.
    """
    db_path = get_default_db_path()
    updated = update_play_session(session_id=session_id, notes=notes, db_path=db_path)
    if not updated:
        return _sanitize_output({"status": "error", "message": f"Session with id {session_id} not found"})
    return _sanitize_output({"status": "ok", "session": updated})


@mcp.resource("vertex://wishlist/active")
def resource_wishlist_active() -> str:
    """Active wishlist items, watched platform targets, and latest prices."""
    db_path = get_default_db_path()
    items = list_wishlist_items(status="active", db_path=db_path)
    return json.dumps(_sanitize_output(items), indent=2)


@mcp.resource("vertex://wishlist/{id}/history")
def resource_wishlist_history(id: str) -> str:
    """Historical price log, All-Time Lows, and edition SKU breakdown for a wishlist item."""
    db_path = get_default_db_path()
    try:
        w_id = int(id)
    except (ValueError, TypeError):
        return json.dumps({"error": f"Invalid wishlist ID '{id}'"})

    item = get_wishlist_item(w_id, db_path=db_path)
    if not item:
        return json.dumps({"error": f"Wishlist item {id} not found"})

    history_by_sku: Dict[int, List[Dict[str, Any]]] = {}
    for s in item.get("skus") or []:
        history_by_sku[s["id"]] = get_sku_price_history(s["id"], limit=50, db_path=db_path)

    res = {
        **item,
        "price_history_by_sku": history_by_sku,
    }
    return json.dumps(_sanitize_output(res), indent=2)


if __name__ == "__main__":
    mcp.run()

