"""Live lyrics: fetch for the playing song and draw the sidebar / full-size panes."""
from typing import Any, Dict

from rich.text import Text
from textual.widgets import Static

from ...core.lyrics import get_lyrics
from ...core.player import player
from ..components import PLAY
from ..constants import LYRICS_TOP_ROWS
from ..panels import LyricsView

DIM = "#6b6b78"
# Lines fade the further they are from the line being sung: next to it, then 2, 3, ... away.
FADE = ["#d0d0da", "#a1a1b0", "#7d7d8a", "#5d5d68", "#4a4a54"]


def _centered_note(message: str, view) -> Text:
    """A one-line message in the middle of the pane (below the usual 2 header rows, so clicks still map)."""
    width = max(10, view.size.width - 8)
    return Text("\n\n" + " " * max(0, (width - len(message)) // 2) + message, style=DIM)


class LyricsMixin:
    """Live lyrics: fetch for the playing song and draw the sidebar / full-size panes."""

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
            full.update(_centered_note("Play a song to see its lyrics.", full))
            return
        if self.lyrics_loading:
            mini.update(Text("Loading lyrics...", style=DIM))
            full.update(_centered_note("Loading lyrics...", full))
            return

        synced = self.lyrics.get("synced")
        active = 0
        if synced:
            for i, ln in enumerate(lines):
                if ln.get("time") is not None and ln["time"] <= pos:
                    active = i
                else:
                    break
        primary = self.current_theme.primary

        def render(height: int, before: int, width: int = 0, centre: bool = False):
            """Text for `height` rows starting `before` rows above the active line. One row per lyric line (long
            lines are cut with an ellipsis, never wrapped) so a click maps straight to a line."""
            out = Text(no_wrap=True)

            def put(txt: str, style: str, mark: bool = False) -> None:
                line = Text((f"{PLAY} " if mark else "  ") + txt if not centre else txt, no_wrap=True)
                if width:
                    line.truncate(width, overflow="ellipsis")
                if centre:
                    out.append(" " * max(0, (width - line.cell_len) // 2))
                out.append_text(Text(line.plain, style=style))
                out.append("\n")

            if synced:
                start = max(0, min(active - before, max(0, len(lines) - height)))
                for i in range(start, min(len(lines), start + height)):
                    txt = lines[i].get("text", "")
                    if i == active:
                        put(txt, f"bold {primary}", mark=not centre)
                    else:
                        put(txt, FADE[min(abs(i - active), len(FADE)) - 1])
                return out, start
            for ln in lines[:height]:
                put(ln.get("text", ""), FADE[0])
            return out, 0

        mini_text, _ = render(7, 2, width=max(0, mini.size.width - 2))
        mini.update(mini_text)
        if self.active_tab == "lyrics":
            h = max(5, full.size.height - 2)
            width = max(10, full.size.width - 8)
            text, start = render(h, h // 2, width=width, centre=True)
            source = self.lyrics.get("source") or ""
            label = "" if source in ("", "None") else (f"Synced lyrics  \u00b7  {source}" if synced else f"Lyrics  \u00b7  {source}")
            header = Text(" " * max(0, (width - len(label)) // 2) + label + "\n\n", style=DIM)   # always 2 rows
            full.update(header + text)
            full.window_start = start - LYRICS_TOP_ROWS

    def _fetch_lyrics(self, track: Dict[str, Any]) -> None:
        data = get_lyrics(track.get("title", ""), artist_name=track.get("artist", ""),
                          duration=track.get("duration_seconds"), video_id=track.get("videoId"))
        if track.get("videoId") == self.lyrics_vid:  # ignore stale results
            self._ui(self._set_lyrics, data)

    def _set_lyrics(self, data) -> None:
        self.lyrics = data
        self.lyrics_loading = False
        self._refresh_lyrics()
