"""
Album art for the terminal.

    fetch_image(track)        download a track's thumbnail (cached in memory)
    render_art(img, w, h, s)  turn an image into a Rich Text block

Styles:
    "ascii"   colored characters chosen by brightness (classic ASCII art)
    "blocks"  half-block pixels, two image rows per text row (sharper)
    "off"     no art
"""
import io
import re
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional

import requests
from PIL import Image
from rich.text import Text

STYLES = ["auto", "ascii", "blocks", "off"]

# Dark to light. Brightness picks the character; the pixel's own color tints it.
RAMP = " .:-=+*#%@"

_CACHE: "OrderedDict[str, Image.Image]" = OrderedDict()
_CACHE_MAX = 24
_LOCK = threading.Lock()


def hi_res_url(url: str, size: int = 400) -> str:
    """YouTube/Google thumbnail URLs encode their size (=w120-h120...). Ask for a bigger one."""
    if not url:
        return url
    return re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url)


def fetch_image(track: Dict[str, Any]) -> Optional[Image.Image]:
    """Download the track's cover. Returns None on any failure (art is decoration, never an error)."""
    url = hi_res_url(track.get("thumbnail") or "")
    if not url:
        return None
    with _LOCK:
        if url in _CACHE:
            _CACHE.move_to_end(url)
            return _CACHE[url]
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        img.load()
        img = img.convert("RGB")
    except Exception:
        return None
    with _LOCK:
        _CACHE[url] = img
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return img


def trim_borders(img: Image.Image, threshold: int = 14) -> Image.Image:
    """Remove solid black borders (YouTube's 16:9 video thumbnails are letterboxed).

    Only trims when it removes a meaningful strip and leaves most of the picture, so a genuinely
    dark cover is never cropped down to a speck.
    """
    mask = img.convert("L").point(lambda p: 255 if p > threshold else 0)
    box = mask.getbbox()
    if not box:
        return img
    w, h = img.size
    kept = (box[2] - box[0]) * (box[3] - box[1])
    removed_w, removed_h = w - (box[2] - box[0]), h - (box[3] - box[1])
    if kept < 0.4 * w * h or (removed_w < 0.04 * w and removed_h < 0.04 * h):
        return img
    return img.crop(box)


def center_square(img: Image.Image) -> Image.Image:
    """Crop to the centered square. Song thumbnails are often 16:9 with the cover in the middle."""
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def square_for_display(img: Image.Image, size: int = 400) -> Image.Image:
    """The cover as shown to a terminal graphics protocol: borders trimmed, centred square, bounded size."""
    sq = center_square(trim_borders(img))
    if sq.width > size:
        sq = sq.resize((size, size), Image.Resampling.LANCZOS)
    return sq


def footprint(max_width: int, max_height: int) -> tuple:
    """(columns, rows) for a square image. Terminal cells are about twice as tall as wide."""
    cols = max(2, min(max_width, max_height * 2))
    return cols - (cols % 2), max(1, (cols - (cols % 2)) // 2)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % rgb[:3]


def _boost(rgb, floor: int = 70) -> tuple:
    """Keep dark pixels legible on a dark terminal."""
    r, g, b = rgb[:3]
    peak = max(r, g, b)
    if peak >= floor or peak == 0:
        return (r, g, b)
    k = floor / peak
    return (min(255, int(r * k)), min(255, int(g * k)), min(255, int(b * k)))


def render_art(img: Optional[Image.Image], max_width: int, max_height: int, style: str = "ascii") -> Optional[Text]:
    """Render `img` into at most max_width x max_height cells. None when art is off or there is no room."""
    if img is None or style not in ("ascii", "blocks") or max_width < 4 or max_height < 2:
        return None

    cols, rows = footprint(max_width, max_height)
    sq = center_square(trim_borders(img))
    out = Text(no_wrap=True, justify="center")

    if style == "blocks":
        px = sq.resize((cols, rows * 2), Image.Resampling.LANCZOS).load()
        for y in range(rows):
            for x in range(cols):
                top, bottom = px[x, y * 2], px[x, y * 2 + 1]
                out.append("▀", style=f"{_hex(top)} on {_hex(bottom)}")
            if y < rows - 1:
                out.append("\n")
        return out

    px = sq.resize((cols, rows), Image.Resampling.LANCZOS).load()
    for y in range(rows):
        for x in range(cols):
            rgb = px[x, y]
            lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0
            char = RAMP[min(len(RAMP) - 1, int(lum * len(RAMP)))]
            out.append(char, style=_hex(_boost(rgb)))
        if y < rows - 1:
            out.append("\n")
    return out
