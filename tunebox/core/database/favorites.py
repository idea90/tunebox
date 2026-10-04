"""Favorites."""
import json
import time
from typing import Any, Dict, List

from .connection import get_db


def add_favorite(track: Dict[str, Any]) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    video_id = track.get("videoId") or track.get("id")
    if not video_id:
        conn.close()
        return False
    
    artist_name = ""
    artists = track.get("artists", [])
    if isinstance(artists, list) and artists:
        artist_name = ", ".join([a.get("name", "") if isinstance(a, dict) else str(a) for a in artists])
    elif isinstance(artists, str):
        artist_name = artists
    elif track.get("artist"):
        artist_name = track.get("artist")

    album_name = ""
    album_id = None
    album_val = track.get("album")
    if isinstance(album_val, dict):
        album_name = album_val.get("name", "")
        album_id = album_val.get("id")
    elif isinstance(album_val, str):
        album_name = album_val
        
    try:
        cursor.execute("""
        INSERT OR REPLACE INTO favorites (
            video_id, title, artist, artists_json, album, album_id, thumbnail, duration, duration_seconds, added_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            video_id,
            track.get("title", "Unknown"),
            artist_name,
            json.dumps(track.get("artists", [])),
            album_name,
            album_id,
            track.get("thumbnail", ""),
            track.get("duration", ""),
            track.get("duration_seconds", 0),
            int(time.time())
        ))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def remove_favorite(video_id: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM favorites WHERE video_id = ?", (video_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def is_favorite(video_id: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM favorites WHERE video_id = ?", (video_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def get_favorite_ids() -> set:
    """All favorited video ids in one query (avoids a DB hit per table row)."""
    conn = get_db()
    try:
        return {r[0] for r in conn.execute("SELECT video_id FROM favorites")}
    finally:
        conn.close()

def get_favorites(limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM favorites ORDER BY added_at DESC LIMIT ? OFFSET ?
    """, (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({
            "videoId": r["video_id"],
            "title": r["title"],
            "artist": r["artist"],
            "artists": json.loads(r["artists_json"]) if r["artists_json"] else [{"name": r["artist"]}],
            "album": {"name": r["album"], "id": r["album_id"]} if r["album"] else None,
            "thumbnail": r["thumbnail"],
            "duration": r["duration"],
            "duration_seconds": r["duration_seconds"],
            "added_at": r["added_at"]
        })
    return results
