"""Custom playlists and their items."""
import json
import time
from typing import Any, Dict, List, Optional

from .connection import get_db


def create_playlist(title: str, description: str = "") -> Dict[str, Any]:
    import uuid
    conn = get_db()
    cursor = conn.cursor()
    p_id = str(uuid.uuid4())[:8]
    now = int(time.time())
    
    cursor.execute("""
    INSERT INTO playlists (id, title, description, cover_url, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (p_id, title, description, "", now, now))
    conn.commit()
    conn.close()
    return {"id": p_id, "title": title, "description": description, "trackCount": 0}

def get_playlists() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT p.*, COUNT(pi.id) as track_count 
    FROM playlists p 
    LEFT JOIN playlist_items pi ON p.id = pi.playlist_id 
    GROUP BY p.id 
    ORDER BY p.updated_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    
    return [{
        "id": r["id"],
        "title": r["title"],
        "description": r["description"],
        "cover_url": r["cover_url"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "trackCount": r["track_count"]
    } for r in rows]

def get_playlist(playlist_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    p = cursor.fetchone()
    if not p:
        conn.close()
        return None
        
    cursor.execute("""
    SELECT * FROM playlist_items WHERE playlist_id = ? ORDER BY position ASC
    """, (playlist_id,))
    items = cursor.fetchall()
    conn.close()
    
    tracks = []
    for r in items:
        tracks.append({
            "videoId": r["video_id"],
            "title": r["title"],
            "artist": r["artist"],
            "artists": json.loads(r["artists_json"]) if r["artists_json"] else [{"name": r["artist"]}],
            "album": {"name": r["album"], "id": r["album_id"]} if r["album"] else None,
            "thumbnail": r["thumbnail"],
            "duration": r["duration"],
            "duration_seconds": r["duration_seconds"],
            "playlist_item_id": r["id"]
        })
        
    return {
        "id": p["id"],
        "title": p["title"],
        "description": p["description"],
        "cover_url": p["cover_url"],
        "created_at": p["created_at"],
        "updated_at": p["updated_at"],
        "tracks": tracks,
        "trackCount": len(tracks)
    }

def delete_playlist(playlist_id: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        cursor.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def add_track_to_playlist(playlist_id: str, track: Dict[str, Any]) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    video_id = track.get("videoId") or track.get("id")
    if not video_id:
        conn.close()
        return False
        
    cursor.execute("SELECT MAX(position) FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    max_pos_row = cursor.fetchone()
    next_pos = (max_pos_row[0] or 0) + 1
    
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
    try:
        cursor.execute("""
        INSERT INTO playlist_items (
            playlist_id, video_id, title, artist, artists_json, album, album_id, thumbnail, duration, duration_seconds, position, added_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            playlist_id, video_id, track.get("title", "Unknown"), artist_name,
            json.dumps(track.get("artists", [])), album_name, album_id,
            track.get("thumbnail", ""), track.get("duration", ""), track.get("duration_seconds", 0),
            next_pos, now
        ))
        cursor.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def remove_track_from_playlist(playlist_id: str, video_id: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND video_id = ?", (playlist_id, video_id))
        cursor.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (int(time.time()), playlist_id))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()
