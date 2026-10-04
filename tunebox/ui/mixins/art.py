"""Cover art in the sidebar, and the row budget it shares with the visualizer."""
from typing import Any, Dict, Optional

from ...config import config
from ...core import albumart
from ...core.player import player
from .. import inline
from ..widgets import ArtView


class ArtMixin:
    """Cover art in the sidebar (graphics-protocol image or text art) and the sidebar row budget it shares
    with the visualizer."""

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
