"""
Personal Home shelves built from what you actually listen to.

    local_shelves()          instant, from your own database
    because_shelf(seed)      YouTube Music suggestions related to a song you played (network)
"""
from typing import Any, Dict, List, Optional

from .database import get_most_played, get_recent_unique
from .ytmusic import yt_client

MIN_PLAYS_FOR_FAVOURITES = 2     # a single play isn't a favourite yet
SHELF_SIZE = 6


def local_shelves() -> List[Dict[str, Any]]:
    """[{title, items}] from history and play counts. Empty for a new profile."""
    shelves: List[Dict[str, Any]] = []

    recent = get_recent_unique(SHELF_SIZE)
    if recent:
        shelves.append({"title": "Jump back in", "items": recent})

    top = [t for t in get_most_played(SHELF_SIZE) if (t.get("playCount") or 0) >= MIN_PLAYS_FOR_FAVOURITES]
    if top:
        shelves.append({"title": "Your most played", "items": top})
    return shelves


def seed_track() -> Optional[Dict[str, Any]]:
    """The song recommendations are based on: the one you played most recently."""
    recent = get_recent_unique(1)
    return recent[0] if recent else None


def because_shelf(seed: Dict[str, Any], exclude_ids: Optional[set] = None, size: int = SHELF_SIZE) -> Optional[Dict[str, Any]]:
    """\"Because you played X\": related songs from YouTube Music's radio, minus ones you already see."""
    vid = seed.get("videoId")
    if not vid:
        return None
    skip = set(exclude_ids or ()) | {vid}
    related = [t for t in yt_client.get_watch_playlist(vid, limit=size * 3) if t.get("videoId") not in skip]
    if not related:
        return None
    title = seed.get("title", "a song")
    if len(title) > 40:
        title = title[:39] + "…"
    return {"title": f"Because you played {title}", "items": related[:size]}
