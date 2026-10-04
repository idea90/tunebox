"""
Tunebox interactive app (Textual).

Real event loop: live progress/lyrics, single-click playback, clickable
controls, seek bar, volume bar, scroll-wheel volume and right-click pause.
"""
import asyncio
import os
import random
import threading
from typing import Any, Dict, List, Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Footer, Header, Input, Label, OptionList, Select, Static, TabbedContent, TabPane,
)
from textual.widgets.option_list import Option

from ..config import config, CACHE_DIR, DOWNLOADS_DIR
from ..core.player import player
from ..core.ytmusic import yt_client
from ..core.lyrics import get_lyrics
from ..core import downloader, session, mediakeys, albumart, share, recommend, spectrum
from ..core.downloader import download_track_file
from ..core.database import (
    add_favorite, remove_favorite, get_favorite_ids, get_favorites, get_history,
    get_most_played, get_playlists, get_playlist, create_playlist, delete_playlist,
    add_track_to_playlist, get_downloads, remove_download,
)
from .components import HEART_ON, HEART_OFF, PLAY, PAUSE
from .theme import THEMES, TEXTUAL_PALETTES
from . import inline
from .suggest import SearchSuggester
from .visualizer import Visualizer, VIZ_STYLES, VIZ_COLORS
from .widgets import ArtView, Chip, SeekBar, VolumeBar, TrackTable, is_track

TABS = ["home", "search", "queue", "lyrics", "library", "downloads", "settings"]
LIB_TABS = ["favorites", "playlists", "history", "most_played", "yt_liked", "yt_playlists"]
SLEEP_STEPS = [0, 15, 30, 60, 90]   # minutes; 0 = off
# Rows above the first lyric line: 1 padding + source label + blank line. Used to map clicks to lines.
LYRICS_TOP_ROWS = 3
SEARCH_FILTERS = [("All", "all"), ("Songs", "songs"), ("Albums", "albums"),
                  ("Artists", "artists"), ("Playlists", "playlists"), ("Videos", "videos")]


class PlaylistPicker(ModalScreen[Optional[str]]):
    """Pick an existing playlist or type a new name; dismisses with a playlist id."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    PlaylistPicker { align: center middle; }
    #picker { width: 56; height: auto; max-height: 24; background: $panel; border: round $primary; padding: 1 2; }
    #picker OptionList { height: auto; max-height: 12; margin: 1 0; }
    """

    def __init__(self, track_title: str):
        super().__init__()
        self.track_title = track_title

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label(Text(f"Add \"{self.track_title}\" to a playlist", style="bold"))
            plist = get_playlists()
            if plist:
                yield OptionList(*[Option(f"{p['title']}  ({p.get('trackCount', 0)})", id=p["id"]) for p in plist])
            yield Input(placeholder="...or type a new playlist name and press Enter", id="new-name")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if name:
            self.dismiss(create_playlist(name)["id"])

    def action_cancel(self) -> None:
        self.dismiss(None)


class NowPlaying(Vertical):
    """Sidebar card. Scrolling the wheel anywhere over it changes the volume."""

    def compose(self) -> ComposeResult:
        yield ArtView(id="art")
        img = inline.make_image_widget(id="art-img")
        if img is not None:               # only on terminals with a graphics protocol
            with Horizontal(id="art-wrap"):
                yield img
        yield Visualizer(id="viz")
        yield Static("Nothing playing", id="np-title")
        yield Static("Select a song to start", id="np-artist")
        yield SeekBar(id="seek")
        with Horizontal(classes="ctl"):
            yield Chip("◀◀", "prev", id="c-prev")
            yield Chip(PLAY, "play_pause", id="c-play")
            yield Chip("▶▶", "next", id="c-next")
            yield Chip(HEART_OFF, "favorite", id="c-fav")
        with Horizontal(classes="ctl"):
            yield Chip("Shuffle", "shuffle", id="c-shuf")
            yield Chip("Repeat", "repeat", id="c-rep")
            yield Chip("Auto", "autoplay", id="c-auto")
        yield VolumeBar(id="vol")

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        player.volume_up(5)
        self.query_one(VolumeBar).refresh()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        player.volume_down(5)
        self.query_one(VolumeBar).refresh()


