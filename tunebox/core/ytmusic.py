"""
YouTube Music API Service Wrapper for Tunebox
"""
import os
import time
from typing import Dict, Any, List, Optional
from ytmusicapi import YTMusic
from ..config import config, AUTH_FILE

class YTMusicClient:
    _instance = None
    _yt: Optional[YTMusic] = None
    last_error: str = ""
    authenticated: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(YTMusicClient, cls).__new__(cls)
            cls._instance.init_client()
        return cls._instance

    def init_client(self):
        """Use the account created by `tunebox login` if present, else anonymous access."""
        self.authenticated = False
        if AUTH_FILE.exists():
            try:
                self._yt = YTMusic(auth=str(AUTH_FILE))
                self.authenticated = True
                return
            except Exception as e:
                self.last_error = f"Saved login is invalid ({type(e).__name__}); using anonymous mode."
        self._yt = YTMusic()

    @property
    def yt(self) -> YTMusic:
        if self._yt is None:
            self.init_client()
        return self._yt

    def format_thumbnail(self, thumbnails: Any, video_id: Optional[str] = None) -> str:
        if isinstance(thumbnails, list) and len(thumbnails) > 0:
            thumb_url = thumbnails[-1].get("url", "")
            if thumb_url:
                return thumb_url
        elif isinstance(thumbnails, dict):
            thumb_url = thumbnails.get("url", "")
            if thumb_url:
                return thumb_url
        elif isinstance(thumbnails, str) and thumbnails:
            return thumbnails

        if video_id:
            return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        return ""

    def format_track(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize track item representation."""
        artists = item.get("artists", [])
        if isinstance(artists, str):
            artists = [{"name": artists, "id": None}]
        elif isinstance(artists, list):
            formatted_artists = []
            for a in artists:
                if isinstance(a, dict):
                    formatted_artists.append({
                        "name": a.get("name", "Unknown Artist"),
                        "id": a.get("id") or a.get("browseId")
                    })
                elif isinstance(a, str):
                    formatted_artists.append({"name": a, "id": None})
            artists = formatted_artists

        album = item.get("album")
        album_dict = None
        if isinstance(album, dict):
            album_dict = {
                "name": album.get("name", ""),
                "id": album.get("id") or album.get("browseId")
            }
        elif isinstance(album, str):
            album_dict = {"name": album, "id": None}

        # Radio / watch-playlist results call it "length"; search and library results call it "duration".
        duration_str = item.get("duration") or item.get("length") or ""
        duration_seconds = item.get("duration_seconds", 0)
        if not duration_seconds and duration_str and ":" in duration_str:
            parts = duration_str.split(":")
            try:
                if len(parts) == 2:
                    duration_seconds = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    duration_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except Exception:
                duration_seconds = 0

        v_id = item.get("videoId") or item.get("id", "")
        artist_display = ", ".join([a["name"] for a in artists]) if artists else (item.get("artist") or "Unknown Artist")

        return {
            "videoId": v_id,
            "title": item.get("title", "Unknown Title"),
            "artist": artist_display,
            "artists": artists,
            "album": album_dict,
            "duration": duration_str,
            "duration_seconds": duration_seconds,
            "thumbnail": self.format_thumbnail(item.get("thumbnails") or item.get("thumbnail"), video_id=v_id),
            "isExplicit": item.get("isExplicit", False),
            "views": item.get("views", ""),
            "type": "song"
        }

    def search(self, query: str, filter_type: Optional[str] = None, limit: int = 25) -> List[Dict[str, Any]]:
        """Search songs, albums, artists, playlists."""
        try:
            filter_arg = None
            if filter_type in ["songs", "videos", "albums", "artists", "playlists"]:
                filter_arg = filter_type
                
            results = self.yt.search(query, filter=filter_arg, limit=limit)
            formatted = []
            for item in results:
                result_type = item.get("resultType", "")
                if result_type in ["song", "video"] or "videoId" in item:
                    formatted.append(self.format_track(item))
                elif result_type == "album":
                    artists = item.get("artists", [])
                    artist_str = ", ".join([a.get("name", "") if isinstance(a, dict) else str(a) for a in artists])
                    formatted.append({
                        "type": "album",
                        "browseId": item.get("browseId"),
                        "title": item.get("title", "Unknown Album"),
                        "artist": artist_str,
                        "artists": artists,
                        "year": item.get("year"),
                        "thumbnail": self.format_thumbnail(item.get("thumbnails"))
                    })
                elif result_type == "artist":
                    formatted.append({
                        "type": "artist",
                        "browseId": item.get("browseId"),
                        "name": item.get("artist") or item.get("title"),
                        "title": item.get("artist") or item.get("title"),
                        "subscribers": item.get("subscribers"),
                        "thumbnail": self.format_thumbnail(item.get("thumbnails"))
                    })
                elif result_type == "playlist":
                    formatted.append({
                        "type": "playlist",
                        "browseId": item.get("browseId"),
                        "title": item.get("title"),
                        "author": item.get("author") or (item.get("artists", [{}])[0].get("name", "") if item.get("artists") else ""),
                        "itemCount": item.get("itemCount"),
                        "thumbnail": self.format_thumbnail(item.get("thumbnails"))
                    })
            self.last_error = ""
            return formatted
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            return []

    def get_home_feed(self) -> List[Dict[str, Any]]:
        """Fetch home feed categories, charts and shelves."""
        shelves = []
        
        # 1. First add Top Trending Songs shelf
        try:
            charts = self.get_charts(country="US")
            if charts.get("songs"):
                shelves.append({
                    "title": "Top Global Hits & Charts",
                    "items": charts["songs"][:10]
                })
        except Exception:
            pass

        # 2. Add Curated Shelves from get_home()
        try:
            home = self.yt.get_home(limit=4)
            for section in home:
                title = section.get("title", "")
                contents = section.get("contents", [])
                items = []
                for item in contents:
                    if "videoId" in item:
                        items.append(self.format_track(item))
                    elif "playlistId" in item or "browseId" in item:
                        pl_id = item.get("playlistId") or item.get("browseId")
                        items.append({
                            "type": "playlist",
                            "browseId": pl_id,
                            "title": item.get("title", "Playlist"),
                            "description": item.get("description", ""),
                            "thumbnail": self.format_thumbnail(item.get("thumbnails"))
                        })
                if items:
                    shelves.append({
                        "title": title,
                        "items": items[:8]
                    })
        except Exception:
            pass

        # Fallback if empty
        if not shelves:
            top_songs = self.search("Top Hits 2026", filter_type="songs", limit=12)
            if top_songs:
                shelves.append({
                    "title": "Recommended For You",
                    "items": top_songs
                })

        return shelves

    def get_charts(self, country: str = "US") -> Dict[str, Any]:
        """Fetch Top Charts."""
        try:
            charts = self.yt.get_charts(country=country)
            formatted_songs = []
            
            videos_data = charts.get("videos")
            if isinstance(videos_data, list) and len(videos_data) > 0:
                top_item = videos_data[0]
                pl_id = top_item.get("playlistId")
                if pl_id:
                    pl = self.get_playlist(pl_id)
                    formatted_songs = pl.get("tracks", [])

            if not formatted_songs:
                formatted_songs = self.search("Top Global Hits", filter_type="songs", limit=20)

            return {"songs": formatted_songs, "videos": [], "artists": []}
        except Exception:
            fallback = self.search("Top Global Hits", filter_type="songs", limit=20)
            return {"songs": fallback, "videos": [], "artists": []}

    def get_playlist(self, playlist_id: str) -> Dict[str, Any]:
        """Get playlist tracks and details."""
        try:
            data = self.yt.get_playlist(playlist_id, limit=100)
            tracks = []
            for item in data.get("tracks", []):
                if "videoId" in item or "id" in item:
                    tracks.append(self.format_track(item))
            return {
                "title": data.get("title", "Playlist"),
                "description": data.get("description", ""),
                "author": data.get("author", {}).get("name", "") if isinstance(data.get("author"), dict) else str(data.get("author", "")),
                "trackCount": data.get("trackCount", len(tracks)),
                "thumbnail": self.format_thumbnail(data.get("thumbnails")),
                "tracks": tracks
            }
        except Exception:
            return {"title": "Playlist", "tracks": []}

    def get_album(self, album_id: str) -> Dict[str, Any]:
        """Get album details and tracklist."""
        try:
            data = self.yt.get_album(album_id)
            tracks = []
            for item in data.get("tracks", []):
                tracks.append(self.format_track(item))
            return {
                "title": data.get("title", "Unknown Album"),
                "artist": ", ".join([a.get("name", "") for a in data.get("artists", [])]) if isinstance(data.get("artists"), list) else "",
                "year": data.get("year", ""),
                "trackCount": data.get("trackCount", len(tracks)),
                "duration": data.get("duration", ""),
                "thumbnail": self.format_thumbnail(data.get("thumbnails")),
                "tracks": tracks
            }
        except Exception:
            return {"title": "Album", "tracks": []}

    def get_watch_playlist(self, video_id: str, limit: int = 25) -> List[Dict[str, Any]]:
        """Get radio / autoplay recommendations related to a song."""
        try:
            watch_data = self.yt.get_watch_playlist(videoId=video_id, limit=limit)
            tracks = []
            for item in watch_data.get("tracks", []):
                v_id = item.get("videoId") or item.get("id")
                if v_id and v_id != video_id:
                    tracks.append(self.format_track(item))
            return tracks
        except Exception:
            return []

    # ------------------------------------------------------------ suggestions / artists / account

    def get_suggestions(self, query: str) -> List[str]:
        try:
            return [s for s in self.yt.get_search_suggestions(query) if isinstance(s, str)]
        except Exception:
            return []

    def get_artist(self, browse_id: str, name: str = "") -> Dict[str, Any]:
        """Artist page: top songs (every song when YouTube exposes the full list), albums and singles.

        YouTube sometimes answers with a page layout ytmusicapi can't parse (KeyError such as
        'musicImmersiveHeaderRenderer'), and not always for the same artist. So: retry once, and if the page
        is still unusable fall back to the artist's songs and albums from search (flagged "limited").
        """
        data, error = None, None
        for _ in range(2):
            try:
                data = self.yt.get_artist(browse_id)
                break
            except Exception as e:
                error = e
        if data is None:
            reason = f"{type(error).__name__}: {error}"[:200]
            page = self._artist_from_search(name)
            self.last_error = reason      # the fallback's own (successful) searches must not erase the real cause
            return page

        songs = data.get("songs") or {}
        tracks = [self.format_track(i) for i in songs.get("results", []) if i.get("videoId")]
        full_id = songs.get("browseId")
        if full_id:  # the artist page only shows ~5; this is the complete "Songs" playlist
            full = self.get_playlist(full_id).get("tracks", [])
            if full:
                tracks = full

        albums = []
        for key in ("albums", "singles"):
            for a in (data.get(key) or {}).get("results", []):
                if a.get("browseId"):
                    albums.append({
                        "type": "album", "browseId": a["browseId"], "title": a.get("title", "Unknown"),
                        "artist": f"{'Single' if key == 'singles' else 'Album'} - {a.get('year', '')}".strip(" -"),
                        "thumbnail": self.format_thumbnail(a.get("thumbnails")),
                    })
        return {"title": data.get("name", "Artist"), "description": (data.get("description") or "")[:300],
                "subscribers": data.get("subscribers", ""), "tracks": tracks, "albums": albums}

    def _artist_from_search(self, name: str) -> Dict[str, Any]:
        """Reduced artist page built from search results, used when YouTube's artist page can't be parsed."""
        result: Dict[str, Any] = {"title": name or "Artist", "tracks": [], "albums": [], "limited": True}
        if not name:
            return result
        needle = name.lower()

        def by_artist(item: Dict[str, Any]) -> bool:
            return needle in (item.get("artist") or "").lower()

        songs = self.search(name, filter_type="songs", limit=30)
        result["tracks"] = [s for s in songs if by_artist(s)] or songs
        result["albums"] = [a for a in self.search(name, filter_type="albums", limit=20) if by_artist(a)]
        return result

    def get_liked_tracks(self, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.authenticated:
            return []
        try:
            data = self.yt.get_liked_songs(limit=limit)
            return [self.format_track(i) for i in data.get("tracks", []) if i.get("videoId")]
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            return []

    def get_library_playlist_items(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.authenticated:
            return []
        try:
            return [{"type": "playlist", "browseId": p["playlistId"], "title": p.get("title", "Playlist"),
                     "author": f"{p.get('count', '')} songs".strip(), "thumbnail": self.format_thumbnail(p.get("thumbnails"))}
                    for p in self.yt.get_library_playlists(limit=limit) if p.get("playlistId")]
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            return []


yt_client = YTMusicClient()
