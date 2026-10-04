"""Loading and caching what the tabs show: Home shelves, search results, the Library lists and artist/album/playlist pages."""
from typing import Any, Dict, List

from rich.text import Text
from textual.widgets import Static, TabbedContent

from ...core import recommend
from ...core.database import get_favorites, get_history, get_most_played, get_playlist, get_playlists
from ...core.player import player
from ...core.ytmusic import yt_client
from ..widgets import TrackTable


class DataMixin:
    """Loading and caching what the tabs show: Home shelves, search results, the Library lists and artist/album/playlist pages."""

    def _load_home(self) -> None:
        shelves = yt_client.get_home_feed()
        items: List[Dict[str, Any]] = []
        for shelf in shelves[:3]:
            if shelf.get("items"):
                items.append({"type": "header", "title": shelf.get("title", "Explore")})
                items.extend(shelf["items"][:8])
        if not items:
            self._ui(self.say, yt_client.last_error or "Could not load the home feed (offline?).", True)
        self._ui(self._set_home, items)

    def _set_home(self, feed_items) -> None:
        self.feed_items = feed_items
        self.home_items = self._compose_home()
        self.refresh_tables()

    def _compose_home(self) -> List[Dict[str, Any]]:
        """Your own shelves first (instant, from the local database), then YouTube's."""
        shelves = list(recommend.local_shelves())
        if self._because:
            shelves.append(self._because)
        items: List[Dict[str, Any]] = []
        for shelf in shelves:
            items.append({"type": "header", "title": shelf["title"]})
            items.extend(shelf["items"])
        return items + self.feed_items

    def _refresh_because(self) -> None:
        """(worker) Fetch 'Because you played X' when the most recently played song has changed."""
        seed = recommend.seed_track()
        if not seed or seed.get("videoId") == self._because_seed:
            return
        shown = {t["videoId"] for sh in recommend.local_shelves() for t in sh["items"]}
        shelf = recommend.because_shelf(seed, shown)
        if shelf:                      # on failure leave the seed unset so the next visit to Home retries
            self._ui(self._set_because, seed["videoId"], shelf)

    def _set_because(self, seed_vid: str, shelf: Dict[str, Any]) -> None:
        self._because_seed, self._because = seed_vid, shelf
        self.home_items = self._compose_home()
        self._sig = None
        self.refresh_tables()

    def _do_search(self, query: str, filt: str, seq: int = 0) -> None:
        results = yt_client.search(query, filter_type=None if filt == "all" else filt)
        if not results and yt_client.last_error:
            self._ui(self.say, f"Search failed: {yt_client.last_error}", True)
        self._ui(self._set_search, query, results, seq)

    def _set_search(self, query: str, results, seq: int = 0) -> None:
        if seq and seq != self._search_seq:     # an older search finished after a newer one: drop it
            return
        self.search_query = query
        self.search_items = results
        if not results and not yt_client.last_error:
            self.say(f"No results for \"{query}\"")
        self.refresh_tables()
        self.query_one("#t-search", TrackTable).focus()

    def _library_items(self) -> List[Dict[str, Any]]:
        if self.library_sub == "favorites":
            return get_favorites()
        if self.library_sub == "history":
            return get_history(limit=50)
        if self.library_sub == "most_played":
            return get_most_played(limit=30)
        if self.library_sub in ("yt_liked", "yt_playlists"):
            sub = self.library_sub
            if not yt_client.authenticated:
                return []
            if sub not in self._yt_cache and sub not in self._yt_loading:
                self._yt_loading.add(sub)
                self._bg(self._load_yt_library, sub)
            return self._yt_cache.get(sub, [])
        return [{"type": "local_playlist", "id": p["id"], "title": p["title"], "trackCount": p.get("trackCount", 0)}
                for p in get_playlists()]

    def _load_yt_library(self, sub: str) -> None:
        items = yt_client.get_liked_tracks() if sub == "yt_liked" else yt_client.get_library_playlist_items()
        if not items and yt_client.last_error:
            self._ui(self.say, f"Could not load your library: {yt_client.last_error}", True)
        self._ui(self._set_yt_library, sub, items)

    def _set_yt_library(self, sub: str, items) -> None:
        self._yt_loading.discard(sub)
        self._yt_cache[sub] = items
        self._sig = None
        self.refresh_tables()

    def open_detail(self, typ: str, item: Dict[str, Any]) -> None:
        if self.active_tab != "detail":
            self.detail_prev = self.active_tab
        bid = item["browseId"]
        self.detail = {"id": bid, "kind": typ, "tracks": [], "albums": []}
        self.query_one("#d-title", Static).update(Text(item.get("title") or item.get("name") or typ.title()))
        self.query_one("#d-sub", Static).update("Loading...")
        self.query_one(TabbedContent).active = "detail"
        self._bg(self._load_detail, typ, bid, item.get("title") or item.get("name") or "")

    def _load_detail(self, typ: str, bid: str, title: str = "") -> None:
        if typ == "artist":
            data = yt_client.get_artist(bid, title)
            sub = " - ".join(x for x in (data.get("subscribers") and f"{data['subscribers']} subscribers",
                                         f"{len(data['tracks'])} songs", f"{len(data['albums'])} releases") if x)
            if data.get("limited"):
                sub = f"Showing search results ({sub}): YouTube's full artist page is unavailable right now."
        elif typ == "album":
            data = yt_client.get_album(bid)
            sub = " - ".join(str(x) for x in (data.get("artist"), data.get("year"), f"{len(data['tracks'])} songs") if x)
        else:
            data = yt_client.get_playlist(bid)
            sub = " - ".join(str(x) for x in (data.get("author"), f"{len(data['tracks'])} songs") if x)
        if not data.get("tracks") and not data.get("albums"):
            sub = yt_client.last_error or "Nothing found (it may be unavailable in your region)."
        self._ui(self._set_detail, bid, data, sub)

    def _set_detail(self, bid: str, data: Dict[str, Any], sub: str) -> None:
        if self.detail.get("id") != bid:      # the user already opened something else
            return
        self.detail.update(tracks=data.get("tracks", []), albums=data.get("albums", []))
        self.query_one("#d-title", Static).update(Text(data.get("title") or "Details"))
        self.query_one("#d-sub", Static).update(Text(sub))
        self._sig = None
        self.refresh_tables()

    def _play_local_playlist(self, playlist_id: str) -> None:
        pl = get_playlist(playlist_id)
        if pl and pl["tracks"]:
            player.play(queue=pl["tracks"], index=0)
        else:
            self._ui(self.say, "This playlist is empty. Highlight a song and press P to add it.", True)