class LyricsView(Static):
    """Full-size lyrics pane. Click a synced line to seek to it."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self.window_start = 0
        self.lines: List[Dict[str, Any]] = []

    def on_click(self, event: events.Click) -> None:
        if event.button != 1 or not self.lines:
            return
        idx = self.window_start + event.y
        if 0 <= idx < len(self.lines) and self.lines[idx].get("time") is not None:
            event.stop()
            self.app.seek_to(self.lines[idx]["time"])


class AppTabs(TabbedContent):
    """TabbedContent that never changes tab just because a widget inside a pane gained focus.

    Textual's default does exactly that, so a focus message queued for the *old* pane (from a click or a
    deferred focus call) lands after a switch and silently reverts it. This app navigates tabs explicitly
    (keys, tab bar clicks, open_detail), so focus must not drive navigation.
    """

    def _on_tab_pane_focused(self, event) -> None:
        event.stop()
        event.prevent_default()


class TuneboxApp(App):
    TITLE = "TUNEBOX"
    SUB_TITLE = "YouTube Music"
    ENABLE_COMMAND_PALETTE = False
    # Textual activates a tab whenever a widget inside its pane gains focus. Screen auto-focus at startup
    # would therefore race with (and revert) a tab switch, so focus is placed explicitly instead.
    AUTO_FOCUS = None

    CSS = """
    Screen { background: $background; }
    Header { background: $primary; color: $background; }
    #main { width: 3fr; }
    #sidebar { width: 2fr; min-width: 36; max-width: 56; border-left: tall $panel; padding: 0 1; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0; }
    TrackTable { height: 1fr; background: $surface; }
    TrackTable > .datatable--header { background: $panel; color: $primary; text-style: bold; }
    TrackTable > .datatable--cursor { background: $primary 40%; color: $text; }
    #search-bar { height: 3; }
    #search-bar Input { width: 1fr; }
    #search-bar Select { width: 20; }
    #lib-bar { height: 1; margin: 0 0 1 0; }
    #lyrics-view { height: 1fr; padding: 1 2; background: $surface; }
    #mini-lyrics { height: 8; padding: 0 1; color: $text-muted; }
    .section { color: $primary; text-style: bold; margin-top: 1; }

    NowPlaying { height: auto; padding: 1 0; background: $surface; border: round $panel; }
    #np-title { text-style: bold; padding: 0 1; }
    #np-artist { color: $secondary; padding: 0 1; margin-bottom: 1; }
    SeekBar, VolumeBar { margin: 0 1; }
    .ctl { height: 1; margin: 1 1 0 1; }
    .chip { width: auto; height: 1; padding: 0 1; margin-right: 1; background: $panel; color: $text; }
    .chip:hover { background: $primary 60%; }
    .chip.on { background: $primary; color: $background; text-style: bold; }
    .chip.sel { background: $secondary; color: $background; text-style: bold; }
    #art-wrap { height: 12; margin: 0 1 1 1; align: center middle; }
    #art-img { height: 12; width: auto; }
    #d-title { text-style: bold; color: $primary; padding: 1 2 0 2; }
    #d-sub { color: $text-muted; padding: 0 2; }
    #d-actions { height: 1; margin: 1 2; }
    #d-albums-label { color: $primary; text-style: bold; padding: 1 2 0 2; }
    #t-detail { height: 3fr; }
    #t-detail-albums { height: 2fr; }
    #settings-box { padding: 1 2; }
    #s-usage, #s-account { color: $text-muted; margin-top: 1; }
    #settings-box .chip { margin: 0 0 1 0; }
    #settings-note { color: $text-muted; margin-top: 1; }
    """

    BINDINGS = [
        Binding("space", "play_pause", "Play/Pause"),
        Binding("n", "next", "Next"),
        Binding("p", "prev", "Prev"),
        Binding("f", "favorite", "Fav"),
        Binding("s", "shuffle", "Shuffle"),
        Binding("r", "repeat", "Repeat"),
        Binding("a", "autoplay", "Auto"),
        Binding("d", "download", "Download"),
        Binding("q", "quit_app", "Quit"),
        Binding("plus,equals_sign", "vol(5)", "Vol+", show=False),
        Binding("minus", "vol(-5)", "Vol-", show=False),
        Binding("right_square_bracket", "skip(10)", "+10s", show=False),
        Binding("left_square_bracket", "skip(-10)", "-10s", show=False),
        Binding("R", "radio", "Radio", show=False),
        Binding("P", "add_to_playlist", "To playlist", show=False),
        Binding("x", "remove_selected", "Remove", show=False),
        Binding("c", "clear_queue", "Clear queue", show=False),
        Binding("N", "queue_add(True)", "Play next", show=False),
        Binding("E", "queue_add(False)", "Add to queue", show=False),
        Binding("shift+up", "queue_move(-1)", "Move up", show=False),
        Binding("shift+down", "queue_move(1)", "Move down", show=False),
        Binding("z", "sleep_cycle", "Sleep", show=False),
        Binding("i", "cycle_art", "Cover art", show=False),
        Binding("v", "cycle_viz", "Visualizer", show=False),
        Binding("V", "cycle_viz_colors", "Visualizer colours", show=False),
        Binding("y", "copy_smart", "Copy", show=False),
        Binding("Y", "copy_lyrics", "Copy lyrics", show=False),
        Binding("g", "go_artist", "Artist", show=False),
        Binding("b", "go_album", "Album", show=False),
        Binding("t", "cycle_theme", "Theme", show=False),
        Binding("l", "tab('lyrics')", "Lyrics", show=False),
        Binding("slash", "focus_search", "Search", show=False),
        Binding("escape", "focus_table", "Back", show=False),
        Binding("1", "tab('home')", "Home", show=False),
        Binding("2", "tab('search')", "Search", show=False),
        Binding("3", "tab('queue')", "Queue", show=False),
        Binding("4", "tab('lyrics')", "Lyrics", show=False),
        Binding("5", "tab('library')", "Library", show=False),
        Binding("6", "tab('downloads')", "Downloads", show=False),
        Binding("7", "tab('settings')", "Settings", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.home_items: List[Dict[str, Any]] = []
        self.search_items: List[Dict[str, Any]] = []
        self.search_query = ""
        self.library_sub = "favorites"
        self.active_tab = "home"
        self.lyrics: Dict[str, Any] = {"lines": [], "synced": False, "source": ""}
        self.lyrics_vid: Optional[str] = None
        self.lyrics_loading = False
        self.detail: Dict[str, Any] = {}
        self.detail_prev = "home"
        self._yt_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._yt_loading: set = set()
        self.tab_events = 0
        self._search_seq = 0
        self.art_vid: Optional[str] = None
        self._art_failed: Optional[str] = None     # song whose cover failed to load (don't retry every tick)
        self._art_loading = False
        self.viz_path: Optional[str] = None       # audio file the visualizer's spectrum belongs to
        self._viz_failed: Optional[str] = None
        self._viz_loading = False
        self.feed_items: List[Dict[str, Any]] = []      # YouTube's own Home shelves
        self._because: Optional[Dict[str, Any]] = None  # "Because you played X" shelf
        self._because_seed: Optional[str] = None
        self._saved = False      # session already written by an explicit quit (stop() zeroes the position)
        self._sig = None
        self._ui_thread = threading.get_ident()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="main"):
                with AppTabs(initial="home", id="tabs"):
                    with TabPane("1 Home", id="home"):
                        yield TrackTable(id="t-home")
                    with TabPane("2 Search", id="search"):
                        with Horizontal(id="search-bar"):
                            yield Input(placeholder="Search songs, albums, artists...  (press / to focus, Right arrow accepts a suggestion)",
                                        id="search-input", suggester=SearchSuggester())
                            yield Select(SEARCH_FILTERS, value="all", allow_blank=False, id="search-filter")
                        yield TrackTable(id="t-search")
                    with TabPane("3 Queue", id="queue"):
                        yield TrackTable(kind="queue", id="t-queue")
                    with TabPane("4 Lyrics", id="lyrics"):
                        yield LyricsView(id="lyrics-view")
                    with TabPane("5 Library", id="library"):
                        with Horizontal(id="lib-bar"):
                            yield Chip("Favorites", "lib('favorites')", id="lib-favorites")
                            yield Chip("Playlists", "lib('playlists')", id="lib-playlists")
                            yield Chip("History", "lib('history')", id="lib-history")
                            yield Chip("Most played", "lib('most_played')", id="lib-most_played")
                            yield Chip("YT Liked", "lib('yt_liked')", id="lib-yt_liked")
                            yield Chip("YT Playlists", "lib('yt_playlists')", id="lib-yt_playlists")
                        yield TrackTable(kind="library", id="t-library")
                    with TabPane("6 Downloads", id="downloads"):
                        yield TrackTable(kind="downloads", id="t-downloads")
                    with TabPane("7 Settings", id="settings"):
                        with Vertical(id="settings-box"):
                            yield Chip("", "cycle_theme", id="s-theme")
                            yield Chip("", "autoplay", id="s-auto")
                            yield Chip("", "shuffle", id="s-shuf")
                            yield Chip("", "repeat", id="s-rep")
                            yield Chip("", "cycle_art", id="s-art")
                            yield Chip("", "cycle_viz", id="s-viz")
                            yield Chip("", "cycle_viz_colors", id="s-vizc")
                            yield Chip("", "sleep_cycle", id="s-sleep")
                            yield Chip("", "toggle_normalize", id="s-norm")
                            yield Chip("", "toggle_gapless", id="s-gapless")
                            yield Chip("Clear audio cache", "clear_cache", id="s-cache")
                            yield Static("", id="s-usage")
                            yield Static("", id="s-account")
                            yield Static("Click a button to change it. Volume: scroll over the player card.", id="settings-note")
                    with TabPane("8 Details", id="detail"):
                        yield Static("Nothing selected", id="d-title")
                        yield Static("Click an artist, album or playlist to open it here.", id="d-sub")
                        with Horizontal(id="d-actions"):
                            yield Chip("Play all", "play_all", id="d-play")
                            yield Chip("Shuffle", "shuffle_play", id="d-shuffle")
                            yield Chip("Back", "detail_back", id="d-back")
                        yield TrackTable(kind="detail", id="t-detail")
                        yield Static("Albums & singles", id="d-albums-label")
                        yield TrackTable(kind="detail", id="t-detail-albums")
            with Vertical(id="sidebar"):
                yield NowPlaying(id="np")
                yield Static("Up Next", classes="section")
                yield TrackTable(compact=True, kind="upnext", id="t-upnext")
                yield Static("", id="mini-lyrics")
        yield Footer()

    # ------------------------------------------------------------ lifecycle

    def on_mount(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._ui_thread = threading.get_ident()
        for name, pal in TEXTUAL_PALETTES.items():
            self.register_theme(Theme(name=name, foreground="#e8e8ea", dark=True, **pal))
        self.theme = config.get("theme", "metro_dark") if config.get("theme") in THEMES else "metro_dark"

        player.on_track_change = lambda tr: self._ui(self.tick)
        player.on_state_change = lambda: self._ui(self.tick)
        player.on_error = lambda msg: self._ui(self.say, msg, True)
        player.on_info = lambda msg: self._ui(self.say, msg)
        if session.restore(player):
            self.say("Restored your last session - press Space to resume.")
        if mediakeys.start(lambda action: self._ui(self._media_action, action)):
            self.say("Media keys enabled.")
        self.set_interval(15, lambda: session.save(player))   # so a crash loses at most 15 s
        if config.load_error:
            self.say(config.load_error, True)
        if not player.audio_ok:
            self.say("No audio output device found - playback is unavailable.", True)

        self.set_interval(0.5, self.tick)
        self._bg(self._load_home)
        self.call_after_refresh(self.tick)

    def on_unmount(self) -> None:
        if not self._saved:
            session.save(player)
        mediakeys.stop()

    def _media_action(self, action: str) -> None:
        if action == "play_pause":
            self.action_play_pause()
        elif action == "next":
            self.action_next()
        elif action == "prev":
            self.action_prev()
        elif action == "stop":
            player.stop()

    def _ui(self, fn, *args) -> None:
        """Run `fn` on the UI thread from any thread (non-blocking)."""
        if threading.get_ident() == self._ui_thread:
            fn(*args)
        elif self._loop and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:
                pass

    def _bg(self, fn, *args) -> None:
        """Run blocking work (network, downloads) off the UI thread."""
        def runner():
            try:
                fn(*args)
            except Exception as e:  # never let a worker die silently
                self._ui(self.say, f"{type(e).__name__}: {e}"[:160], True)
        self.run_worker(runner, thread=True, exit_on_error=False)

    def say(self, msg: str, error: bool = False) -> None:
        self.notify(msg, severity="error" if error else "information", timeout=5 if error else 3)

    def seek_to(self, seconds: float) -> None:
        self._bg(lambda: player.seek(seconds, relative=False))

    # ------------------------------------------------------------ data loading

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

    # ------------------------------------------------------------ periodic refresh

    def tick(self) -> None:
        """Cheap UI refresh: runs twice a second and on every player event."""
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
        if tr:
            status = "Loading..." if player.is_loading else ("Paused" if player.is_paused else
                                                              ("Playing" if player.is_playing else "Stopped"))
            title.update(Text(tr.get("title", "Unknown"), no_wrap=True, overflow="ellipsis"))
            album = tr.get("album")
            album_name = album.get("name") if isinstance(album, dict) else (album or "")
            sub = " - ".join(x for x in (tr.get("artist"), album_name) if x)
            sleep = f" | sleep {player.sleep_remaining()}m" if player.sleep_deadline else ""
            artist.update(Text(f"{sub}  [{status}{sleep}]" if sub else f"[{status}{sleep}]", no_wrap=True, overflow="ellipsis"))
        else:
            title.update("Nothing playing")
            artist.update("Select a song to start")

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

    def _refresh_lyrics(self) -> None:
        tr = player.current_track
        vid = tr.get("videoId") if tr else None
        if vid and vid != self.lyrics_vid:
            self.lyrics_vid = vid
            self.lyrics = {"lines": [], "synced": False, "source": ""}
            self.lyrics_loading = True
            self._bg(self._fetch_lyrics, dict(tr))

        pos = player.get_position()
        mini = self.query_one("#mini-lyrics", Static)
        full = self.query_one("#lyrics-view", LyricsView)
        lines = self.lyrics.get("lines", [])
        full.lines = lines if self.lyrics.get("synced") else []

        if not tr:
            mini.update("")
            full.update(Text("\nPlay a song to see its lyrics.", style="grey50"))
            return
        if self.lyrics_loading:
            mini.update(Text("Loading lyrics...", style="grey50"))
            full.update(Text("\nLoading lyrics...", style="grey50"))
            return

        synced = self.lyrics.get("synced")
        active = 0
        if synced:
            for i, ln in enumerate(lines):
                if ln.get("time") is not None and ln["time"] <= pos:
                    active = i
                else:
                    break

        def render(height: int, before: int):
            out = Text()
            if synced:
                start = max(0, min(active - before, max(0, len(lines) - height)))
                for i in range(start, min(len(lines), start + height)):
                    txt = lines[i].get("text", "")
                    if i == active:
                        out.append(f"{PLAY} {txt}\n", style=f"bold {self.current_theme.primary}")
                    else:
                        out.append(f"  {txt}\n", style="grey50" if i < active else "grey70")
                return out, start
            for ln in lines[:height]:
                out.append(f"  {ln.get('text', '')}\n")
            return out, 0

        mini_text, _ = render(7, 2)
        mini.update(mini_text)
        if self.active_tab == "lyrics":
            h = max(5, full.size.height - 2)
            text, start = render(h, h // 2)
            header = Text(f"{self.lyrics.get('source', '')}\n\n", style="grey50")   # always 2 rows
            full.update(header + text)
            full.window_start = start - LYRICS_TOP_ROWS

    # ------------------------------------------------------------ cover art

    MIN_ART_ROWS = 6
    MAX_ART_ROWS = 14
    ART_RESERVED_ROWS = 32     # rest of the sidebar, incl. ~5 rows of Up Next (28 left Up Next one row at 40 lines)

    VIZ_MIN_ROWS = 2
    VIZ_MAX_ROWS = 4

    def _sidebar_budget(self) -> tuple:
        """(art rows, visualizer rows). Both share what's left after the rest of the sidebar; on short
        terminals the visualizer shrinks first so the cover still fits, and Up Next always keeps its rows."""
        avail = self.size.height - self.ART_RESERVED_ROWS
        art_on = inline.resolve_style(config.get("art_style", "auto")) != "off"
        viz_rows = 0
        if config.get("viz_style", "bars") != "off":
            room_for_art = art_on and avail - self.VIZ_MIN_ROWS >= self.MIN_ART_ROWS
            viz_rows = min(self.VIZ_MAX_ROWS, avail - (self.MIN_ART_ROWS if room_for_art else 0))
            if viz_rows < self.VIZ_MIN_ROWS:
                viz_rows = 0
        return min(self.MAX_ART_ROWS, avail - viz_rows), viz_rows

    def _refresh_viz(self) -> None:
        """Size/show the visualizer and analyse each new song's audio (in the background)."""
        viz = self.query_one("#viz", Visualizer)
        _, rows = self._sidebar_budget()
        viz.display = rows > 0
        if rows:
            viz.styles.height = rows
        path = player._current_file if (player.is_playing and player.current_track) else None
        if path != self.viz_path:
            self.viz_path = path
            viz.spectrum = None
            if path and rows:
                self._bg(self._analyze_viz, path)
        elif path and rows and viz.spectrum is None and path != self._viz_failed and not self._viz_loading:
            self._bg(self._analyze_viz, path)      # switched on mid-song

    def _analyze_viz(self, path: str) -> None:
        self._viz_loading = True
        try:
            spec = spectrum.analyze(path)
        finally:
            self._viz_loading = False
        self._ui(self._set_viz, path, spec)

    def _set_viz(self, path: str, spec) -> None:
        if path != self.viz_path:                   # a slow analysis for a song that already changed
            return
        self._viz_failed = path if spec is None else None
        self.query_one("#viz", Visualizer).spectrum = spec

    def action_cycle_viz(self) -> None:
        cur = config.get("viz_style", "bars")
        nxt = VIZ_STYLES[(VIZ_STYLES.index(cur) + 1) % len(VIZ_STYLES)] if cur in VIZ_STYLES else VIZ_STYLES[0]
        config.set("viz_style", nxt)
        self.say(f"Visualizer: {nxt}")
        self.tick()

    def action_cycle_viz_colors(self) -> None:
        cur = config.get("viz_colors", "theme")
        nxt = VIZ_COLORS[(VIZ_COLORS.index(cur) + 1) % len(VIZ_COLORS)] if cur in VIZ_COLORS else VIZ_COLORS[0]
        config.set("viz_colors", nxt)
        self.say(f"Visualizer colours: {nxt}")
        self.tick()

    def _inline_widgets(self):
        """(wrap, image widget) when this terminal has a graphics protocol, else (None, None)."""
        wraps, imgs = self.query("#art-wrap"), self.query("#art-img")
        return (wraps.first() if wraps else None), (imgs.first() if imgs else None)

    def _art_label(self) -> str:
        style = config.get("art_style", "auto")
        proto = inline.graphics_protocol()
        if style == "auto":
            return f"Cover art: auto ({proto} image)" if proto else "Cover art: auto (ascii; no image support)"
        return f"Cover art: {style}"

    def _refresh_art(self) -> None:
        """Size the art to the terminal, pick image vs text art, and (re)load the cover when the song changes."""
        art = self.query_one("#art", ArtView)
        wrap, img = self._inline_widgets()
        effective = inline.resolve_style(config.get("art_style", "auto"))
        rows, _ = self._sidebar_budget()
        show = effective != "off" and rows >= self.MIN_ART_ROWS
        use_image = show and effective == "image" and img is not None

        art.display = show and not use_image
        if show and not use_image:
            art.styles.height = rows
            art.sync_style()
        if wrap is not None:
            wrap.display = use_image
            if use_image:
                wrap.styles.height = rows
                img.styles.height = rows

        tr = player.current_track
        vid = tr.get("videoId") if tr else None
        if vid != self.art_vid:
            self.art_vid = vid
            art.set_image(None)
            if img is not None:
                img.image = None
            if tr and show:
                self._bg(self._fetch_art, dict(tr))
        elif (tr and show and art.image is None and self.art_vid != self._art_failed
              and not self._art_loading):
            # art was switched on after the song started (and we haven't already failed for this song)
            self._bg(self._fetch_art, dict(tr))
        if use_image and img.image is None and art.image is not None:
            img.image = albumart.square_for_display(art.image)    # style switched to image after the cover loaded

    def _fetch_art(self, track: Dict[str, Any]) -> None:
        self._art_loading = True
        try:
            image = albumart.fetch_image(track)
        finally:
            self._art_loading = False
        self._ui(self._set_art, track.get("videoId"), image)

    def _set_art(self, vid: Optional[str], image) -> None:
        if vid == self.art_vid:                 # ignore a slow download for a song that already changed
            self._art_failed = vid if image is None else None   # don't retry a failing cover every tick
            self.query_one("#art", ArtView).set_image(image)
            _, img = self._inline_widgets()
            if img is not None:
                img.image = albumart.square_for_display(image) if image is not None else None

    def action_cycle_art(self) -> None:
        styles = albumart.STYLES
        cur = config.get("art_style", "auto")
        nxt = styles[(styles.index(cur) + 1) % len(styles)] if cur in styles else styles[0]
        config.set("art_style", nxt)
        self.say(self._art_label())
        self.tick()

    def _fetch_lyrics(self, track: Dict[str, Any]) -> None:
        data = get_lyrics(track.get("title", ""), artist_name=track.get("artist", ""),
                          duration=track.get("duration_seconds"), video_id=track.get("videoId"))
        if track.get("videoId") == self.lyrics_vid:  # ignore stale results
            self._ui(self._set_lyrics, data)

    def _set_lyrics(self, data) -> None:
        self.lyrics = data
        self.lyrics_loading = False
        self._refresh_lyrics()

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

        self.query_one("#t-home", TrackTable).sync(self.home_items, favs, vid, playing)
        self.query_one("#t-search", TrackTable).sync(self.search_items, favs, vid, playing)
        self.query_one("#t-queue", TrackTable).sync(q, favs, vid, playing, cur_idx=qi)
        upnext = player.upcoming(8)
        self.query_one("#t-upnext", TrackTable).sync(upnext, favs, None, playing, cur_idx=-1)
        if self.active_tab == "library":
            self.query_one("#t-library", TrackTable).sync(self._library_items(), favs, vid, playing)
        elif self.active_tab == "downloads":
            self.query_one("#t-downloads", TrackTable).sync(get_downloads(), favs, vid, playing)
        elif self.active_tab == "detail":
            d = self.detail
            self.query_one("#t-detail", TrackTable).sync(d.get("tracks", []), favs, vid, playing)
            albums = d.get("albums", [])
            self.query_one("#t-detail-albums", TrackTable).sync(albums, favs, None, False)
            self.query_one("#d-albums-label").display = bool(albums)
            self.query_one("#t-detail-albums").display = bool(albums)
        elif self.active_tab == "settings":
            self._refresh_usage()

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

    # ------------------------------------------------------------ events

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self.active_tab = event.pane.id
        self.tab_events += 1       # lets tests/tools know startup activation has been processed
        if self.active_tab == "home":
            self.home_items = self._compose_home()      # pick up what you played since last visit
            self._bg(self._refresh_because)
        self._sig = None
        self.refresh_tables()
        try:
            self._refresh_lyrics()
        except NoMatches:
            pass
        # Single owner of focus on tab change, so two callbacks can't race.
        if self.active_tab == "search":
            self.call_after_refresh(self._focus_search_box)
        else:
            self.call_after_refresh(self._focus_active_table, self.active_tab)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search-input" or not event.value.strip():
            return
        config.add_search(event.value)
        self.say(f"Searching for \"{event.value.strip()}\"...")
        self._search_seq += 1
        self._bg(self._do_search, event.value.strip(), self.query_one("#search-filter", Select).value, self._search_seq)

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.action_play_pause()

    def on_track_table_activated(self, event: TrackTable.Activated) -> None:
        table, row = event.table, event.row
        if not (0 <= row < len(table.items)):
            return
        item = table.items[row]
        typ = item.get("type", "song")
        if typ == "header":
            return

        if table.kind == "queue":
            self._bg(player.jump_to, row)
            return
        if table.kind == "upnext":
            idx = player.index_of(item)            # Up Next isn't always queue order (shuffle)
            if idx is not None:
                self._bg(player.jump_to, idx)
            return

        if event.column is not None and event.column == table.heart_col and is_track(item):
            self._toggle_favorite(item)
            return

        if is_track(item):
            tracks = [t for t in table.items if is_track(t)]
            idx = tracks.index(item)
            self._bg(player.play, None, tracks, idx)
        elif typ in ("album", "playlist", "artist") and item.get("browseId"):
            self.open_detail(typ, item)
        elif typ == "local_playlist":
            self._bg(self._play_local_playlist, item["id"])

    # ------------------------------------------------------------ detail pages (artist / album / playlist)

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

    def action_play_all(self) -> None:
        tracks = self.detail.get("tracks", [])
        if tracks:
            self._bg(player.play, None, list(tracks), 0)
        else:
            self.say("Nothing to play here yet.", True)

    def action_shuffle_play(self) -> None:
        tracks = list(self.detail.get("tracks", []))
        if tracks:
            random.shuffle(tracks)
            self._bg(player.play, None, tracks, 0)
        else:
            self.say("Nothing to play here yet.", True)

    def action_detail_back(self) -> None:
        self.query_one(TabbedContent).active = self.detail_prev or "home"

    def _target_track(self) -> Optional[Dict[str, Any]]:
        """Highlighted song in the focused list, else the playing song."""
        table = self.focused if isinstance(self.focused, TrackTable) else self.active_table()
        item = table.selected_item() if table else None
        return item if is_track(item) else player.current_track

    def _target_item(self) -> Optional[Dict[str, Any]]:
        """Highlighted row of any kind (song, album, artist...), else the playing song."""
        table = self.focused if isinstance(self.focused, TrackTable) else self.active_table()
        item = table.selected_item() if table else None
        if item and item.get("type") != "header":
            return item
        return player.current_track

    def action_copy_smart(self) -> None:
        """y: the lyrics when you're on the Lyrics tab, otherwise a link to the highlighted/playing item."""
        if self.active_tab == "lyrics":
            self.action_copy_lyrics()
        else:
            self.action_copy_link()

    def action_copy_link(self) -> None:
        link = share.share_link(self._target_item())
        if link:
            self._copy(link, "link")
        else:
            self.say("Nothing shareable here (local playlists have no link).", True)

    def action_copy_lyrics(self) -> None:
        tr = player.current_track
        text = share.lyrics_to_text(self.lyrics, (tr or {}).get("title", ""), (tr or {}).get("artist", "")) if tr else None
        if text:
            self._copy(text, f"lyrics ({len(text.splitlines()) - 2} lines)")
        else:
            self.say("No lyrics to copy for this song.", True)

    def _copy(self, text: str, what: str) -> None:
        def work():
            method = share.copy_native(text)
            self._ui(self._copy_done, text, what, method)
        self._bg(work)

    def _copy_done(self, text: str, what: str, method: Optional[str]) -> None:
        if method:
            self.say(f"Copied {what} to the clipboard.")
            return
        try:        # no system clipboard tool: ask the terminal (OSC 52). It can't confirm, so say so.
            self.copy_to_clipboard(text)
            self.say(f"Copied {what} via the terminal. If pasting gives nothing, your terminal blocks clipboard access.")
        except Exception:
            self.say("Could not access the clipboard.", True)

    def action_go_artist(self) -> None:
        track = self._target_track()
        artists = (track or {}).get("artists") or []
        artist = artists[0] if artists and isinstance(artists[0], dict) else {}
        if artist.get("id"):
            self.open_detail("artist", {"browseId": artist["id"], "title": artist.get("name", "Artist")})
        else:
            self.say("No artist page available for this song.", True)

    def action_go_album(self) -> None:
        track = self._target_track()
        album = (track or {}).get("album")
        if isinstance(album, dict) and album.get("id"):
            self.open_detail("album", {"browseId": album["id"], "title": album.get("name", "Album")})
        else:
            self.say("No album page available for this song.", True)

    def _play_local_playlist(self, playlist_id: str) -> None:
        pl = get_playlist(playlist_id)
        if pl and pl["tracks"]:
            player.play(queue=pl["tracks"], index=0)
        else:
            self._ui(self.say, "This playlist is empty. Highlight a song and press P to add it.", True)

    def _toggle_favorite(self, track: Dict[str, Any]) -> None:
        vid = track.get("videoId")
        if not vid:
            return
        if vid in get_favorite_ids():
            remove_favorite(vid)
            self.say(f"Removed \"{track.get('title')}\" from favorites")
        else:
            add_favorite(track)
            self.say(f"Added \"{track.get('title')}\" to favorites")
        self._sig = None
        self.tick()

    # ------------------------------------------------------------ actions

    def active_table(self) -> Optional[TrackTable]:
        table_id = {"home": "t-home", "search": "t-search", "queue": "t-queue",
                    "library": "t-library", "downloads": "t-downloads", "detail": "t-detail"}.get(self.active_tab)
        return self.query_one(f"#{table_id}", TrackTable) if table_id else None

    def action_tab(self, name: str) -> None:
        self.query_one(TabbedContent).active = name

    def action_focus_search(self) -> None:
        tabs = self.query_one(TabbedContent)
        if tabs.active == "search":
            self.query_one("#search-input", Input).focus()
        else:
            tabs.active = "search"   # the tab-activated handler focuses the box

    def action_focus_table(self) -> None:
        """Escape: leave the search box, or go back from a detail page."""
        if self.active_tab == "detail" and isinstance(self.focused, TrackTable):
            self.action_detail_back()
            return
        self._focus_active_table()

    def _focus_search_box(self) -> None:
        if self.query_one(TabbedContent).active == "search":   # same stale-focus guard as _focus_active_table
            self.query_one("#search-input", Input).focus()

    def _focus_active_table(self, expected_tab: Optional[str] = None) -> None:
        """Move focus to the current tab's list. Pure focus change: must never navigate.

        `expected_tab` guards deferred calls: focusing a widget makes Textual activate its tab, so a call
        scheduled for tab A that lands after the user already switched to B would drag them back to A.
        """
        # Compare with TabbedContent.active (changes instantly), not self.active_tab (updated later by an event).
        if expected_tab is not None and expected_tab != self.query_one(TabbedContent).active:
            return
        table = self.active_table()
        if table:
            table.focus()
        else:
            self.set_focus(None)

    def action_play_pause(self) -> None:
        self._bg(player.toggle_pause)

    def action_next(self) -> None:
        self._bg(player.next)

    def action_prev(self) -> None:
        self._bg(player.previous)

    def action_skip(self, seconds: int) -> None:
        self._bg(lambda: player.seek(seconds, relative=True))

    def action_vol(self, delta: int) -> None:
        player.set_volume(player.volume + delta)
        self.say(f"Volume: {player.volume}%")

    def action_shuffle(self) -> None:
        on = player.toggle_shuffle()
        self.say(f"Shuffle {'on' if on else 'off'}")

    def action_repeat(self) -> None:
        self.say(f"Repeat: {player.cycle_repeat()}")

    def action_autoplay(self) -> None:
        on = player.toggle_autoplay()
        self.say(f"Autoplay radio {'on' if on else 'off'}")

    def action_favorite(self) -> None:
        if player.current_track:
            self._toggle_favorite(player.current_track)
        else:
            self.say("Nothing is playing.", True)

    def action_download(self) -> None:
        tr = player.current_track
        if not tr:
            self.say("Nothing is playing.", True)
            return
        self.say(f"Downloading \"{tr.get('title')}\"...")
        self._bg(self._download, dict(tr))

    def _download(self, track: Dict[str, Any]) -> None:
        path = download_track_file(track, fmt=downloader.download_format())
        if path:
            self._ui(self.say, f"Saved: {path}")
            self._ui(self.refresh_tables)
        else:
            self._ui(self.say, f"Download failed: {downloader.last_error or 'unknown error'}", True)

    def action_radio(self) -> None:
        tr = player.current_track
        if not tr:
            self.say("Play a song first to start its radio.", True)
            return
        self.say(f"Starting radio for \"{tr.get('title')}\"...")
        self._bg(self._radio, dict(tr))

    def _radio(self, seed: Dict[str, Any]) -> None:
        related = yt_client.get_watch_playlist(seed["videoId"], limit=25)
        if related:
            player.set_queue_radio(seed, related)
            self._ui(self.say, "Radio queued - see the Queue tab.")
        else:
            self._ui(self.say, "No radio suggestions available for this song.", True)

    def action_add_to_playlist(self) -> None:
        track = self._target_track()
        if not track:
            self.say("Highlight a song (or play one) first.", True)
            return

        def done(pid: Optional[str]) -> None:
            if pid and add_track_to_playlist(pid, track):
                self.say(f"Added \"{track.get('title')}\" to playlist")
                self._sig = None
                self.refresh_tables()

        self.push_screen(PlaylistPicker(track.get("title", "track")), done)

    def action_remove_selected(self) -> None:
        table = self.focused if isinstance(self.focused, TrackTable) else None
        if table is None or table.selected_item() is None:
            return
        if table.kind == "queue":
            self._bg(player.remove_from_queue, table.cursor_row)
        elif table.kind == "library" and self.library_sub == "playlists":
            item = table.selected_item()
            if item.get("type") == "local_playlist":
                delete_playlist(item["id"])
                self.say(f"Deleted playlist \"{item['title']}\"")
                self.refresh_tables()
        elif table.kind == "downloads":
            item = table.selected_item()
            remove_download(item["videoId"])
            self.say(f"Deleted \"{item.get('title')}\" from disk")
            self._sig = None
            self.refresh_tables()
        elif table.kind == "library" and self.library_sub == "favorites":
            item = table.selected_item()
            remove_favorite(item["videoId"])
            self._sig = None
            self.tick()

    def action_queue_add(self, play_next: bool) -> None:
        track = self._target_track()
        if not track:
            self.say("Highlight a song (or play one) first.", True)
            return
        if play_next:
            self._bg(player.play_next, track)
            self.say(f"Playing next: {track.get('title')}")
        else:
            self._bg(player.add_to_queue, track)
            self.say(f"Added to queue: {track.get('title')}")

    def action_queue_move(self, delta: int) -> None:
        table = self.focused
        if not (isinstance(table, TrackTable) and table.kind == "queue") or table.selected_item() is None:
            return
        new = player.move_in_queue(table.cursor_row, delta)
        self._sig = None
        self.refresh_tables()
        table.move_cursor(row=new, animate=False)

    def action_sleep_cycle(self) -> None:
        mins = player.sleep_remaining()
        nxt = next((m for m in SLEEP_STEPS if m > mins), 0) if mins else SLEEP_STEPS[1]
        player.set_sleep_timer(nxt)
        self.say(f"Sleep timer: {nxt} min" if nxt else "Sleep timer off")
        self.tick()

    def action_toggle_normalize(self) -> None:
        on = not config.get("normalize_volume", True)
        config.set("normalize_volume", on)
        self.say(f"Volume normalization {'on' if on else 'off'} (applies to songs cached from now on)")
        self.tick()

    def action_toggle_gapless(self) -> None:
        on = not config.get("gapless", True)
        config.set("gapless", on)
        self.say(f"Gapless playback {'on' if on else 'off'}")
        self.tick()

    def action_clear_queue(self) -> None:
        player.clear_queue()
        self.say("Queue cleared")

    def action_lib(self, name: str) -> None:
        self.library_sub = name
        self.refresh_tables()
        self._refresh_now_playing()

    def action_cycle_theme(self) -> None:
        names = list(THEMES.keys())
        nxt = names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]
        self.theme = nxt
        config.set("theme", nxt)
        self.say(f"Theme: {nxt}")
        self.tick()

    def action_clear_cache(self) -> None:
        count = 0
        playing = None
        for f in CACHE_DIR.glob("*.*"):
            try:
                f.unlink()
                count += 1
            except OSError:
                playing = f  # file is open by the current track
        self.say(f"Cleared {count} cached files" + (" (skipped the one in use)" if playing else ""))
        self._refresh_usage()

    def action_quit_app(self) -> None:
        session.save(player)      # before stop(), which resets the position
        self._saved = True
        player.stop()
        self.exit()


def run() -> None:
    inline.detect()      # asks the terminal what it can draw; must happen before Textual takes over the screen
    TuneboxApp().run(mouse=True)
