"""Visualizer: spectrum analysis accuracy, drawing styles/colours, animation, and app wiring."""
import os
import subprocess
import time

import numpy as np
import pygame
import pytest

from tunebox.config import config
from tunebox.core import spectrum as sp
from tunebox.core.downloader import get_ffmpeg_path
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui.app import TuneboxApp
from tunebox.ui.visualizer import Visualizer, bar_layout, render_viz, resample_bands, viz_color

FFMPEG = get_ffmpeg_path()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")


def tone(tmp_path, hz, seconds=2.0, name=None):
    path = tmp_path / (name or f"tone{hz}.mp3")
    src = f"sine=frequency={hz}:duration={seconds}" if hz else f"anullsrc=r=22050:cl=mono:d={seconds}"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", src, "-t", str(seconds), str(path)], check=True)
    return str(path)


# ================================================================== analysis

@needs_ffmpeg
@pytest.mark.parametrize("hz", [100, 440, 1000, 5000])
def test_loudest_band_matches_the_tone(tmp_path, hz):
    spec = sp.analyze(tone(tmp_path, hz))
    centre = sp.band_centres_hz()[int(spec[sp.FPS].argmax())]
    assert abs(np.log2(centre / hz)) < 0.2, f"{hz} Hz tone peaked at {centre:.0f} Hz"


@needs_ffmpeg
def test_spectrum_shape_range_and_timing(tmp_path):
    spec = sp.analyze(tone(tmp_path, 440, seconds=3))
    assert spec.shape[1] == sp.BANDS and abs(spec.shape[0] - 3 * sp.FPS) <= 2
    assert spec.dtype == np.float32 and spec.min() >= 0 and spec.max() <= 1


@needs_ffmpeg
def test_silence_draws_nothing(tmp_path):
    spec = sp.analyze(tone(tmp_path, 0, name="silence.mp3"))
    assert spec is not None and float(spec.max()) == 0.0


@needs_ffmpeg
def test_change_of_note_is_followed_in_time(tmp_path):
    a, b = tone(tmp_path, 1000), tone(tmp_path, 200)
    both = str(tmp_path / "both.mp3")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", a, "-i", b, "-filter_complex", "[0][1]concat=n=2:v=0:a=1", both], check=True)
    spec = sp.analyze(both)
    c = sp.band_centres_hz()
    assert 800 < c[int(sp.frame_at(spec, 1.0).argmax())] < 1250
    assert 160 < c[int(sp.frame_at(spec, 3.0).argmax())] < 250


def test_unreadable_or_missing_audio_gives_none(tmp_path):
    bad = tmp_path / "not_audio.mp3"
    bad.write_bytes(b"definitely not an mp3")
    assert sp.analyze(str(bad)) is None
    assert sp.analyze(str(tmp_path / "missing.mp3")) is None


def test_frame_at_bounds():
    spec = np.ones((30, sp.BANDS), np.float32)
    assert sp.frame_at(spec, 0.5) is not None
    assert sp.frame_at(spec, 5.0) is None and sp.frame_at(spec, -1) is None and sp.frame_at(None, 1) is None


@needs_ffmpeg
def test_analysis_is_cached(tmp_path, monkeypatch):
    path = tone(tmp_path, 440)
    sp.analyze(path)
    monkeypatch.setattr(sp, "_decode", lambda p: pytest.fail("should come from the cache"))
    assert sp.analyze(path) is not None


# ================================================================== layout and drawing

def test_resample_keeps_the_loudest_band_of_each_bar():
    assert np.allclose(resample_bands([0, 1, 0, 0], 2), [1.0, 0.0])          # a clear note stays at full height
    assert np.allclose(resample_bands(np.linspace(0, 1, 64), 4), [15 / 63, 31 / 63, 47 / 63, 1.0])
    assert len(resample_bands(np.ones(64), 100)) == 100       # more bars than bands still works
    assert len(resample_bands(np.ones(64), 0)) == 0


