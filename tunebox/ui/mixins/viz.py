"""The spectrum visualizer: size it (budget comes from ArtMixin), analyse each new song, cycle style / colours."""
from ...config import config
from ...core import spectrum
from ...core.player import player
from ..visualizer import VIZ_COLORS, VIZ_STYLES, Visualizer


class VizMixin:
    """The spectrum visualizer: size it (budget comes from ArtMixin), analyse each new song, cycle style / colours."""

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
