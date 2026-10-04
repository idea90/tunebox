"""Album art: URL upscaling, ASCII/block rendering, fetching + caching, and the UI wiring."""
import io

import pygame
import pytest
from PIL import Image
from textual.widgets import TabbedContent

from tunebox.config import config
from tunebox.core import albumart
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui.app import TuneboxApp
from tunebox.ui.widgets import ArtView


def gradient(w=64, h=64, rgb=(255, 80, 0)):
    """Left side black, right side `rgb`."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for x in range(w):
        for y in range(h):
            k = x / (w - 1)
            px[x, y] = tuple(int(c * k) for c in rgb)
    return img


async def wait_for(pilot, cond, timeout=5.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


# ------------------------------------------------------------------ pure functions

def test_hi_res_url_requests_a_bigger_thumbnail():
    assert albumart.hi_res_url("https://lh3.googleusercontent.com/abc=w120-h120-l90-rj") == \
        "https://lh3.googleusercontent.com/abc=w400-h400-l90-rj"
    assert albumart.hi_res_url("https://i.ytimg.com/vi/x/hqdefault.jpg") == "https://i.ytimg.com/vi/x/hqdefault.jpg"
    assert albumart.hi_res_url("") == ""


def test_center_square_crops_wide_thumbnails():
    img = Image.new("RGB", (160, 90), (0, 0, 0))
    img.paste((255, 255, 255), (35, 0, 125, 90))             # a 90px white square in the middle
    sq = albumart.center_square(img)
    assert sq.size == (90, 90) and sq.getpixel((45, 45)) == (255, 255, 255) and sq.getpixel((2, 2)) == (255, 255, 255)


@pytest.mark.parametrize("w,h,expect", [(40, 10, (20, 10)), (12, 20, (12, 6)), (3, 3, (2, 1))])
def test_footprint_keeps_cells_roughly_square(w, h, expect):
    assert albumart.footprint(w, h) == expect


def test_ascii_render_dimensions_characters_and_color():
    art = albumart.render_art(gradient(rgb=(255, 255, 255)), 40, 10, "ascii")
    lines = art.plain.split("\n")
    assert len(lines) == 10 and all(len(l) == 20 for l in lines)
    assert set(art.plain) <= set(albumart.RAMP + "\n")
    row = lines[0]
    assert row[0] == " " and row[-1] == "@"                    # dark side blank, white side densest glyph

    orange = albumart.render_art(gradient(), 40, 10, "ascii")  # saturated orange is mid-luminance: a lighter glyph
    assert orange.plain.split("\n")[0][-1] in "=+*"
    assert any(str(sp.style).startswith("#f") for sp in orange.spans), "glyphs keep the cover's own colour"


def test_dark_pixels_stay_legible():
    art = albumart.render_art(Image.new("RGB", (32, 32), (10, 5, 0)), 20, 5, "ascii")
    # every glyph is tinted at least to the legibility floor instead of vanishing into the background
    peaks = [max(int(str(sp.style)[i:i + 2], 16) for i in (1, 3, 5)) for sp in art.spans]
    assert peaks and min(peaks) >= 70


def test_block_render_uses_half_blocks_with_fg_and_bg():
    art = albumart.render_art(gradient(), 40, 10, "blocks")
    lines = art.plain.split("\n")
    assert len(lines) == 10 and all(set(l) == {"▀"} and len(l) == 20 for l in lines)
    assert all(" on #" in str(sp.style) for sp in art.spans)


def test_no_art_when_off_missing_or_no_room():
    img = gradient()
    assert albumart.render_art(img, 40, 10, "off") is None
    assert albumart.render_art(None, 40, 10, "ascii") is None
    assert albumart.render_art(img, 3, 10, "ascii") is None
    assert albumart.render_art(img, 40, 1, "ascii") is None


# ------------------------------------------------------------------ fetching

def _png_bytes():
    buf = io.BytesIO()
    gradient(8, 8).save(buf, "PNG")
    return buf.getvalue()


def test_fetch_image_downloads_upscaled_url_and_caches(monkeypatch):
    albumart._CACHE.clear()
    calls = []

    class Resp:
        content = _png_bytes()
        def raise_for_status(self): pass

    monkeypatch.setattr(albumart.requests, "get", lambda url, timeout=0: calls.append(url) or Resp())
    track = {"thumbnail": "https://lh3.googleusercontent.com/x=w60-h60"}
    img = albumart.fetch_image(track)
    assert img is not None and img.size == (8, 8)
    assert calls == ["https://lh3.googleusercontent.com/x=w400-h400"]
    assert albumart.fetch_image(track) is img and len(calls) == 1      # second call served from memory


def test_fetch_failure_returns_none_instead_of_raising(monkeypatch):
    albumart._CACHE.clear()
    def boom(url, timeout=0):
        raise ConnectionError("offline")
    monkeypatch.setattr(albumart.requests, "get", boom)
    assert albumart.fetch_image({"thumbnail": "http://x/y=w1-h1"}) is None
    assert albumart.fetch_image({}) is None


def test_cache_is_bounded(monkeypatch):
    albumart._CACHE.clear()

    class Resp:
        content = _png_bytes()
        def raise_for_status(self): pass

    monkeypatch.setattr(albumart.requests, "get", lambda url, timeout=0: Resp())
    for i in range(albumart._CACHE_MAX + 10):
        albumart.fetch_image({"thumbnail": f"http://x/{i}=w1-h1"})
    assert len(albumart._CACHE) == albumart._CACHE_MAX


# ------------------------------------------------------------------ UI

def _track(i=1):
    return {"type": "song", "videoId": f"v{i}", "title": f"Song {i}", "artist": "A", "thumbnail": f"http://x/{i}=w1-h1",
            "duration_seconds": 180, "duration": "3:00"}


@pytest.fixture
def art_env(monkeypatch):
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(player, "_trigger_prefetch", lambda: None)
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    player.queue, player.queue_index = [], -1
    player.is_playing = False
    config.set("art_style", "ascii")
    yield
    config.set("art_style", "ascii")
    player.current_track = None


@pytest.mark.asyncio
async def test_art_loads_for_playing_song_and_follows_style_key(art_env, monkeypatch):
    monkeypatch.setattr(albumart, "fetch_image", lambda track: gradient(rgb=(255, 255, 255)))
    monkeypatch.setattr(player, "current_track", _track(1), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        art = app.query_one("#art", ArtView)
        assert await wait_for(pilot, lambda: art.image is not None), "cover should load in the background"
        assert art.display and art.size.height >= app.MIN_ART_ROWS
        assert "@" in art.render().plain

        await pilot.press("i")                               # ascii -> blocks
        assert await wait_for(pilot, lambda: config.get("art_style") == "blocks")
        # check what is actually painted on screen, not just what render() would return
        assert await wait_for(pilot, lambda: "▀" in app.export_screenshot()), "style change must repaint"
        assert "▀" in art.render().plain

        await pilot.press("i")                               # blocks -> off: hidden, no space reserved
        assert await wait_for(pilot, lambda: config.get("art_style") == "off")
        assert await wait_for(pilot, lambda: art.display is False)

        await pilot.press("i")                               # off -> ascii: shown again
        assert await wait_for(pilot, lambda: art.display is True)


@pytest.mark.asyncio
async def test_art_hidden_on_short_terminals(art_env, monkeypatch):
    monkeypatch.setattr(albumart, "fetch_image", lambda track: gradient())
    monkeypatch.setattr(player, "current_track", _track(1), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 30)) as pilot:
        art = app.query_one("#art", ArtView)
        assert await wait_for(pilot, lambda: art.display is False), "no room: don't squeeze the Up Next list"


@pytest.mark.asyncio
async def test_failed_cover_is_not_retried_every_tick(art_env, monkeypatch):
    calls = []
    monkeypatch.setattr(albumart, "fetch_image", lambda track: calls.append(1) or None)
    monkeypatch.setattr(player, "current_track", _track(2), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        assert await wait_for(pilot, lambda: len(calls) >= 1)
        await pilot.pause(1.6)                               # three more 0.5s ticks
        assert len(calls) == 1, f"a failing download must not be hammered ({len(calls)} attempts)"
        assert "♪" in app.query_one("#art", ArtView).render().plain      # placeholder note instead of art


@pytest.mark.asyncio
async def test_slow_cover_for_previous_song_is_ignored(art_env, monkeypatch):
    monkeypatch.setattr(player, "current_track", _track(3), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        assert await wait_for(pilot, lambda: app.art_vid == "v3")
        app._set_art("v-OLD", gradient())                    # a late response for a song that already changed
        assert app.query_one("#art", ArtView).image is None


# ------------------------------------------------------------------ letterbox trimming

def test_trim_borders_removes_black_bars_from_video_thumbnails():
    img = Image.new("RGB", (480, 360), (0, 0, 0))
    img.paste((200, 120, 40), (0, 45, 480, 315))             # 16:9 picture between black bars (hqdefault layout)
    trimmed = albumart.trim_borders(img)
    assert trimmed.size == (480, 270)
    assert trimmed.getpixel((0, 0)) == (200, 120, 40)


def test_trim_borders_leaves_dark_covers_and_tiny_bars_alone():
    dark = Image.new("RGB", (200, 200), (0, 0, 0))
    dark.paste((255, 255, 255), (95, 95, 105, 105))          # a speck of light on a black cover
    assert albumart.trim_borders(dark).size == (200, 200)    # trimming to the speck would destroy the cover
    assert albumart.trim_borders(Image.new("RGB", (100, 100), (0, 0, 0))).size == (100, 100)   # all black
    slim = Image.new("RGB", (200, 200), (200, 50, 50))
    slim.paste((0, 0, 0), (0, 0, 200, 2))                    # 1% bar: not worth cropping
    assert albumart.trim_borders(slim).size == (200, 200)


def test_rendered_art_has_no_black_band_after_trimming():
    img = Image.new("RGB", (480, 360), (0, 0, 0))
    img.paste((220, 220, 220), (0, 45, 480, 315))
    art = albumart.render_art(img, 40, 10, "blocks")
    styles = [str(sp.style) for sp in art.spans]
    assert not any(s.startswith("#000000") or " on #000000" in s for s in styles), "black bars must not reach the screen"
