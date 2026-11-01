"""
Model Context Protocol (MCP) Server for Unified Game Hub.
Allows AI agents to inspect your gaming history, playtime metrics, platform breakdowns,
and backlog across PC (Steam), Xbox, PlayStation, and Nintendo Switch.
"""

from typing import Any, Dict, List, Optional

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

from db import (
    add_manual_game,
    get_library_stats,
    get_pending_playtime_prompts,
    get_reconcile_candidates,
    init_db,
    normalize_ownership_status,
    query_games,
    reconcile_games,
    resolve_playtime_prompt,
    update_game,
)
from taxonomy import (
    merge_lists,
    remove_from_list,
    validate_genres,
    validate_tags,
)

from sync_engine import enrich_missing_metadata, sync_game_achievements_on_demand

init_db()

mcp = FastMCP("GameHub-Library-Assistant")


@mcp.tool()
def get_gaming_profile_and_metrics() -> Dict[str, Any]:
    """
    Retrieve high-level gaming metrics, platform distribution (PC, Xbox, PS4/PS5, Switch),
    completion status breakdown, top genres, top 10 most-played games, and recently played titles.
    Use this first to understand the user's gaming habits and history.
    """
    return get_library_stats()


@mcp.tool()
def search_game_library(
    query: Optional[str] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    genre: Optional[str] = None,
    tag: Optional[str] = None,
    ownership_status: Optional[str] = None,
    is_game_pass: Optional[bool] = None,
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
    user_rating: Optional[int] = None,
    notes: Optional[str] = None,
    favorite: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Update a game's completion status ('Not Played', 'Plan to Play', 'Playing', 'Beaten', 'Completed', 'Dropped'),
    ownership_status ('Owned', 'Subscription', 'Borrowed', 'None'),
    is_game_pass (true/false), user rating (1-10), personal notes, or favorite flag by matching its title.
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
    if user_rating is not None:
        updates["user_rating"] = user_rating
    if notes is not None:
        updates["notes"] = notes
    if favorite is not None:
        updates["favorite"] = favorite

    updated = update_game(target["id"], updates)
    return {"status": "updated", "game": updated}


@mcp.tool()
def update_game_ownership(
    title: Optional[str] = None,
    ownership_status: str = "Owned",
    is_game_pass: Optional[bool] = None,
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

        res = update_game(target_id, patch_dict)
        if res:
            updated_games.append(
                {
                    "id": res["id"],
                    "title": res["title"],
                    "platform": res["platform"],
                    "ownership_status": res.get("ownership_status"),
                    "is_game_pass": res.get("is_game_pass"),
                    "linked_games": res.get("linked_games"),
                }
            )

    return {
        "status": "updated",
        "ownership_status": norm_own,
        "is_game_pass": is_game_pass,
        "updated_count": len(updated_games),
        "updated_games": updated_games,
        "not_found": not_found,
    }


@mcp.tool()
def add_game_to_library(
    title: str,
    platform: str = "Nintendo Switch",
    completion_status: str = "Plan to Play",
    ownership_status: str = "Owned",
    is_game_pass: bool = False,
    playtime_hours: float = 0.0,
    user_rating: Optional[int] = None,
    genres: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Add a new game (such as a Nintendo Switch, PS4, Xbox, or PC title) to the library.
    - ownership_status: 'Owned', 'Subscription', 'Borrowed', or 'None'
    - is_game_pass: boolean flag for Xbox Game Pass availability
    """
    ignored: List[str] = []
    for label in genres or []:
        if not validate_genres([label]):
            ignored.append(label)
    for label in tags or []:
        if not validate_tags([label]):
            ignored.append(label)

    game = add_manual_game(
        title=title,
        platform=platform,
        completion_status=completion_status,
        ownership_status=normalize_ownership_status(ownership_status),
        is_game_pass=is_game_pass,
        playtime_hours=playtime_hours,
        user_rating=user_rating,
        genres=validate_genres(genres),
        tags=validate_tags(tags),
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
    return await enrich_missing_metadata(limit=limit, force=force)


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
    return res


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


if __name__ == "__main__":
    mcp.run()
