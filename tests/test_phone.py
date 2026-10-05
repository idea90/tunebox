"""The phone-sized (Termux) layout: one column, a mini player, the full player as its own screen."""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from textual.widgets import Static, TabbedContent

from tunebox import termux
from tunebox.core.database import get_favorite_ids
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui.app import TuneboxApp
from tunebox.ui.constants import COMPACT_TAB_NAMES, COMPACT_WIDTH
from tunebox.ui.screens import HelpScreen
from tunebox.ui.widgets import NARROW_WIDTH, TrackTable

PHONE = (50, 38)


def song(i, **extra):
    t = {"videoId": f"ph{i}", "title": f"Phone song {i}", "artist": f"Artist {i}", "artists": [{"name": f"Artist {i}", "id": None}],
         "album": {"name": f"Album {i}", "id": None}, "duration": "3:00", "duration_seconds": 180, "type": "song"}
    t.update(extra)
    return t


async def wait_for(pilot, cond, timeout=5.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


def text_of(widget) -> str:
    content = getattr(widget, "content", "")
    return content.plain if hasattr(content, "plain") else str(content)


@pytest.fixture(autouse=True)
def calm(monkeypatch):
    import pygame
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(player, "_trigger_prefetch", lambda: None)
    monkeypatch.setattr(player, "queue", [], raising=False)
    monkeypatch.setattr(player, "current_track", None, raising=False)
    monkeypatch.setattr(player, "is_playing", False, raising=False)
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [{"title": "Top", "items": [song(1), song(2), song(3)]}])


@pytest.mark.asyncio
async def test_a_narrow_terminal_gets_the_one_column_layout():
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        assert await wait_for(pilot, lambda: app.compact)
        assert app.has_class("compact")
        assert await wait_for(pilot, lambda: not app.query_one("#sidebar").display)
        assert app.query_one("#main").display and app.query_one("#mini").display
        assert not app.query_one("#footer", expect_type=None) if app.query("#footer") else True
        tabs = app.query_one(TabbedContent)
        assert await wait_for(pilot, lambda: str(tabs.get_tab("search").label) == COMPACT_TAB_NAMES["search"])
        assert all(not str(tabs.get_tab(t).label)[0].isdigit() for t in ("home", "queue", "settings"))


@pytest.mark.asyncio
async def test_a_wide_terminal_keeps_the_two_pane_layout():
    app = TuneboxApp()
    async with app.run_test(size=(COMPACT_WIDTH + 20, 40)) as pilot:
        await pilot.pause(0.4)
        assert not app.compact and not app.has_class("compact")
        assert app.query_one("#sidebar").display and app.query_one("#main").display and not app.query_one("#mini").display
        await pilot.press("o")                                              # nothing to toggle on a big screen
        await pilot.pause(0.2)
        assert app.player_view is False and app.query_one("#main").display


@pytest.mark.asyncio
async def test_resizing_switches_layouts_live():
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.4)
        assert not app.compact and app.query_one("#sidebar").display
        await pilot.resize_terminal(*PHONE)
        assert await wait_for(pilot, lambda: app.compact and not app.query_one("#sidebar").display and app.query_one("#mini").display)
        await pilot.resize_terminal(140, 40)
        assert await wait_for(pilot, lambda: not app.compact and app.query_one("#sidebar").display and not app.query_one("#mini").display)


@pytest.mark.asyncio
async def test_the_mini_player_shows_the_song_and_its_buttons_work(monkeypatch):
    calls = []
    monkeypatch.setattr(player, "toggle_pause", lambda: calls.append("pause"))
    monkeypatch.setattr(player, "next", lambda auto=False: calls.append("next") or True)
    monkeypatch.setattr(player, "previous", lambda: calls.append("prev") or True)
    monkeypatch.setattr(player, "current_track", song(5), raising=False)
    monkeypatch.setattr(player, "is_playing", True, raising=False)
    monkeypatch.setattr(player, "is_paused", False, raising=False)
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        assert await wait_for(pilot, lambda: "Phone song 5" in text_of(app.query_one("#mini-now", Static)))
        assert "Artist 5" in text_of(app.query_one("#mini-now", Static))
        await pilot.click("#m-play")
        await pilot.click("#m-next")
        await pilot.click("#m-prev")
        assert await wait_for(pilot, lambda: sorted(calls) == ["next", "pause", "prev"])
        await pilot.click("#m-fav")
        assert await wait_for(pilot, lambda: "ph5" in get_favorite_ids())
        assert await wait_for(pilot, lambda: "♥" in text_of(app.query_one("#m-fav")))


