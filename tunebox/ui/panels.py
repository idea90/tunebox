"""Container widgets the app's layout is built from: the player card, the lyrics pane and the tab strip."""
from typing import Any, Dict, List

from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, TabbedContent

from ..core.player import player
from . import inline
from .components import HEART_OFF, PLAY
from .visualizer import Visualizer
from .widgets import ArtView, Chip, SeekBar, VolumeBar


class TopBar(Static):
    """Slim title bar: the brand on the left; sleep timer and account state on the right."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._shown = None

    def show(self, signed_in: bool, sleep_minutes: int, downloading: str = "") -> None:
        th = self.app.current_theme
        state = (signed_in, sleep_minutes, th.primary, downloading)
        if state == self._shown:
            return
        self._shown = state
        left = Text()
        left.append("\u266a ", style=f"bold {th.primary}")
        left.append("TUNEBOX", style=f"bold {th.foreground}")
        left.append("   YouTube Music", style="#7d7d8a")
        right = Text()
        if downloading:
            right.append(f"\u2193 {downloading}   ", style=f"bold {th.primary}")
        if sleep_minutes:
            right.append(f"sleep {sleep_minutes}m   ", style=th.accent)
        right.append("\u25cf ", style=th.success if signed_in else "#7d7d8a")
        right.append("signed in" if signed_in else "not signed in", style="#7d7d8a")
        grid = Table.grid(expand=True)
        grid.add_column()
        grid.add_column(justify="right")
        grid.add_row(left, right)
        self.update(grid)


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
        yield Static("", id="np-status")
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
