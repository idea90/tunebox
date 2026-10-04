"""
Album art for the terminal.

    find_cover_url(track)     official album cover from iTunes, then Deezer (cached; None if not found)
    best_cover_url(track)     that, else YouTube Music's own square album art, never a video frame
    fetch_image(track)        download the cover (cached in memory)
    render_art(img, w, h, s)  turn an image into a Rich Text block

Styles:
    "ascii"   colored characters chosen by brightness (classic ASCII art)
    "blocks"  half-block pixels, two image rows per text row (sharper)
    "off"     no art
"""
import io
import re
import threading
import unicodedata
from collections import OrderedDict
from typing import Any, Dict, Optional

import requests
from PIL import Image
from rich.text import Text

from ..config import config

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


# ------------------------------------------------------------------ official cover lookup

ITUNES_URL = "https://itunes.apple.com/search"
DEEZER_URL = "https://api.deezer.com/search"
LOOKUP_TIMEOUT = 6
_LOOKUP: "OrderedDict[str, Optional[str]]" = OrderedDict()   # track key -> cover url (None = nothing found)
_LOOKUP_MAX = 256


def _norm(text: str) -> str:
    """Lowercase, accent-free, no (brackets), no 'feat.' tail, no punctuation: for comparing titles and names."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", text)
    text = re.sub(r"\s+(feat|ft|featuring)\.?\s.*$", "", text)
    text = re.sub(r"\s+-\s+(remaster(ed)?|single|radio|live|mono|stereo|version|edit)\b.*$", "", text)
    text = re.sub(r"\s+-\s+topic$", "", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _artist_names(track: Dict[str, Any]) -> list:
    names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in (track.get("artists") or [])]
    if not any(names) and track.get("artist"):
        names = [n for n in re.split(r",|&|\band\b", track["artist"])]
    return [n for n in (_norm(n) for n in names) if n]


def _album_name(track: Dict[str, Any]) -> str:
    album = track.get("album")
    return _norm(album.get("name", "") if isinstance(album, dict) else (album or ""))


def _loosely_equal(a: str, b: str) -> bool:
    return bool(a and b and (a == b or a in b or b in a))


def _pick(candidates: list, track: Dict[str, Any]) -> Optional[str]:
    """Best cover among (title, artist, album, url) candidates, or None when nothing really is this song.

    The title must match and the artist must match one of the track's artists; a candidate from the
    same album as the track wins over the same song on a compilation or single.
    """
    title, artists, album = _norm(track.get("title", "")), _artist_names(track), _album_name(track)
    best, best_score = None, 0
    for c_title, c_artist, c_album, url in candidates:
        c_title, c_artist, c_album = _norm(c_title), _norm(c_artist), _norm(c_album)
        if not url or not _loosely_equal(title, c_title):
            continue
        if artists and not any(_loosely_equal(a, c_artist) for a in artists):
            continue
        score = 2 if title == c_title else 1
        if album and _loosely_equal(album, c_album):
            score += 2
        if score > best_score:                   # strict '>': on a tie the API's own relevance order wins
            best, best_score = url, score
    return best


def _lookup_itunes(term: str, track: Dict[str, Any]):
    resp = requests.get(ITUNES_URL, params={"term": term, "entity": "song", "media": "music", "limit": 10},
                        timeout=LOOKUP_TIMEOUT)
    resp.raise_for_status()
    cands = [(r.get("trackName", ""), r.get("artistName", ""), r.get("collectionName", ""),
              re.sub(r"/\d+x\d+(bb)?\.", "/1000x1000bb.", r.get("artworkUrl100") or ""))
             for r in resp.json().get("results", [])]
    return _pick(cands, track)


def _lookup_deezer(term: str, track: Dict[str, Any]):
    resp = requests.get(DEEZER_URL, params={"q": term, "limit": 10}, timeout=LOOKUP_TIMEOUT)
    resp.raise_for_status()
    cands = [(r.get("title", ""), (r.get("artist") or {}).get("name", ""), (r.get("album") or {}).get("title", ""),
              (r.get("album") or {}).get("cover_xl") or (r.get("album") or {}).get("cover_big") or "")
             for r in resp.json().get("data", [])]
    return _pick(cands, track)


def find_cover_url(track: Dict[str, Any]) -> Optional[str]:
    """Official album cover (1000 px square) for this song from iTunes, then Deezer. None if neither knows it.

    Turned off by the `online_covers` setting. Results are remembered (misses too), but a network error is not,
    so an offline start doesn't blank the art for the rest of the session.
    """
    if not config.get("online_covers", True) or not track.get("title"):
        return None
    artists = _artist_names(track)
    key = f"{artists[0] if artists else ''}|{_norm(track['title'])}|{_album_name(track)}"
    with _LOCK:
        if key in _LOOKUP:
            _LOOKUP.move_to_end(key)
            return _LOOKUP[key]

    term = f"{artists[0] if artists else ''} {_norm(track['title'])}".strip()
    url, failed = None, False
    for lookup in (_lookup_itunes, _lookup_deezer):
        try:
            url = lookup(term, track)
        except Exception:
            failed = True
            continue
        if url:
            break
    if not url and failed:
        return None
    with _LOCK:
        _LOOKUP[key] = url
        while len(_LOOKUP) > _LOOKUP_MAX:
            _LOOKUP.popitem(last=False)
    return url


def _own_art(track: Dict[str, Any]) -> Optional[str]:
    """YouTube Music's own album art (square, on googleusercontent). Video frames from ytimg are never used."""
    url = track.get("thumbnail") or ""
    if not url or "ytimg.com" in url or "youtube.com" in url:
        return None
    return hi_res_url(url)


def best_cover_url(track: Dict[str, Any]) -> Optional[str]:
    """The official cover if one is found, else YouTube Music's album art, else nothing."""
    return find_cover_url(track) or _own_art(track)


def fetch_image(track: Dict[str, Any]) -> Optional[Image.Image]:
    """Download the track's cover. Returns None on any failure (art is decoration, never an error)."""
    url = best_cover_url(track)
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
