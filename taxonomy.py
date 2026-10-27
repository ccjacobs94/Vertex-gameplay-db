"""
Controlled Genre / Tag taxonomy for Game Hub.

Genres answer "what do you do moment to moment" (1-3 per game).
Tags answer "how, where, and with whom" (0-6 per game).

Rules, verified against the live library on 2026-10-27:
- Merge, never replace. Automated enrichment adds to the existing
  list (union, order preserved); nothing is removed unless a human
  explicitly removes it.
- Steam Store appdetails is a coarse source: it has no Stealth genre
  (Stealth is a user tag, not an appdetails genre), and it lists both
  Portal 2 and Splinter Cell Blacklist as only Action / Adventure.
  Steam genres can suggest, they can never overwrite a manual tag.
- Labels below are the only values enrichment may add. Anything
  outside these lists is held for review, never written silently.
"""

from typing import List, Optional

# Genres: broad gameplay categories. Keep the existing strings where
# they work so existing searches and the web UI keep working.
GENRES = [
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

# Tags: mechanics, modes, themes, and settings. Stealth, Open World,
# Co-op, and Survival Horror live here, not in Genres, because the
# sources enrichment reads from cannot produce them as genres.
TAGS = [
    # Mechanics and modes
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
    # Themes and settings
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
    # Origin / audience, moved here from Genres
    "Indie",
    "Free To Play",
    "Casual",
]

# Labels that used to appear as genres but are not gameplay genres.
# Enrichment must never add these as genres. "Indie", "Free To Play",
# and "Casual" are valid Tags; "Utilities" is neither.
RETIRED_GENRES = ["Free To Play", "Utilities", "Casual", "Indie"]

# Case-insensitive lookup so "stealth", "Stealth", and "STEALTH" agree.
_GENRE_LOOKUP = {g.lower(): g for g in GENRES}
_TAG_LOOKUP = {t.lower(): t for t in TAGS}


def normalize_genre(raw_label: Optional[str]) -> Optional[str]:
    """Return the canonical Genre for a label, or None if unknown/retired."""
    if not raw_label:
        return None
    label = raw_label.strip()
    if not label:
        return None
    if label.lower() in {r.lower() for r in RETIRED_GENRES}:
        return None
    return _GENRE_LOOKUP.get(label.lower())


def normalize_tag(raw_label: Optional[str]) -> Optional[str]:
    """Return the canonical Tag for a label, or None if unknown."""
    if not raw_label:
        return None
    label = raw_label.strip()
    if not label:
        return None
    return _TAG_LOOKUP.get(label.lower())


def validate_genres(labels: Optional[List[str]]) -> List[str]:
    """Deduplicate (order preserved) and keep only known Genres."""
    valid: List[str] = []
    for label in labels or []:
        canon = normalize_genre(label)
        if canon and canon not in valid:
            valid.append(canon)
    return valid


def validate_tags(labels: Optional[List[str]]) -> List[str]:
    """Deduplicate (order preserved) and keep only known Tags."""
    valid: List[str] = []
    for label in labels or []:
        canon = normalize_tag(label)
        if canon and canon not in valid:
            valid.append(canon)
    return valid


def merge_lists(existing: Optional[List[str]], additions: Optional[List[str]]) -> List[str]:
    """Order-preserving union: existing items first, then new ones."""
    merged: List[str] = list(existing or [])
    for item in additions or []:
        if item not in merged:
            merged.append(item)
    return merged


def remove_from_list(existing: Optional[List[str]], removals: Optional[List[str]]) -> List[str]:
    """Return existing with every removal dropped, order preserved."""
    drop = {str(r).strip().lower() for r in removals or []}
    return [item for item in (existing or []) if item.strip().lower() not in drop]
