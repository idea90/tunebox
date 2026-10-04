"""
SQLite Database manager for Tunebox:
- Favorites
- Custom Playlists
- Playlist Items
- Listening History
- Play Count Stats
- Offline Downloads Index
"""
import sqlite3
import json
import time
import os
from typing import List, Dict, Any, Optional
from ..config import DB_FILE

def get_db():
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Favorites table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS favorites (
        video_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        artist TEXT,
        artists_json TEXT,
        album TEXT,
        album_id TEXT,
        thumbnail TEXT,
        duration TEXT,
        duration_seconds INTEGER DEFAULT 0,
        added_at INTEGER NOT NULL
    )
    """)
    
    # Custom playlists table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS playlists (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        cover_url TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """)
    
    # Playlist items
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS playlist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        playlist_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        title TEXT NOT NULL,
        artist TEXT,
        artists_json TEXT,
        album TEXT,
        album_id TEXT,
        thumbnail TEXT,
        duration TEXT,
        duration_seconds INTEGER DEFAULT 0,
        position INTEGER NOT NULL,
        added_at INTEGER NOT NULL,
        FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
    )
    """)
    
    # History table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id TEXT NOT NULL,
        title TEXT NOT NULL,
        artist TEXT,
        artists_json TEXT,
        album TEXT,
        album_id TEXT,
        thumbnail TEXT,
        duration TEXT,
        duration_seconds INTEGER DEFAULT 0,
        played_at INTEGER NOT NULL
    )
    """)
    
    # Play counts stats
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS play_counts (
        video_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        artist TEXT,
        play_count INTEGER DEFAULT 1,
        last_played INTEGER NOT NULL
    )
    """)

    # Downloads table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS downloads (
        video_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        artist TEXT,
        album TEXT,
        file_path TEXT NOT NULL,
        file_size INTEGER DEFAULT 0,
        format TEXT DEFAULT 'mp3',
        duration TEXT,
        duration_seconds INTEGER DEFAULT 0,
        thumbnail TEXT,
        downloaded_at INTEGER NOT NULL
    )
    """)
    
    conn.commit()
    conn.close()

# Initialize upon module import
init_db()

# --- Favorites Helpers ---

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

# --- Playlists Helpers ---

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

# --- History & Stats Helpers ---

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

# --- Downloads Index ---

def add_download(track: Dict[str, Any], file_path: str, file_size: int, fmt: str = "mp3") -> bool:
    conn = get_db()
    cursor = conn.cursor()
    video_id = track.get("videoId") or track.get("id")
    if not video_id:
        conn.close()
        return False
    
    artist_name = track.get("artist") or ""
    if not artist_name and track.get("artists"):
        artist_name = ", ".join([a.get("name", "") if isinstance(a, dict) else str(a) for a in track.get("artists", [])])
        
    album_name = ""
    if isinstance(track.get("album"), dict):
        album_name = track.get("album", {}).get("name", "")
    elif isinstance(track.get("album"), str):
        album_name = track.get("album")
        
    now = int(time.time())
    try:
        cursor.execute("""
        INSERT OR REPLACE INTO downloads (
            video_id, title, artist, album, file_path, file_size, format, duration, duration_seconds, thumbnail, downloaded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            video_id, track.get("title", "Unknown"), artist_name, album_name,
            file_path, file_size, fmt, track.get("duration", ""),
            track.get("duration_seconds", 0), track.get("thumbnail", ""), now
        ))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def get_downloads() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM downloads ORDER BY downloaded_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        # Check if file still exists on disk
        if os.path.exists(r["file_path"]):
            results.append({
                "videoId": r["video_id"],
                "title": r["title"],
                "artist": r["artist"],
                "album": r["album"],
                "filePath": r["file_path"],
                "fileSize": r["file_size"],
                "format": r["format"],
                "duration": r["duration"],
                "duration_seconds": r["duration_seconds"],
                "thumbnail": r["thumbnail"],
                "downloaded_at": r["downloaded_at"]
            })
    return results

def get_download_owner(file_path: str) -> Optional[str]:
    """video_id of the download stored at this path (None if unknown)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT video_id FROM downloads WHERE file_path = ?", (file_path,)).fetchone()
        return row["video_id"] if row else None
    finally:
        conn.close()


def remove_download(video_id: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM downloads WHERE video_id = ?", (video_id,))
    row = cursor.fetchone()
    shared = False
    if row:
        # Older versions could save two songs to one file; never delete a file another entry still uses.
        cursor.execute("SELECT COUNT(*) FROM downloads WHERE file_path = ? AND video_id != ?", (row["file_path"], video_id))
        shared = cursor.fetchone()[0] > 0
    if row and not shared and os.path.exists(row["file_path"]):
        try:
            os.remove(row["file_path"])
        except Exception:
            pass
    cursor.execute("DELETE FROM downloads WHERE video_id = ?", (video_id,))
    conn.commit()
    conn.close()
    return True
