"""
SQLite database for Tunebox, split by topic:

    connection  get_db / init_db (schema)
    favorites   liked songs
    playlists   custom playlists and their items
    history     listening history, play counts, most played
    downloads   index of downloaded files

Everything is re-exported here, so `from tunebox.core.database import add_favorite` keeps working.
"""
from .connection import get_db, init_db
from .favorites import add_favorite, remove_favorite, is_favorite, get_favorite_ids, get_favorites
from .playlists import (
    create_playlist, get_playlists, get_playlist, delete_playlist,
    add_track_to_playlist, add_tracks_to_playlist, remove_track_from_playlist,
)
from .history import add_history, get_history, clear_history, get_most_played, get_recent_unique
from .downloads import add_download, get_downloads, get_download_owner, remove_download

# Create the tables upon first import.
init_db()

__all__ = [
    "get_db", "init_db",
    "add_favorite", "remove_favorite", "is_favorite", "get_favorite_ids", "get_favorites",
    "create_playlist", "get_playlists", "get_playlist", "delete_playlist",
    "add_track_to_playlist", "add_tracks_to_playlist", "remove_track_from_playlist",
    "add_history", "get_history", "clear_history", "get_most_played", "get_recent_unique",
    "add_download", "get_downloads", "get_download_owner", "remove_download",
]
