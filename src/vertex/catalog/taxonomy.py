"""Controlled Genre / Tag taxonomy retained verbatim from legacy taxonomy.py."""

from __future__ import annotations

from typing import Iterable

GENRES: list[str] = [
    "Action",
    "Adventure",
    "Action-Adventure",
    "RPG",
    "Action RPG",
    "Strategy",
    "Turn-Based Strategy",
    "Tactical RPG",
    "Tactical",
    "Simulation",
    "Sports",
    "Racing",
    "Horror",
    "Puzzle",
    "Platformer",
    "Puzzle Platformer",
    "FPS",
    "Third-Person Shooter",
    "Shooter",
    "Visual Novel",
    "Roguelike",
    "Metroidvania",
    "Soulslike",
    "Immersive Sim",
    "Interactive Drama",
    "Sandbox",
    "Survival",
    "Hack and Slash",
]

TAGS: list[str] = [
    "Stealth",
    "Open World",
    "Co-op",
    "Multiplayer",
    "Turn-Based",
    "Real-Time",
    "Crafting",
    "Exploration",
    "Puzzle",
    "Survival Horror",
    "Tower Defense",
    "Rhythm",
    "Detective",
    "Sci-Fi",
    "Fantasy",
    "Supernatural",
    "Post-Apocalyptic",
    "Superhero",
    "Space",
    "Horror",
    "Comedy",
    "Dark",
    "Psychological",
    "Story-Rich",
    "Indie",
    "Free To Play",
    "Casual",
]

RETIRED_GENRES: list[str] = ["Free To Play", "Utilities", "Casual", "Indie"]
_RETIRED_GENRES_SET = {r.lower() for r in RETIRED_GENRES}

_GENRE_LOOKUP = {g.lower(): g for g in GENRES}
_TAG_LOOKUP = {t.lower(): t for t in TAGS}


def normalize_genre(raw_label: str | None) -> str | None:
    if not raw_label or not isinstance(raw_label, str):
        return None
    label = raw_label.strip()
    if not label or label.lower() in _RETIRED_GENRES_SET:
        return None
    return _GENRE_LOOKUP.get(label.lower())


def normalize_tag(raw_label: str | None) -> str | None:
    if not raw_label or not isinstance(raw_label, str):
        return None
    label = raw_label.strip()
    if not label:
        return None
    return _TAG_LOOKUP.get(label.lower())


def validate_genres(labels: Iterable[str] | None) -> list[str]:
    valid: list[str] = []
    seen: set[str] = set()
    for label in labels or ():
        canon = normalize_genre(label)
        if canon and canon.lower() not in seen:
            seen.add(canon.lower())
            valid.append(canon)
    return valid


def validate_tags(labels: Iterable[str] | None) -> list[str]:
    valid: list[str] = []
    seen: set[str] = set()
    for label in labels or ():
        canon = normalize_tag(label)
        if canon and canon.lower() not in seen:
            seen.add(canon.lower())
            valid.append(canon)
    return valid


def merge_lists(existing: Iterable[str] | None, additions: Iterable[str] | None) -> list[str]:
    merged: list[str] = list(existing or ())
    seen = {str(x).strip().lower() for x in merged}
    for item in additions or ():
        if item and str(item).strip().lower() not in seen:
            seen.add(str(item).strip().lower())
            merged.append(item)
    return merged


def remove_from_list(existing: Iterable[str] | None, removals: Iterable[str] | None) -> list[str]:
    drop = {str(r).strip().lower() for r in (removals or ()) if r}
    return [item for item in (existing or ()) if str(item).strip().lower() not in drop]