@pytest.mark.asyncio
async def test_the_full_player_opens_with_o_or_a_tap_and_closes_with_escape_or_back(monkeypatch):
    monkeypatch.setattr(player, "current_track", song(6), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        assert await wait_for(pilot, lambda: app.compact)
        await pilot.press("o")
        assert await wait_for(pilot, lambda: app.player_view and app.query_one("#sidebar").display and not app.query_one("#main").display)
        assert not app.query_one("#mini").display
        assert "Phone song 6" in text_of(app.query_one("#np-title", Static))
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: not app.player_view and app.query_one("#main").display and app.query_one("#mini").display)

        await pilot.click("#mini-now")                                      # tap the song strip
        assert await wait_for(pilot, lambda: app.player_view)
        await pilot.click("#close-player")                                   # the big Back button
        assert await wait_for(pilot, lambda: not app.player_view and app.query_one("#main").display)

        await pilot.click("#m-open")                                         # the arrow on the mini player
        assert await wait_for(pilot, lambda: app.player_view)


@pytest.mark.asyncio
async def test_going_back_to_a_wide_screen_leaves_the_full_player_view(monkeypatch):
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        await pilot.press("o")
        assert await wait_for(pilot, lambda: app.player_view)
        await pilot.resize_terminal(140, 40)
        assert await wait_for(pilot, lambda: not app.player_view and app.query_one("#main").display and app.query_one("#sidebar").display)


@pytest.mark.asyncio
async def test_the_full_player_gets_its_cover_art_on_a_phone(monkeypatch):
    from PIL import Image
    from tunebox.core import albumart
    monkeypatch.setattr(albumart, "fetch_image", lambda t: Image.new("RGB", (64, 64), (200, 50, 200)))
    monkeypatch.setattr(player, "current_track", song(7), raising=False)
    monkeypatch.setattr(player, "is_playing", True, raising=False)
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        await pilot.press("o")
        art = app.query_one("#art")
        assert await wait_for(pilot, lambda: app.player_view and art.display and art.size.height >= app.MIN_ART_ROWS and art.image is not None)
        assert not app.query_one("#t-upnext").display and not app.query_one("#mini-lyrics").display   # the tabs cover those


@pytest.mark.asyncio
async def test_song_lists_use_one_combined_column_on_a_phone():
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        table = app.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 4 and 0 < table.size.width < NARROW_WIDTH)
        assert [str(c.label) for c in table.ordered_columns] == ["#", "Song", "♥"] and table.heart_col == 2
        cell = table.get_cell_at((1, 1)).plain
        assert cell.startswith("Phone song 1") and "Artist 1" in cell        # title and artist share the cell
        # tapping the heart favourites without playing; tapping the song plays it
        played = []
        monkeypatch_play = pytest.MonkeyPatch()
        monkeypatch_play.setattr(player, "play", lambda track=None, queue=None, index=0: played.append(index))
        try:
            await pilot.click("#t-home", offset=(table.size.width - 3, 2))
            assert await wait_for(pilot, lambda: "ph1" in get_favorite_ids())
            assert played == []
            await pilot.click("#t-home", offset=(12, 3))
            assert await wait_for(pilot, lambda: played == [1])
        finally:
            monkeypatch_play.undo()


@pytest.mark.asyncio
async def test_dialogs_fit_a_phone_screen():
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        await pilot.pause(0.3)
        await pilot.press("question_mark")
        assert await wait_for(pilot, lambda: isinstance(app.screen, HelpScreen) and bool(app.screen.query("#help")))
        assert app.screen.query_one("#help").region.width <= PHONE[0]
        await pilot.press("escape")
        from tunebox.ui.screens import ConfirmPrompt
        app.push_screen(ConfirmPrompt("Download 12 songs from \"A fairly long playlist name here\" as MP3?\nSaved in: /storage/emulated/0/Music/Tunebox"))
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt) and bool(app.screen.query("#confirm")))
        assert app.screen.query_one("#confirm").region.width <= PHONE[0]


# ------------------------------------------------------------------ Termux integration

@pytest.mark.asyncio
async def test_the_phone_is_kept_awake_while_music_plays(monkeypatch):
    seen = []
    monkeypatch.setattr(termux, "wake_lock", lambda on: seen.append(bool(on)) or on)
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        assert await wait_for(pilot, lambda: seen and seen[-1] is False)    # idle: no lock
        monkeypatch.setattr(player, "is_playing", True, raising=False)
        monkeypatch.setattr(player, "is_paused", False, raising=False)
        assert await wait_for(pilot, lambda: seen[-1] is True)
        monkeypatch.setattr(player, "is_paused", True, raising=False)
        assert await wait_for(pilot, lambda: seen[-1] is False)             # paused: let the phone sleep
        app.batch = {"label": "x", "done": 1, "total": 3}                     # a download in progress also holds it
        assert await wait_for(pilot, lambda: seen[-1] is True)
        app.batch = None
    assert seen[-1] is False                                                  # and it is released on exit


