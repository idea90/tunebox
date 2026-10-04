"""
Tunebox interactive app (Textual).

Real event loop: live progress/lyrics, single-click playback, clickable
controls, seek bar, volume bar, scroll-wheel volume and right-click pause.

This module holds the app shell (layout, key bindings, lifecycle). The behaviour lives in the feature
mixins under ui/mixins/; the stylesheet in ui/styles.py, shared constants in ui/constants.py.
"""
import asyncio
import threading
from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Footer, Input, Select, Static, TabbedContent, TabPane

from ..config import config
from ..core import mediakeys, mpris, session
from ..core.player import player
from . import inline
from .constants import SEARCH_FILTERS
from .mixins import (
    ArtMixin, ClipboardMixin, DataMixin, DownloadsMixin, HelpersMixin, LibraryMixin, LyricsMixin, NavigationMixin,
    PlaybackMixin, RefreshMixin, RemoteMixin, SettingsMixin, VizMixin,
)
from .panels import AppTabs, LyricsView, NowPlaying, TopBar
from .styles import APP_CSS
from .suggest import SearchSuggester
from .theme import THEMES, TEXTUAL_PALETTES
from .widgets import Chip, TrackTable


class TuneboxApp(HelpersMixin, DataMixin, DownloadsMixin, RefreshMixin, LyricsMixin, ArtMixin, VizMixin, NavigationMixin,
                 PlaybackMixin, LibraryMixin, ClipboardMixin, SettingsMixin, RemoteMixin, App):
    TITLE = "TUNEBOX"
    SUB_TITLE = "YouTube Music"
    ENABLE_COMMAND_PALETTE = False
    # Textual activates a tab whenever a widget inside its pane gains focus. Screen auto-focus at startup
    # would therefore race with (and revert) a tab switch, so focus is placed explicitly instead.
    AUTO_FOCUS = None

    CSS = APP_CSS

    BINDINGS = [
        Binding("space", "play_pause", "Play/Pause"),
        Binding("n", "next", "Next"),
        Binding("p", "prev", "Prev"),
        Binding("f", "favorite", "Fav"),
        Binding("s", "shuffle", "Shuffle"),
        Binding("r", "repeat", "Repeat"),
        Binding("a", "autoplay", "Auto"),
        Binding("d", "download", "Download"),
        Binding("D", "download_all", "Download all", show=False),
        Binding("q", "quit_app", "Quit"),
        Binding("question_mark", "help", "Help"),
        Binding("plus,equals_sign", "vol(5)", "Vol+", show=False),
        Binding("minus", "vol(-5)", "Vol-", show=False),
        Binding("right_square_bracket", "skip(10)", "+10s", show=False),
        Binding("left_square_bracket", "skip(-10)", "-10s", show=False),
        Binding("R", "radio", "Radio", show=False),
        Binding("P", "add_to_playlist", "To playlist", show=False),
        Binding("x", "remove_selected", "Remove", show=False),
        Binding("c", "clear_queue", "Clear queue", show=False),
        Binding("S", "save_queue", "Save queue as playlist", show=False),
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
        self.batch: Optional[Dict[str, Any]] = None       # the whole-list download in progress, if any
        self._saved = False      # session already written by an explicit quit (stop() zeroes the position)
        self._sig = None
        self._ui_thread = threading.get_ident()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="body"):
            with Vertical(id="main"):
                with AppTabs(initial="home", id="tabs"):
                    with TabPane("1 Home", id="home"):
                        yield TrackTable(id="t-home")
                        yield Static("", id="e-home", classes="empty")
                    with TabPane("2 Search", id="search"):
                        with Horizontal(id="search-bar"):
                            yield Input(placeholder="Search songs, albums, artists...   ( / to focus,  \u2192 to accept a suggestion )",
                                        id="search-input", suggester=SearchSuggester())
                            yield Select(SEARCH_FILTERS, value="all", allow_blank=False, id="search-filter")
                        yield TrackTable(id="t-search")
                        yield Static("", id="e-search", classes="empty")
                    with TabPane("3 Queue", id="queue"):
                        yield TrackTable(kind="queue", id="t-queue")
                        yield Static("", id="e-queue", classes="empty")
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
                        yield Static("", id="e-library", classes="empty")
                    with TabPane("6 Downloads", id="downloads"):
                        yield TrackTable(kind="downloads", id="t-downloads")
                        yield Static("", id="e-downloads", classes="empty")
                    with TabPane("7 Settings", id="settings"):
                        with VerticalScroll(id="settings-box"):
                            yield Static("APPEARANCE", classes="settings-title")
                            with Grid(classes="settings-grid"):
                                yield Chip("", "cycle_theme", id="s-theme", classes="setting")
                                yield Chip("", "cycle_art", id="s-art", classes="setting")
                                yield Chip("", "cycle_viz", id="s-viz", classes="setting")
                                yield Chip("", "cycle_viz_colors", id="s-vizc", classes="setting")
                            yield Static("PLAYBACK", classes="settings-title")
                            with Grid(classes="settings-grid"):
                                yield Chip("", "autoplay", id="s-auto", classes="setting")
                                yield Chip("", "shuffle", id="s-shuf", classes="setting")
                                yield Chip("", "repeat", id="s-rep", classes="setting")
                                yield Chip("", "sleep_cycle", id="s-sleep", classes="setting")
                                yield Chip("", "toggle_normalize", id="s-norm", classes="setting")
                                yield Chip("", "toggle_gapless", id="s-gapless", classes="setting")
                            yield Static("STORAGE AND ACCOUNT", classes="settings-title")
                            with Grid(classes="settings-grid"):
                                yield Chip("", "cycle_download_format", id="s-dlfmt", classes="setting")
                                yield Chip("Clear audio cache", "clear_cache", id="s-cache", classes="setting")
                            yield Static("", id="s-usage")
                            yield Static("", id="s-account")
                            yield Static("Click a setting to change it. Volume: scroll over the player card.", id="settings-note")
                    with TabPane("8 Details", id="detail"):
                        yield Static("Nothing selected", id="d-title")
                        yield Static("Click an artist, album or playlist to open it here.", id="d-sub")
                        with Horizontal(id="d-actions"):
                            yield Chip("Play all", "play_all", id="d-play")
                            yield Chip("Shuffle", "shuffle_play", id="d-shuffle")
                            yield Chip("Download all", "download_all", id="d-download")
                            yield Chip("Back", "detail_back", id="d-back")
                        yield TrackTable(kind="detail", id="t-detail")
                        yield Static("Albums & singles", id="d-albums-label")
                        yield TrackTable(kind="detail", id="t-detail-albums")
            with Vertical(id="sidebar"):
                yield NowPlaying(id="np")
                yield Static("UP NEXT", classes="section")
                yield TrackTable(compact=True, kind="upnext", id="t-upnext")
                yield Static("LYRICS", id="mini-lyrics-title")
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
        self._start_mpris()
        self.call_after_refresh(self._hide_details_tab)
        self.call_after_refresh(self._relabel_tabs)
        self.set_interval(15, lambda: session.save(player))   # so a crash loses at most 15 s
        if config.load_error:
            self.say(config.load_error, True)
        if not player.audio_ok:
            self.say("No audio output device found - playback is unavailable.", True)

        self.set_interval(0.5, self.tick)
        self._bg(self._load_home)
        self.call_after_refresh(self.tick)

    def _hide_details_tab(self) -> None:
        """The Details tab only appears once you open an artist / album / playlist."""
        try:
            tabs = self.query_one(TabbedContent)
            if tabs.active != "detail":
                tabs.hide_tab("detail")
        except Exception:
            pass

    def on_unmount(self) -> None:
        if not self._saved:
            session.save(player)
        mediakeys.stop()
        mpris.stop()

    def _media_action(self, action: str) -> None:
        if action == "play_pause":
            self.action_play_pause()
        elif action == "next":
            self.action_next()
        elif action == "prev":
            self.action_prev()
        elif action == "stop":
            player.stop()

    def action_quit_app(self) -> None:
        session.save(player)      # before stop(), which resets the position
        self._saved = True
        player.stop()
        self.exit()


def run() -> None:
    inline.detect()      # asks the terminal what it can draw; must happen before Textual takes over the screen
    TuneboxApp().run(mouse=True)
