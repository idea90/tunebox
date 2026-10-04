"""Live lyrics: fetch for the playing song and draw the sidebar / full-size panes."""
from typing import Any, Dict

from rich.text import Text
from textual.widgets import Static

from ...core.lyrics import get_lyrics
from ...core.player import player
from ..components import PLAY
from ..constants import LYRICS_TOP_ROWS
from ..panels import LyricsView


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

    def _fetch_lyrics(self, track: Dict[str, Any]) -> None:
        data = get_lyrics(track.get("title", ""), artist_name=track.get("artist", ""),
                          duration=track.get("duration_seconds"), video_id=track.get("videoId"))
        if track.get("videoId") == self.lyrics_vid:  # ignore stale results
            self._ui(self._set_lyrics, data)

    def _set_lyrics(self, data) -> None:
        self.lyrics = data
        self.lyrics_loading = False
        self._refresh_lyrics()
