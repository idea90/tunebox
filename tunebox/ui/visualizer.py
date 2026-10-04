"""
Audio visualizer for the player card.

The bars show the real spectrum of the playing song (see core/spectrum.py) at the current playback
position, redrawn 20 times a second with instant rise, smooth fall and falling peak caps.

Styles:  bars (with peak caps) | mirror (bars plus a dimmer reflection) | line | off
Colours: theme (your theme's colours) | rainbow | fire | mono
"""
import colorsys

import numpy as np
from rich.text import Text
from textual.widget import Widget

from ..config import config
from ..core import spectrum
from ..core.player import player

VIZ_STYLES = ["bars", "mirror", "line", "off"]
VIZ_COLORS = ["theme", "rainbow", "fire", "mono"]
EIGHTHS = " ▁▂▃▄▅▆▇█"      # " ▁▂▃▄▅▆▇█"
BASELINE = "#3a3a44"


def _hex_to_rgb(value: str) -> tuple:
    value = (value or "#888888").lstrip("#")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (136, 136, 136)


def _blend(a: tuple, b: tuple, t: float) -> str:
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def viz_color(scheme: str, x: float, y: float, primary: str = "#d946ef", accent: str = "#facc15") -> str:
    """Colour of one cell. x: 0 left .. 1 right, y: 0 bottom .. 1 top."""
    if scheme == "rainbow":
        r, g, b = colorsys.hsv_to_rgb(0.85 * x, 0.7, 1.0)
        return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
    if scheme == "fire":
        if y < 0.5:
            return _blend((255, 40, 0), (255, 140, 0), y * 2)
        return _blend((255, 140, 0), (255, 235, 60), (y - 0.5) * 2)
    if scheme == "mono":
        return _blend((110, 110, 120), (235, 235, 240), y)
    return _blend(_hex_to_rgb(primary), _hex_to_rgb(accent), y)


def resample_bands(levels, count: int) -> np.ndarray:
    """Group the analysis bands into `count` bars, each showing its LOUDEST band. Averaging would flatten a
    clear note (one hot band among quiet neighbours) to a fraction of its height."""
    levels = np.asarray(levels, dtype=np.float32)
    if count <= 0 or len(levels) == 0:
        return np.zeros(max(0, count), np.float32)
    starts = np.minimum((np.arange(count) * len(levels)) // count, len(levels) - 1)
    return np.maximum.reduceat(levels, starts).astype(np.float32)


def bar_layout(width: int, style: str) -> tuple:
    """(number of bars, columns per bar). Bars get a 1-cell gap when there is room; 'line' uses every column."""
    if width <= 0:
        return 0, 1
    if style == "line" or width < 24:
        return width, 1
    return (width + 1) // 2, 2


def render_viz(levels, peaks, width: int, height: int, style: str, scheme: str,
               primary: str = "#d946ef", accent: str = "#facc15") -> Text:
    """Draw bar levels (0..1) as coloured text. A pure function, so every style can be tested directly."""
    out = Text(no_wrap=True)
    n = len(levels)
    if n == 0 or width <= 0 or height <= 0 or style not in ("bars", "mirror", "line"):
        return out
    _, step = bar_layout(width, style)
    used = n * step - (step - 1)
    pad = " " * max(0, (width - used) // 2)
    tail = " " * max(0, width - used - len(pad))
    xs = [i / max(1, n - 1) for i in range(n)]

    def paint(i: int, ch: str, y: float, dim: bool = False) -> None:
        colour = viz_color(scheme, xs[i], y, primary, accent)
        if dim:
            colour = _blend(_hex_to_rgb(colour), (20, 20, 24), 0.55)
        out.append(ch, style=colour)

    def gap(i: int) -> None:
        if step == 2 and i < n - 1:
            out.append(" ")

    if style == "mirror":
        up = height - height // 2                     # rows above the middle line
        down = height - up
        for r in range(height):
            out.append(pad)
            for i in range(n):
                v = float(levels[i])
                if r < up:                            # grows up from the middle
                    fill = max(0.0, min(1.0, v * up - (up - 1 - r)))
                    ch = EIGHTHS[int(round(fill * 8))]
                    if ch == " " and r == up - 1:
                        out.append("▁", style=BASELINE)
                    else:
                        paint(i, ch, (up - r) / up)
                else:                                 # dimmer reflection grows down
                    j = r - up
                    fill = max(0.0, min(1.0, v * down - j))
                    ch = "█" if fill >= 0.75 else ("▀" if fill >= 0.25 else " ")
                    paint(i, ch, (j + 1) / max(1, down), dim=True)
                gap(i)
            out.append(tail)
            if r < height - 1:
                out.append("\n")
        return out

    for r in range(height):
        out.append(pad)
        row = height - 1 - r                          # 0 = bottom row
        y = (row + 1) / height
        for i in range(n):
            v = float(levels[i])
            if style == "line":
                top = min(height - 1, int(v * height - 1e-6)) if v > 0.02 else -1
                if top == row:
                    paint(i, "━", y)
                elif row == 0 and top < 0:
                    out.append("━", style=BASELINE)
                else:
                    out.append(" ")
            else:
                fill = max(0.0, min(1.0, v * height - row))
                ch = EIGHTHS[int(round(fill * 8))]
                peak = float(peaks[i]) if peaks is not None and i < len(peaks) else 0.0
                peak_row = min(height - 1, int(peak * height - 1e-6)) if peak > 0.05 else -1
                if ch == " " and peak_row == row and peak > v + 0.03:
                    paint(i, "▔", y)             # falling peak cap
                elif ch == " " and row == 0:
                    out.append("▁", style=BASELINE)
                else:
                    paint(i, ch, y)
            gap(i)
        out.append(tail)
        if r < height - 1:
            out.append("\n")
    return out


class Visualizer(Widget):
    """Spectrum of the playing song, in sync with the playback position."""

    DEFAULT_CSS = "Visualizer { height: 4; margin: 0 1 1 1; }"
    FPS = 20
    DECAY = 0.78          # fraction of its height a bar keeps each frame when the music gets quieter
    PEAK_FALL = 0.025     # how far the peak caps drop each frame

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.spectrum = None                      # (frames, bands) or None until the song is analysed
        self.levels = np.zeros(0, np.float32)
        self.peaks = np.zeros(0, np.float32)

    def on_mount(self) -> None:
        self.set_interval(1 / self.FPS, self.step)

    def step(self) -> None:
        """Advance one animation frame (only does work while visible and switched on)."""
        style = config.get("viz_style", "bars")
        if not self.display or style not in ("bars", "mirror", "line"):
            return
        n, _ = bar_layout(self.size.width, style)
        resized = len(self.levels) != n
        if resized:
            self.levels = np.zeros(n, np.float32)
            self.peaks = np.zeros(n, np.float32)
        target = np.zeros(n, np.float32)
        if player.is_playing and not player.is_paused and not player.is_loading:
            frame = spectrum.frame_at(self.spectrum, player.get_position())
            if frame is not None:
                target = resample_bands(frame, n)
        before = self.levels
        self.levels = np.maximum(target, before * self.DECAY)
        self.levels[self.levels < 0.01] = 0.0
        self.peaks = np.maximum(self.levels, self.peaks - self.PEAK_FALL)
        if resized or before.any() or self.levels.any() or self.peaks.any():
            self.refresh()                        # idle and silent: no repaint at all

    def render(self) -> Text:
        th = self.app.current_theme
        return render_viz(self.levels, self.peaks, self.size.width, self.size.height,
                          config.get("viz_style", "bars"), config.get("viz_colors", "theme"),
                          primary=th.primary, accent=th.accent)
