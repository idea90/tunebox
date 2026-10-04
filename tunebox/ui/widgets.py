"""
Reusable Textual widgets for the Tunebox app: clickable chips, seek/volume
bars, and a track table with single-click activation.
"""
from typing import Any, Dict, List, Optional

from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Static

from ..config import config
from ..core import albumart
from ..core.player import player
from .components import format_seconds, HEART_ON, HEART_OFF, PLAY, PAUSE

TRACK_TYPES = ("song", "video")

# Quiet greys: colour is kept for the playing song, hearts and the selection.
MUTED = "#a1a1b0"
DIM = "#6b6b78"
HEART_OFF_STYLE = "#4a4a56"
ALBUM_MIN_WIDTH = 80          # the Album column appears on tables at least this wide


def center_text(text: Text, width: int) -> Text:
    """Pad every line of a no-wrap block so it sits in the middle of `width` columns."""
    out = Text(no_wrap=True)
    lines = text.split("\n", allow_blank=True)
    for i, line in enumerate(lines):
        out.append(" " * max(0, (width - line.cell_len) // 2))
        out.append_text(line)
        if i < len(lines) - 1:
            out.append("\n")
    return out


def is_track(item: Optional[Dict[str, Any]]) -> bool:
    return bool(item) and bool(item.get("videoId")) and item.get("type") not in ("album", "playlist", "artist", "local_playlist", "header")


def _fit(value: str, width: int, style: str = "") -> Text:
    """Truncate to a column width. Text() keeps '[brackets]' in titles from being read as markup."""
    t = Text(value or "", style=style, no_wrap=True)
    if width > 0:
        t.truncate(width, overflow="ellipsis")
    return t


class Chip(Static):
    """A one-line clickable label that runs an app action."""

    def __init__(self, label: str, action: str, **kwargs):
        super().__init__(label, **kwargs)
        self.add_class("chip")
        self._chip_action = action

    async def on_click(self, event: events.Click) -> None:
        if event.button == 1:
            event.stop()
            await self.app.run_action(self._chip_action)   # run_action is a coroutine: must be awaited


class SeekBar(Widget):
    """Progress bar; click anywhere to seek."""
    DEFAULT_CSS = "SeekBar { height: 1; }"

    def _labels(self):
        pos = player.get_position()
        dur = player.track_duration
        return f"{format_seconds(pos)} ", f" {format_seconds(dur)}", pos, dur

    def render(self) -> Text:
        left, right, pos, dur = self._labels()
        th = self.app.current_theme
        bar_w = max(4, self.size.width - len(left) - len(right))
        frac = max(0.0, min(1.0, pos / dur)) if dur > 0 else 0.0
        filled = int(round((bar_w - 1) * frac))
        out = Text()
        out.append(left, style=MUTED)
        out.append("━" * filled, style=th.primary)
        out.append("●", style="bold " + th.foreground)
        out.append("━" * (bar_w - 1 - filled), style="#33333d")
        out.append(right, style=MUTED)
        return out

    def on_click(self, event: events.Click) -> None:
        if event.button != 1:
            return
        event.stop()
        left, right, _, dur = self._labels()
        bar_w = max(4, self.size.width - len(left) - len(right))
        if dur <= 0 or not player.is_playing:
            return
        frac = max(0.0, min(1.0, (event.x - len(left)) / max(1, bar_w - 1)))
        self.app.seek_to(frac * dur)


class VolumeBar(Widget):
    """Volume slider; click to set, scroll wheel (handled by the parent) to nudge."""
    DEFAULT_CSS = "VolumeBar { height: 1; }"
    LABEL = "Vol "

    def render(self) -> Text:
        th = self.app.current_theme
        right = f" {player.volume:>3}%"
        bar_w = max(4, self.size.width - len(self.LABEL) - len(right))
        filled = int(round(bar_w * player.volume / 100))
        out = Text()
        out.append(self.LABEL, style=MUTED)
        out.append("━" * filled, style=th.secondary)
        out.append("━" * (bar_w - filled), style="#33333d")
        out.append(right, style=MUTED)
        return out

    def on_click(self, event: events.Click) -> None:
        if event.button != 1:
            return
        event.stop()
        bar_w = max(4, self.size.width - len(self.LABEL) - 5)
        vol = round(100 * (event.x - len(self.LABEL)) / max(1, bar_w - 1))
        player.set_volume(max(0, min(100, vol)))
        self.refresh()


class ArtView(Widget):
    """Album art for the playing song, drawn as ASCII or half-block pixels. Purely decorative."""
    DEFAULT_CSS = "ArtView { height: 12; margin: 0 1 1 1; }"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.image = None             # PIL image of the current cover, or None while loading / unavailable
        self._cache_key = None
        self._cache_val = None
        self._style_drawn = config.get("art_style", "auto")

    def set_image(self, image) -> None:
        self.image = image
        self.refresh()

    def sync_style(self) -> None:
        """Repaint when the style setting changed (render() alone is never called on a settings change)."""
        style = config.get("art_style", "auto")
        if style != self._style_drawn:
            self._style_drawn = style
            self.refresh()

    def render(self):
        style = config.get("art_style", "auto")
        if style not in ("ascii", "blocks"):      # auto / image: this text view is the ASCII fallback
            style = "ascii"
        width, height = self.size.width, self.size.height
        if self.image is not None:
            key = (id(self.image), width, height, style)
            if key != self._cache_key:       # resizing/colouring is the costly part: only redo it on change
                self._cache_key = key
                art = albumart.render_art(self.image, width, height, style)
                self._cache_val = center_text(art, width) if art is not None else None
            if self._cache_val is not None:
                return self._cache_val
        return center_text(Text("\n" * max(0, height // 2 - 1) + "\u266a", style="#3a3a46", no_wrap=True), width)

    def on_resize(self, event: events.Resize) -> None:
        self.refresh()


class TrackTable(DataTable):
    """DataTable that plays on a single click and exposes the heart column."""

    class Activated(Message):
        """A row was clicked (column set) or Enter was pressed (column None)."""
        def __init__(self, table: "TrackTable", row: int, column: Optional[int]) -> None:
            super().__init__()
            self.table = table
            self.row = row
            self.column = column

    def __init__(self, compact: bool = False, kind: str = "tracks", **kwargs):
        # The compact Up Next list is a plain read-out: no header, no selection bar, no stripes.
        super().__init__(cursor_type="row", zebra_stripes=not compact, show_header=not compact,
                         show_cursor=not compact, **kwargs)
        self.compact = compact
        self.kind = kind          # "tracks" | "queue" | "upnext" | "library" | "downloads"
        self.items: List[Dict[str, Any]] = []
        self._sig: List[Any] = []
        self._ctx = (set(), None, False, None)   # favs, current video id, playing, current index
        self._widths: Optional[tuple] = None
        self._dirty = False     # items changed while hidden/zero-width; rebuild once we can lay out
        self._touched = False   # the user has moved or clicked here, so the cursor is a real selection
        self._palette = None    # theme colours the cells were painted with; a theme change repaints them

    @property
    def heart_col(self) -> Optional[int]:
        if self.compact:
            return None
        return 5 if self._widths and self._widths[2] else 4

    # ------------------------------------------------------------ rendering

    def _compute_widths(self) -> tuple:
        """(title, artist, album) column widths; album is 0 when the table is too narrow to show it."""
        wide = not self.compact and self.size.width >= ALBUM_MIN_WIDTH
        ncols = 3 if self.compact else (6 if wide else 5)
        fixed = 4 if self.compact else 5 + 7 + 2
        avail = max(12, self.size.width - 2 * ncols - 2 - fixed)
        if wide:
            title, artist = int(avail * 0.4), int(avail * 0.3)
            return (title, artist, avail - title - artist)
        title = max(8, int(avail * (0.6 if self.compact else 0.55)))
        return (title, max(6, avail - title), 0)

    def _build_columns(self) -> None:
        self.clear(columns=True)
        t, a, al = self._widths or (20, 15, 0)
        self.add_column("#", width=4 if self.compact else 5)
        self.add_column("Title", width=t)
        self.add_column("Artist", width=a)
        if al:
            self.add_column("Album", width=al)
        if not self.compact:
            self.add_column("Time", width=7)
            self.add_column(HEART_ON, width=2)

    def _cells(self, i: int, item: Dict[str, Any], num: int) -> tuple:
        favs, cur_vid, playing, cur_idx = self._ctx
        th = self.app.current_theme
        t_w, a_w, al_w = self._widths or (20, 15, 0)
        typ = item.get("type", "song")

        def pack(num_c, title_c, artist_c, album_c, time_c, heart_c) -> tuple:
            """Cells in column order for this table's layout."""
            if self.compact:
                return (num_c, title_c, artist_c)
            return (num_c, title_c, artist_c) + ((album_c,) if al_w else ()) + (time_c, heart_c)

        if typ == "header":
            span = t_w + a_w + (al_w if al_w else 0)
            blank = Text("")
            return pack(blank, _fit(str(item.get("title", "")).upper(), span, "bold " + th.primary), blank, blank, blank, blank)

        v_id = item.get("videoId") or item.get("id") or ""
        if cur_idx is not None:
            is_curr = i == cur_idx
        else:
            is_curr = bool(cur_vid and v_id == cur_vid and is_track(item))
        band = ""
        if is_curr:       # the playing row: marker in the number column and a bold, coloured title
            num_t = Text(f"{PLAY if playing else PAUSE}{num}", style=f"bold {th.primary}")
            title_style = f"bold {th.primary}"
        else:
            num_t = Text(str(num), style=DIM)
            title_style = ""

        if is_track(item):
            artist = item.get("artist") or "Unknown"
            album = item.get("album")
            album = album.get("name", "") if isinstance(album, dict) else (album or "")
            secs = item.get("duration_seconds") or 0
            dur = item.get("duration") or (format_seconds(secs) if secs else "")   # unknown stays "--:--", not a fake 00:00
            if item.get("playCount"):
                dur = f"{item['playCount']}x"
            heart = (Text(HEART_ON, style="bold #ff4d6d" + band) if v_id in favs
                     else Text(HEART_OFF, style=HEART_OFF_STYLE + band))
            return pack(num_t, _fit(item.get("title", "Unknown"), t_w, title_style),
                        _fit(artist, a_w, MUTED + band), _fit(album, al_w, DIM + band),
                        Text(dur or "--:--", style=DIM + band, justify="right"), heart)

        tag = {"album": "ALBUM", "playlist": "PLAYLIST", "artist": "ARTIST", "local_playlist": "MY LIST"}.get(typ, typ.upper())
        sub = item.get("artist") or item.get("author") or item.get("description") or ""
        if typ == "local_playlist":
            sub = f"{item.get('trackCount', 0)} tracks"
        return pack(num_t, _fit(item.get("title") or item.get("name") or "Unknown", t_w, title_style),
                    _fit(sub, a_w, MUTED), Text(""), Text(tag[:7], style="bold " + th.secondary), Text(""))

    def _populate(self) -> None:
        self.clear()
        num = 0
        for i, item in enumerate(self.items):
            if item.get("type") != "header":
                num += 1
            self.add_row(*self._cells(i, item, num))

    def _rebuild(self) -> None:
        self._dirty = False
        keep_row = self.cursor_row
        self._widths = self._compute_widths()
        self._build_columns()
        self._populate()
        self._update_cursor_visibility()
        if self.row_count:
            self.move_cursor(row=min(keep_row, self.row_count - 1), animate=False)

    def _update_cursor_visibility(self) -> None:
        """A table that opens on a section title (Home) shows no selection bar until you move: the cursor is
        parked on that title, and highlighting it would look like a song is picked when none is."""
        if self.compact:
            return
        starts_with_title = bool(self.items) and self.items[0].get("type") == "header"
        self.show_cursor = self._touched or not starts_with_title

    def _touch(self) -> None:
        if not self._touched:
            self._touched = True
            self._update_cursor_visibility()

    def on_key(self, event: events.Key) -> None:
        if event.key in ("up", "down", "pageup", "pagedown", "home", "end", "enter"):
            self._touch()

    def _refresh_marks(self) -> None:
        """Update cells in place so scrolling is not disturbed."""
        from textual.coordinate import Coordinate
        num = 0
        for i, item in enumerate(self.items):
            if item.get("type") == "header":
                continue
            num += 1
            for col, cell in enumerate(self._cells(i, item, num)):
                self.update_cell_at(Coordinate(i, col), cell, update_width=False)

    def sync(self, items: List[Dict[str, Any]], favs: set, cur_vid: Optional[str], playing: bool,
             cur_idx: Optional[int] = None) -> None:
        th = self.app.current_theme
        palette = (th.primary, th.secondary)
        if palette != self._palette:
            self._palette = palette
            self._sig = []                    # the theme changed: rebuild so section titles and the playing row recolour
        sig = [(it.get("type"), it.get("videoId") or it.get("browseId") or it.get("id") or it.get("title")) for it in items]
        new_ctx = (favs, cur_vid, playing, cur_idx)
        if sig == self._sig and self._widths is not None:
            self.ensure_built()
            if new_ctx != self._ctx:
                self._ctx = new_ctx
                self.items = items
                self._refresh_marks()
            return
        self.items = items
        self._sig = sig
        self._ctx = new_ctx
        if self.size.width:
            self._rebuild()
        else:
            self._dirty = True

    def ensure_built(self) -> None:
        """Rebuild if content changed while we couldn't lay out, or the width changed. Safe to call any time."""
        if self.size.width and (self._dirty or self._compute_widths() != self._widths):
            self._rebuild()

    def on_resize(self, event: events.Resize) -> None:
        self.ensure_built()

    def on_show(self, event: events.Show) -> None:
        self.ensure_built()

    # ------------------------------------------------------------ input

    async def _on_click(self, event: events.Click) -> None:
        event.prevent_default()  # skip DataTable's own double-click-to-select handler
        if event.button != 1:
            return  # let right-click bubble up to the app (play/pause)
        meta = event.style.meta
        if "row" not in meta or "column" not in meta:
            return
        row, col = meta["row"], meta["column"]
        if row < 0 or col < 0 or row >= self.row_count:
            return
        event.stop()
        self._touch()
        self.move_cursor(row=row, animate=False)
        self.post_message(self.Activated(self, row, col))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.post_message(self.Activated(self, event.cursor_row, None))

    def selected_item(self) -> Optional[Dict[str, Any]]:
        if 0 <= self.cursor_row < len(self.items):
            return self.items[self.cursor_row]
        return None
