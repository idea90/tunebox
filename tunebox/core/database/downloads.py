"""Index of downloaded files."""
import os
import time
from typing import Any, Dict, List, Optional

from .connection import get_db


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
