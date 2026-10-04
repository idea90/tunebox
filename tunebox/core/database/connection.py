"""SQLite connection and schema."""
import sqlite3

from ...config import DB_FILE


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