# ------------------------------------------------------------------ Termux has no pygame: the whole app must still work

@pytest.mark.skipif(not shutil.which("mpv"), reason="mpv is not installed")
def test_the_whole_app_starts_and_plays_with_pygame_missing(tmp_path):
    """The real Termux condition: no pygame at all, audio through mpv. Runs the app in a clean interpreter where
    importing pygame fails."""
    import wave
    import numpy as np
    wav = tmp_path / "t.wav"
    sr = 22050
    t = np.arange(sr * 3) / sr
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.sin(2 * np.pi * 440 * t) * 20000).astype("<i2").tobytes())
    script = textwrap.dedent(f'''
        import asyncio, sys, time
        sys.modules["pygame"] = None                       # "import pygame" now raises ImportError, as on Termux
        from tunebox.core.player import player
        from tunebox.core.ytmusic import yt_client
        yt_client.get_home_feed = lambda: []
        assert player.audio.name == "mpv" and player.audio_ok, (player.audio.name, player.audio_problem)
        from tunebox.ui.app import TuneboxApp

        async def main():
            app = TuneboxApp()
            async with app.run_test(size=(50, 38)) as pilot:
                await pilot.pause(0.5)
                assert app.compact
                track = {{"videoId": "nopyg", "title": "No pygame", "artist": "T", "filePath": {str(wav)!r}, "duration_seconds": 3}}
                import threading
                threading.Thread(target=player.play, kwargs=dict(queue=[track], index=0), daemon=True).start()
                for _ in range(100):
                    await pilot.pause(0.05)
                    if player.is_playing and player.get_position() > 0.3:
                        break
                assert player.is_playing and player.get_position() > 0.3, "never started playing"
                assert "No pygame" in str(app.query_one("#mini-now").content)
            player.audio.close()
            print("OK")
        asyncio.run(main())
    ''')
    env = dict(os.environ, TUNEBOX_HOME=str(tmp_path / "home"), TUNEBOX_AUDIO="mpv", SDL_AUDIODRIVER="dummy",
               PYTHONPATH=str(Path(__file__).resolve().parent.parent))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "config.json").write_text('{"mpv_args": ["--ao=null"]}')
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=90)
    assert out.returncode == 0 and "OK" in out.stdout, out.stdout[-800:] + out.stderr[-1500:]


# ------------------------------------------------------------------ the installer

def test_the_termux_installer_refuses_to_run_anywhere_else_and_is_valid_bash():
    root = Path(__file__).resolve().parent.parent
    assert subprocess.run(["bash", "-n", str(root / "termux-install.sh")]).returncode == 0
    env = {k: v for k, v in os.environ.items() if k != "TERMUX_VERSION"}
    env["PREFIX"] = "/usr"
    out = subprocess.run(["bash", str(root / "termux-install.sh")], env=env, capture_output=True, text=True, timeout=20)
    assert out.returncode == 1 and "for Termux" in out.stdout                 # and never got as far as `pkg`


def test_the_termux_requirements_leave_out_what_pkg_provides():
    text = (Path(__file__).resolve().parent.parent / "requirements-termux.txt").read_text()
    names = {l.split(">=")[0].strip().lower() for l in text.splitlines() if l and not l.startswith("#")}
    assert {"textual", "ytmusicapi", "yt-dlp", "rich", "requests", "mutagen"} <= names
    assert not names & {"pygame", "numpy", "pillow", "imageio-ffmpeg", "dbus-next", "pynput", "textual-image"}


@pytest.mark.asyncio
async def test_artist_and_album_rows_keep_their_full_type_tag_on_a_phone(monkeypatch):
    monkeypatch.setattr(yt_client, "search", lambda q, filter_type=None, limit=25: [
        {"type": "artist", "title": "Nova Lines", "browseId": "UCx"}, {"type": "album", "title": "Night LP", "browseId": "MPREx", "artist": "Nova Lines"},
        {"type": "playlist", "title": "Mix", "browseId": "PLx"}, song(1)])
    app = TuneboxApp()
    async with app.run_test(size=PHONE) as pilot:
        await pilot.pause(0.3)
        app.search_items = yt_client.search("x")
        app.refresh_tables()
        await pilot.press("2")
        table = app.query_one("#t-search", TrackTable)
        assert await wait_for(pilot, lambda: app.active_tab == "search" and table.row_count == 4)
        cells = [table.get_cell_at((i, 1)).plain for i in range(3)]
        assert cells[0].startswith("ARTIST Nova Lines") and cells[1].startswith("ALBUM Night LP") and cells[2].startswith("LIST Mix")
