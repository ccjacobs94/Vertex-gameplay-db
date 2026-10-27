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
    get_reconcile_candidates,
    init_db,
    query_games,
    reconcile_games,
    update_game,
)
from taxonomy import (
    merge_lists,
    remove_from_list,
    validate_genres,
    validate_tags,
)

from sync_engine import enrich_missing_metadata

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
    favorite_only: bool = False,
    sort_by: str = "playtime_desc",
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """
    Search and filter the user's unified game library.
    - platform: e.g. 'Steam', 'Xbox', 'PlayStation', 'Switch'
    - status: 'Not Played', 'Plan to Play', 'Playing', 'Beaten', 'Completed', 'Dropped'
    - sort_by: 'playtime_desc', 'last_played_desc', 'rating_desc', 'title_asc'
    - tag: e.g. 'Stealth', 'Open World', 'Co-op'. Matches tags and genres.
    """
    return query_games(
        search=query,
        platform=platform,
        status=status,
        genre=genre,
        tag=tag,
        favorite_only=favorite_only,
        sort_by=sort_by,
        limit=limit,
    )


@mcp.tool()
def get_what_to_play_recommendations(
    platform: Optional[str] = None,
    genre: Optional[str] = None,
    max_candidates: int = 20,
) -> Dict[str, Any]:
    """
    Returns taste-profile context (the user's highest-played and favorite games) alongside
    candidate games from their backlog ('Plan to Play' or 'Not Played' with < 1.5h playtime)
    and currently 'Playing' games so the AI can recommend what to play next.
    """
    all_games = query_games(limit=2000)

    favorites_and_top = sorted(
        all_games,
        key=lambda g: (1 if g.get("favorite") else 0, g.get("user_rating") or 0, g.get("playtime_hours") or 0),
        reverse=True,
    )[:12]

    currently_playing = [
        g for g in all_games
        if g.get("completion_status") == "Playing"
        and (not platform or platform.lower() in g.get("platform", "").lower())
    ]

    backlog = [
        g for g in all_games
        if g.get("completion_status") in ("Plan to Play", "Not Played")
        and (g.get("playtime_hours") or 0) < 2.0
        and (not platform or platform.lower() in g.get("platform", "").lower())
        and (not genre or any(genre.lower() in gen.lower() for gen in (g.get("genres") or [])))
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
    for label in (genres or []) + (tags or []):
        if label not in (validate_genres([label]) + validate_tags([label])):
            ignored.append(label)

    valid_genres = validate_genres(genres)
    valid_tags = validate_tags(tags)
    remove_genres_valid = validate_genres(remove_genres)
    remove_tags_valid = validate_tags(remove_tags)

    updates: Dict[str, Any] = {}
    if genres is not None:
        current = target.get("genres") or []
        if mode == "merge":
            updates["genres"] = merge_lists(current, valid_genres)
        else:
            updates["genres"] = valid_genres
        if remove_genres_valid:
            updates["genres"] = remove_from_list(updates["genres"], remove_genres_valid)
    elif remove_genres_valid:
        updates["genres"] = remove_from_list(target.get("genres") or [], remove_genres_valid)

    if tags is not None:
        current_tags = target.get("tags") or []
        if mode == "merge":
            updates["tags"] = merge_lists(current_tags, valid_tags)
        else:
            updates["tags"] = valid_tags
        if remove_tags_valid:
            updates["tags"] = remove_from_list(updates["tags"], remove_tags_valid)
    elif remove_tags_valid:
        updates["tags"] = remove_from_list(target.get("tags") or [], remove_tags_valid)

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
    user_rating: Optional[int] = None,
    notes: Optional[str] = None,
    favorite: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Update a game's completion status ('Not Played', 'Plan to Play', 'Playing', 'Beaten', 'Completed', 'Dropped'),
    user rating (1-10), personal notes, or favorite flag by matching its title.
    """
    matches = query_games(search=title, limit=5)
    if not matches:
        return {"error": f"No game found matching '{title}'"}

    # Pick exact match first, otherwise top match
    target = next((m for m in matches if m["title"].lower() == title.strip().lower()), matches[0])
    updates: Dict[str, Any] = {}
    if completion_status is not None:
        updates["completion_status"] = completion_status
    if user_rating is not None:
        updates["user_rating"] = user_rating
    if notes is not None:
        updates["notes"] = notes
    if favorite is not None:
        updates["favorite"] = favorite

    updated = update_game(target["id"], updates)
    return {"status": "updated", "game": updated}


@mcp.tool()
def add_game_to_library(
    title: str,
    platform: str = "Nintendo Switch",
    completion_status: str = "Plan to Play",
    playtime_hours: float = 0.0,
    user_rating: Optional[int] = None,
    genres: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Add a new game (such as a Nintendo Switch, PS4, Xbox, or PC title) to the library.
    """
    game = add_manual_game(
        title=title,
        platform=platform,
        completion_status=completion_status,
        playtime_hours=playtime_hours,
        user_rating=user_rating,
        genres=validate_genres(genres),
        tags=validate_tags(tags),
        notes=notes,
    )
    return {"status": "added", "game": game}


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


if __name__ == "__main__":
    mcp.run()
