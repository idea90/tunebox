"""Listening history and play-count stats."""
import json
import time
from typing import Any, Dict, List

from .connection import get_db


def add_history(track: Dict[str, Any]) -> None:
    conn = get_db()
    cursor = conn.cursor()
    video_id = track.get("videoId") or track.get("id")
    if not video_id:
        conn.close()
        return
        
    artist_name = ""
    artists = track.get("artists", [])
    if isinstance(artists, list) and artists:
        artist_name = ", ".join([a.get("name", "") if isinstance(a, dict) else str(a) for a in artists])
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
        
    now = int(time.time())
    
    # 1. Insert history entry
    cursor.execute("""
    INSERT INTO history (video_id, title, artist, artists_json, album, album_id, thumbnail, duration, duration_seconds, played_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        video_id, track.get("title", "Unknown"), artist_name, json.dumps(track.get("artists", [])),
        album_name, album_id, track.get("thumbnail", ""), track.get("duration", ""),
        track.get("duration_seconds", 0), now
    ))
    
    # 2. Update play count
    cursor.execute("""
    INSERT INTO play_counts (video_id, title, artist, play_count, last_played)
    VALUES (?, ?, ?, 1, ?)
    ON CONFLICT(video_id) DO UPDATE SET
        play_count = play_count + 1,
        last_played = ?
    """, (video_id, track.get("title", "Unknown"), artist_name, now, now))
    
    conn.commit()
    conn.close()

def get_history(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM history ORDER BY played_at DESC, id DESC LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    return [{
        "videoId": r["video_id"],
        "title": r["title"],
        "artist": r["artist"],
        "artists": json.loads(r["artists_json"]) if r["artists_json"] else [{"name": r["artist"]}],
        "album": {"name": r["album"], "id": r["album_id"]} if r["album"] else None,
        "thumbnail": r["thumbnail"],
        "duration": r["duration"],
        "duration_seconds": r["duration_seconds"],
        "played_at": r["played_at"]
    } for r in rows]

def clear_history() -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM history")
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def get_most_played(limit: int = 25) -> List[Dict[str, Any]]:
    """Most played songs, with the metadata (duration, cover, album...) of their latest play."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT pc.video_id, pc.title, pc.artist, pc.play_count, pc.last_played,
           h.artists_json, h.album, h.album_id, h.thumbnail, h.duration, h.duration_seconds
    FROM play_counts pc
    LEFT JOIN history h ON h.id = (SELECT MAX(id) FROM history WHERE video_id = pc.video_id)
    ORDER BY pc.play_count DESC, pc.last_played DESC
    LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{
        "videoId": r["video_id"],
        "title": r["title"],
        "artist": r["artist"],
        "artists": json.loads(r["artists_json"]) if r["artists_json"] else [{"name": r["artist"]}],
        "album": {"name": r["album"], "id": r["album_id"]} if r["album"] else None,
        "thumbnail": r["thumbnail"] or "",
        "duration": r["duration"] or "",
        "duration_seconds": r["duration_seconds"] or 0,
        "playCount": r["play_count"],
        "lastPlayed": r["last_played"],
    } for r in rows]


def get_recent_unique(limit: int = 6, scan: int = 80) -> List[Dict[str, Any]]:
    """Most recently played songs, each song once (history has one row per play)."""
    seen, out = set(), []
    for item in get_history(limit=scan):
        vid = item.get("videoId")
        if vid and vid not in seen:
            seen.add(vid)
            out.append(item)
            if len(out) >= limit:
                break
    return out