@pytest.mark.parametrize("width,style,expected", [(40, "bars", (20, 2)), (20, "bars", (20, 1)), (40, "line", (40, 1)), (0, "bars", (0, 1))])
def test_bar_layout(width, style, expected):
    assert bar_layout(width, style) == expected


def lines(text):
    return text.plain.split("\n")


def test_bars_full_empty_and_partial():
    art = render_viz([1.0, 0.0, 0.5], [1.0, 0.0, 0.5], width=5, height=4, style="bars", scheme="theme")
    rows = lines(art)
    assert len(rows) == 4 and all(len(r) == 5 for r in rows), "every row fills the widget width"
    col = lambda i: "".join(r[1 + i] for r in rows)                 # 3 bars centred in 5 columns: 1 cell of padding
    assert col(0) == "█" * 4                              # full bar
    assert col(1) == "   ▁"                               # silent: just the resting baseline
    assert col(2) == "  ██"                          # half height


def test_peak_cap_hangs_above_a_falling_bar():
    rows = lines(render_viz([0.25], [0.9], width=1, height=4, style="bars", scheme="theme"))
    assert rows[0] == "▔" and rows[3] == "█"


def test_mirror_reflects_below_the_middle():
    rows = lines(render_viz([1.0, 0.5], [0, 0], width=3, height=4, style="mirror", scheme="mono"))
    assert [r[0] for r in rows] == ["█"] * 4              # full level: solid top and reflection
    assert rows[1][1] == "█" and rows[2][1] in "█▀"   # half level: top cell + start of reflection


def test_line_draws_one_cell_per_column():
    rows = lines(render_viz([1.0, 0.1, 0.0], [0, 0, 0], width=3, height=4, style="line", scheme="theme"))
    for i in range(3):
        assert sum(r[i] == "━" for r in rows) == 1


def test_off_or_empty_draws_nothing():
    assert render_viz([1.0], [1.0], 10, 4, "off", "theme").plain == ""
    assert render_viz([], [], 10, 4, "bars", "theme").plain == ""


def test_colour_schemes():
    assert viz_color("rainbow", 0.0, 0.5) != viz_color("rainbow", 1.0, 0.5)
    assert viz_color("fire", 0.5, 0.0) == "#ff2800"                                # red at the bottom
    assert viz_color("fire", 0.5, 1.0) == "#ffeb3c"                                # yellow at the top
    assert viz_color("theme", 0.3, 0.0, primary="#112233") == "#112233"
    assert viz_color("theme", 0.3, 1.0, primary="#112233", accent="#aabbcc") == "#aabbcc"
    r, g, b = (int(viz_color("mono", 0, 0.5)[i:i + 2], 16) for i in (1, 3, 5))
    assert abs(r - g) < 15 and abs(g - b) < 15                                     # grey


# ================================================================== app wiring

@pytest.fixture
def playing_tone(tmp_path, monkeypatch):
    """The player looks like it's playing a real 1 kHz tone file, without real audio output."""
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    path = tone(tmp_path, 1000, seconds=6)
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    monkeypatch.setattr(player, "get_position", lambda: 2.0)
    player.current_track = {"videoId": "tone", "title": "Tone", "artist": "Test", "duration_seconds": 6}
    player._current_file = path
    player.is_playing, player.is_paused, player.is_loading = True, False, False
    config.set("viz_style", "bars")
    config.set("viz_colors", "theme")
    yield path
    player.is_playing = False
    player.current_track = None
    player._current_file = None
    config.set("viz_style", "bars")
    config.set("viz_colors", "theme")


async def wait_for(pilot, cond, timeout=6.0):
    t = 0.0
    while not cond() and t < timeout:
        await pilot.pause(0.05)
        t += 0.05
    return cond()


