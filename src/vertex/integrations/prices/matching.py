"""Canonical title matching and edition tier extraction for storefront price discovery."""

from __future__ import annotations

import re
from typing import Any

from vertex.catalog.models import normalize_title

EDITION_KEYWORDS = frozenset(
    {
        "deluxe",
        "ultimate",
        "premium",
        "complete",
        "goty",
        "gold",
        "collector",
        "collectors",
        "standard",
        "special",
        "anniversary",
        "directors",
        "director",
        "cut",
        "bundle",
        "pack",
        "pass",
        "plus",
        "enhanced",
        "expanded",
        "remastered",
        "digital",
        "launch",
        "day",
        "one",
        "season",
        "edition",
        "year",
        "definitive",
        "voidheart",
        "super",
        "collection",
        "legacy",
        "royal",
        "online",
        "original",
        "remaster",
        "platinum",
        "silver",
        "classic",
        "legendary",
        "master",
    }
)

ADDON_EXCLUDE_WORDS = frozenset(
    {
        "upgrade",
        "soundtrack",
        "ost",
        "artbook",
        "wallpaper",
        "avatar",
        "skin",
        "outfit",
        "costume",
        "weapon",
        "item",
        "bonus",
        "preorder",
        "demo",
        "trial",
        "beta",
        "playtest",
        "server",
        "mod",
        "coin",
        "currency",
    }
)


def parse_usd_float(val: Any) -> float | None:
    """Parse a USD price value from float, int, or formatted string like '$59.99'."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    cleaned = re.sub(r"[^0-9.]", "", str(val))
    if not cleaned:
        return None
    try:
        return round(float(cleaned), 2)
    except (TypeError, ValueError):
        return None


def matches_canonical_title(canonical_title: str, candidate_title: str) -> bool:
    t_norm = normalize_title(canonical_title)
    c_norm = normalize_title(candidate_title)
    if not t_norm or not c_norm:
        return False
    if t_norm == c_norm:
        return True
    t_words = t_norm.split()
    c_words = c_norm.split()
    if t_words == c_words:
        return True

    if len(c_words) > len(t_words) and c_words[: len(t_words)] == t_words:
        extra = set(c_words[len(t_words) :])
        if extra & ADDON_EXCLUDE_WORDS:
            return False
        sequel_numbers = {w for w in extra if w.isdigit() and len(w) < 4}
        if sequel_numbers:
            return False
        if all(w in EDITION_KEYWORDS or (w.isdigit() and 1990 <= int(w) <= 2035) for w in extra):
            return True

    if len(t_words) > len(c_words) and t_words[: len(c_words)] == c_words:
        extra = set(t_words[len(c_words) :])
        sequel_numbers = {w for w in extra if w.isdigit() and len(w) < 4}
        if sequel_numbers:
            return False
        if all(w in EDITION_KEYWORDS or (w.isdigit() and 1990 <= int(w) <= 2035) for w in extra):
            return True

    return False


def extract_edition_name(
    candidate_title: str,
    canonical_title: str = "",
    default: str = "Standard Edition",
) -> str:
    title_norm = (canonical_title or "").lower()
    cand_lower = (candidate_title or "").lower()

    remainder = cand_lower
    if title_norm:
        for word in title_norm.split():
            clean_w = re.sub(r"[^a-z0-9]", "", word)
            if clean_w:
                remainder = re.sub(r"\b" + re.escape(clean_w) + r"\b", " ", remainder)

    if re.search(r"\bcollectors?\b", remainder):
        return "Collector's Edition"
    if re.search(r"\bultimate\b", remainder):
        return "Ultimate Edition"
    if re.search(r"\blegendary\b", remainder):
        return "Legendary Edition"
    if re.search(r"\bpremium\b", remainder):
        return "Premium Edition"
    if re.search(r"\b(goty|game of the year)\b", remainder):
        return "Game of the Year Edition"
    if re.search(r"\bcomplete\b", remainder):
        return "Complete Edition"
    if re.search(r"\bvoidheart\b", remainder):
        return "Complete Edition"
    if re.search(r"\bdefinitive\b", remainder):
        return "Definitive Edition"
    if re.search(r"\blegacy\b", remainder):
        return "Legacy Edition"
    if re.search(r"\bgold\b", remainder):
        return "Gold Edition"
    if re.search(r"\bdeluxe\b", remainder):
        return "Deluxe Edition"
    if re.search(r"\bdirector('?s)?(\s+cut)?\b", remainder):
        return "Director's Cut"
    if re.search(r"\banniversary\b", remainder):
        return "Anniversary Edition"
    if re.search(r"\benhanced\b", remainder):
        return "Enhanced Edition"
    if re.search(r"\bspecial\b", remainder):
        return "Special Edition"
    return default
