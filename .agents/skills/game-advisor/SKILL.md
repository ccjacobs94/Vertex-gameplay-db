---
name: game-advisor
description: >-
  Use this skill when the user asks for video game recommendations, wants to decide what to play next,
  asks about their gaming history/playtime metrics across Xbox, PC, PS4, or Switch, or wants to update
  their backlog status or add games to Game Hub.
---

# Game Advisor & Library Manager Skill

Follow these steps to analyze the user's gaming library, make data-driven recommendations, or update their backlog.

## 1. Querying the Library & Metrics

Prefer using the `game-hub` MCP server tools if active (`get_gaming_profile_and_metrics`, `search_game_library`, `get_what_to_play_recommendations`, `update_game_progress`, `add_game_to_library`).

If MCP tools are not loaded in the current session, query the local REST API on `http://localhost:8095`:

- **Overview & Metrics**: `curl -s http://localhost:8095/api/stats`
- **Search / Filter Games**: `curl -s "http://localhost:8095/api/games?platform=Xbox&status=Plan+to+Play&sort_by=playtime_desc"`
- **Trigger Platform Sync**: `curl -s -X POST http://localhost:8095/api/sync`

## 2. Making "What Should I Play Next?" Recommendations

1. Call `get_what_to_play_recommendations` (or query `/api/stats` and `/api/games`).
2. Compare the user's top-played and highest-rated games (`taste_profile_top_games`) against their unplayed backlog (`Plan to Play` and `Not Played`).
3. Present 3–5 tailored recommendations explaining **why** each fits their taste profile, what platform they own it on, and any personal notes they saved.

## 3. Adding Games with Cover Art

When adding a new game (such as a Nintendo Switch or PS4 title):
1. First search `/api/search-external?q=<title>` to get the high-resolution `cover_url` and `release_year`.
2. Add the game via `POST http://localhost:8095/api/games` or the `add_game_to_library` MCP tool.
