"""The 0.5 s UI refresh: now-playing card, settings chips, track tables and disk usage."""
import os

from rich.text import Text
from textual.css.query import NoMatches
from textual.widgets import Static

from ...config import CACHE_DIR, DOWNLOADS_DIR, config
from ...core.database import get_downloads, get_favorite_ids
from ...core.player import player
from ...core.ytmusic import yt_client
from ..components import HEART_OFF, HEART_ON, PAUSE, PLAY
from ..constants import LIB_TABS
from ..panels import TopBar
from ..widgets import Chip, SeekBar, TrackTable, VolumeBar


class RefreshMixin:
    """The 0.5 s UI refresh: now-playing card, settings chips, track tables and disk usage."""

    def tick(self) -> None:
        """Cheap UI refresh: runs twice a second and on every player event."""
        self._mpris_sync()
        try:
            self._refresh_now_playing()
            self._refresh_lyrics()
            self._refresh_art()
            self._refresh_viz()
        except NoMatches:   # widgets not mounted yet (or already torn down on exit)
            return

        sig = (player.current_track.get("videoId") if player.current_track else None,
               player.is_playing, player.is_paused, len(player.queue), player.queue_index)
        if sig != self._sig:
            self._sig = sig
            self.refresh_tables()

    def _refresh_now_playing(self) -> None:
        tr = player.current_track
        title = self.query_one("#np-title", Static)
        artist = self.query_one("#np-artist", Static)
        status_line = self.query_one("#np-status", Static)
        if tr:
            status = "Loading..." if player.is_loading else ("Paused" if player.is_paused else
                                                              ("Playing" if player.is_playing else "Stopped"))
            glyph = PLAY if status == "Playing" else (PAUSE if status == "Paused" else "\u25cf")
            title.update(Text(tr.get("title", "Unknown"), no_wrap=True, overflow="ellipsis", justify="center"))
            album = tr.get("album")
            album_name = album.get("name") if isinstance(album, dict) else (album or "")
            sub = "  \u00b7  ".join(x for x in (tr.get("artist"), album_name) if x)
            artist.update(Text(sub, no_wrap=True, overflow="ellipsis", justify="center"))
            sleep = f"   sleep {player.sleep_remaining()}m" if player.sleep_deadline else ""
            status_line.update(Text(f"{glyph} {status}{sleep}", justify="center"))
        else:
            title.update(Text("Nothing playing", justify="center"))
            artist.update(Text("Select a song to start", justify="center"))
            status_line.update("")
        self.query_one("#topbar", TopBar).show(bool(yt_client.authenticated), player.sleep_remaining())

        self.query_one("#seek", SeekBar).refresh()
        self.query_one("#vol", VolumeBar).refresh()

        playing = player.is_playing and not player.is_paused
        self.query_one("#c-play", Chip).update(PAUSE if playing else PLAY)
        fav = bool(tr and tr.get("videoId") in get_favorite_ids()) if tr else False
        fav_chip = self.query_one("#c-fav", Chip)
        fav_chip.update(HEART_ON if fav else HEART_OFF)
        fav_chip.set_class(fav, "on")

        rep = {"off": "Repeat", "all": "Repeat: all", "one": "Repeat: one"}.get(player.repeat_mode, "Repeat")
        for cid, label, on in (("c-shuf", "Shuffle", player.shuffle), ("c-rep", rep, player.repeat_mode != "off"),
                               ("c-auto", "Auto", player.autoplay)):
            chip = self.query_one(f"#{cid}", Chip)
            chip.update(label)
            chip.set_class(on, "on")

        self.query_one("#s-theme", Chip).update(f"Theme: {self.theme}")
        self.query_one("#s-auto", Chip).update(f"Autoplay radio: {'ON' if player.autoplay else 'OFF'}")
        self.query_one("#s-shuf", Chip).update(f"Shuffle: {'ON' if player.shuffle else 'OFF'}")
        self.query_one("#s-rep", Chip).update(f"Repeat: {player.repeat_mode.upper()}")
        self.query_one("#s-art", Chip).update(self._art_label())
        self.query_one("#s-viz", Chip).update(f"Visualizer: {config.get('viz_style', 'bars')}")
        self.query_one("#s-vizc", Chip).update(f"Visualizer colours: {config.get('viz_colors', 'theme')}")
        mins = player.sleep_remaining()
        self.query_one("#s-sleep", Chip).update(f"Sleep timer: {f'{mins} min left' if mins else 'off'}")
        self.query_one("#s-norm", Chip).update(f"Normalize volume: {'ON' if config.get('normalize_volume', True) else 'OFF'}")
        self.query_one("#s-gapless", Chip).update(f"Gapless playback: {'ON' if config.get('gapless', True) else 'OFF'}")
        self.query_one("#s-account", Static).update(
            "Account: signed in to YouTube Music" if yt_client.authenticated
            else "Account: anonymous. Run `tunebox login` to use your liked songs and playlists.")
        for sub in LIB_TABS:
            chip = self.query_one(f"#lib-{sub}", Chip)
            chip.set_class(sub == self.library_sub, "sel")
            if sub.startswith("yt_"):
                chip.display = yt_client.authenticated

    def refresh_tables(self) -> None:
        try:
            self._refresh_tables()
        except NoMatches:
            # Fired during startup/teardown before every table is mounted. Reset the signature so the
            # next tick repaints once the widgets exist.
            self._sig = None

    def _refresh_tables(self) -> None:
        favs = get_favorite_ids()
        tr = player.current_track
        vid = tr.get("videoId") if tr else None
        playing = player.is_playing and not player.is_paused
        q = list(player.queue)
        qi = player.queue_index if player.is_playing or player.is_paused else None

        self._show_list("home", self.home_items, self._home_hint())
        self.query_one("#t-home", TrackTable).sync(self.home_items, favs, vid, playing)
        self._show_list("search", self.search_items, self._search_hint())
        self.query_one("#t-search", TrackTable).sync(self.search_items, favs, vid, playing)
        self._show_list("queue", q, "The queue is empty.\n\nPlay a song, or press E on one to add it.")
        self.query_one("#t-queue", TrackTable).sync(q, favs, vid, playing, cur_idx=qi)
        upnext = player.upcoming(8)
        self.query_one("#t-upnext", TrackTable).sync(upnext, favs, None, playing, cur_idx=-1)
        if self.active_tab == "library":
            items = self._library_items()
            self._show_list("library", items, self._library_hint())
            self.query_one("#t-library", TrackTable).sync(items, favs, vid, playing)
        elif self.active_tab == "downloads":
            items = get_downloads()
            self._show_list("downloads", items, "No downloads yet.\n\nPress d on a song to save it as a file.")
            self.query_one("#t-downloads", TrackTable).sync(items, favs, vid, playing)
        elif self.active_tab == "detail":
            d = self.detail
            self.query_one("#t-detail", TrackTable).sync(d.get("tracks", []), favs, vid, playing)
            albums = d.get("albums", [])
            self.query_one("#t-detail-albums", TrackTable).sync(albums, favs, None, False)
            self.query_one("#d-albums-label").display = bool(albums)
            self.query_one("#t-detail-albums").display = bool(albums)
        elif self.active_tab == "settings":
            self._refresh_usage()

    def _show_list(self, name: str, items, hint: str) -> None:
        """Show the list when it has rows, otherwise a short explanation in its place."""
        table = self.query_one(f"#t-{name}", TrackTable)
        note = self.query_one(f"#e-{name}", Static)
        has_rows = bool(items)
        table.display = has_rows
        note.display = not has_rows
        if not has_rows:
            note.update(Text(hint, justify="center"))

    def _home_hint(self) -> str:
        if yt_client.last_error:
            return f"Couldn't load YouTube Music.\n\n{yt_client.last_error}"
        return "Loading your music..."

    def _search_hint(self) -> str:
        if self.search_query:
            return f"No results for \"{self.search_query}\".\n\nTry another spelling, or a different filter."
        return "Search YouTube Music\n\nType a song, album or artist above and press Enter."

    def _library_hint(self) -> str:
        sub = self.library_sub
        if sub in ("yt_liked", "yt_playlists") and not yt_client.authenticated:
            return "Sign in to see this.\n\nRun `tunebox login` in a terminal."
        if sub in self._yt_loading:
            return "Loading your library..."
        return {
            "favorites": "No favorites yet.\n\nPress f on a song to add it.",
            "playlists": "No playlists yet.\n\nPress P on a song to start one, or S to save the queue.",
            "history": "Nothing played yet.",
            "most_played": "Play some songs and your most played will show up here.",
            "yt_liked": "No liked songs found on your account.",
            "yt_playlists": "No playlists found on your account.",
        }.get(sub, "Nothing here yet.")

    @staticmethod
    def _dir_stats(path) -> tuple:
        size = count = 0
        try:
            for entry in os.scandir(path):
                if entry.is_file():
                    size += entry.stat().st_size
                    count += 1
        except OSError:
            pass
        return size, count

    def _refresh_usage(self) -> None:
        c_size, c_n = self._dir_stats(CACHE_DIR)
        d_size, d_n = self._dir_stats(config.get("download_dir", DOWNLOADS_DIR))
        mb = lambda b: f"{b / (1024 * 1024):.1f} MB"
        self.query_one("#s-usage", Static).update(
            f"Audio cache: {mb(c_size)} ({c_n} files)    Downloads: {mb(d_size)} ({d_n} files)")