@pytest.mark.asyncio
async def test_visualizer_moves_with_the_playing_song(playing_tone):
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        viz = app.query_one("#viz", Visualizer)
        assert await wait_for(pilot, lambda: viz.spectrum is not None), "the song should be analysed in the background"
        assert await wait_for(pilot, lambda: viz.levels.size and viz.levels.max() > 0.5)
        assert viz.display and viz.size.height == app.VIZ_MAX_ROWS
        assert "█" in app.export_screenshot() or "▇" in app.export_screenshot()
        loud = int(np.argmax(viz.levels))
        assert 0.3 < loud / len(viz.levels) < 0.9, "a 1 kHz tone lights the middle of the display, not the edges"


@pytest.mark.asyncio
async def test_pausing_lets_the_bars_fall_to_rest(playing_tone):
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        viz = app.query_one("#viz", Visualizer)
        assert await wait_for(pilot, lambda: viz.levels.size and viz.levels.max() > 0.5)
        player.is_paused = True
        assert await wait_for(pilot, lambda: viz.levels.max() == 0.0), "bars should fall smoothly to zero"


@pytest.mark.asyncio
async def test_v_cycles_styles_and_V_cycles_colours(playing_tone):
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        viz = app.query_one("#viz", Visualizer)
        assert await wait_for(pilot, lambda: viz.spectrum is not None)
        for expected in ("mirror", "line", "off", "bars"):
            await pilot.press("v")
            assert await wait_for(pilot, lambda: config.get("viz_style") == expected)
            assert await wait_for(pilot, lambda: viz.display == (expected != "off"))
        for expected in ("rainbow", "fire", "mono", "theme"):
            await pilot.press("V")
            assert await wait_for(pilot, lambda: config.get("viz_colors") == expected)
        await pilot.press("7")
        assert await wait_for(pilot, lambda: app.active_tab == "settings")
        assert await wait_for(pilot, lambda: "Visualizer: bars" in str(app.query_one("#s-viz").render()))
        assert "Visualizer colours: theme" in str(app.query_one("#s-vizc").render())


@pytest.mark.asyncio
async def test_failed_analysis_is_not_retried_every_tick(playing_tone, monkeypatch):
    calls = []
    monkeypatch.setattr(sp, "analyze", lambda p: calls.append(p) or None)
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        assert await wait_for(pilot, lambda: len(calls) == 1)
        await pilot.pause(1.6)
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_slow_analysis_for_a_previous_song_is_ignored(playing_tone):
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        assert await wait_for(pilot, lambda: app.viz_path == playing_tone)
        app._set_viz("some-old-song.mp3", np.ones((10, sp.BANDS), np.float32))
        assert app.query_one("#viz", Visualizer).spectrum is None or app.viz_path == playing_tone


@pytest.mark.parametrize("height,expect_viz,expect_art", [(46, 4, 10), (42, 4, 6), (40, 2, 6), (36, 4, 0), (30, 0, -2)])
def test_sidebar_budget_shares_space_between_cover_and_visualizer(height, expect_viz, expect_art, monkeypatch):
    app = TuneboxApp()
    monkeypatch.setattr(type(app), "size", property(lambda self: type("S", (), {"height": height, "width": 140})()))
    config.set("viz_style", "bars")
    art, viz = app._sidebar_budget()
    assert viz == expect_viz and art == expect_art
    config.set("viz_style", "off")
    assert app._sidebar_budget()[1] == 0
    config.set("viz_style", "bars")


@pytest.mark.asyncio
async def test_up_next_keeps_its_rows_with_cover_and_visualizer_at_40_lines(playing_tone):
    from tunebox.ui.widgets import TrackTable
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        table = app.query_one("#t-upnext", TrackTable)
        viz = app.query_one("#viz", Visualizer)
        assert await wait_for(pilot, lambda: viz.size.height == 2 and table.size.height >= 5), (
            f"viz={viz.size.height} up_next={table.size.height}")
