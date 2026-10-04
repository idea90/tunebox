"""Copy-to-clipboard, personal Home shelves, and inline (graphics protocol) cover art."""
import subprocess
import sys
import time

import pygame
import pytest
from PIL import Image
from textual.widgets import TabbedContent

from tunebox.config import config
from tunebox.core import albumart, recommend, share
from tunebox.core.database import add_history, get_db
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui import inline
from tunebox.ui.app import TuneboxApp
from tunebox.ui.widgets import ArtView, TrackTable


def tr(i, **kw):
    t = {"type": "song", "videoId": f"v{i}", "title": f"Song {i}", "artist": f"Artist {i}", "artists": [{"name": f"Artist {i}", "id": f"UC{i}"}],
         "album": {"name": f"Album {i}", "id": f"MPREb_{i}"}, "duration": "3:00", "duration_seconds": 180, "thumbnail": f"http://x/{i}=w1-h1"}
    t.update(kw)
    return t


async def wait_for(pilot, cond, timeout=5.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


async def ready(pilot, app):
    assert await wait_for(pilot, lambda: app.tab_events >= 1 and app.query("#t-detail-albums")), "initial tab never activated"
    await pilot.pause(0.1)


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(player, "_trigger_prefetch", lambda: None)
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    player.queue, player.queue_index = [], -1
    player.current_track, player.is_playing = None, False
    yield
    player.current_track = None


def reset_history():
    conn = get_db()
    conn.execute("DELETE FROM history")
    conn.execute("DELETE FROM play_counts")
    conn.commit()
    conn.close()


# ================================================================== share: links and lyrics text

def test_share_links_for_every_kind_of_item():
    assert share.share_link(tr(1)) == "https://music.youtube.com/watch?v=v1"
    assert share.share_link({"type": "album", "browseId": "MPREb_abc"}) == "https://music.youtube.com/browse/MPREb_abc"
    assert share.share_link({"type": "playlist", "browseId": "VLPLxyz"}) == "https://music.youtube.com/playlist?list=PLxyz"
    assert share.share_link({"type": "playlist", "browseId": "PLxyz"}) == "https://music.youtube.com/playlist?list=PLxyz"
    assert share.share_link({"type": "artist", "browseId": "UCabc"}) == "https://music.youtube.com/channel/UCabc"
    assert share.share_link({"type": "local_playlist", "id": "local-1"}) is None
    assert share.share_link({"type": "header"}) is None and share.share_link(None) is None


def test_lyrics_text_strips_timestamps_and_adds_header():
    lyrics = {"synced": True, "source": "LRCLIB", "lines": [{"time": 1.0, "text": "First line"}, {"time": 5.5, "text": "  Second  "}, {"time": 9, "text": ""}]}
    text = share.lyrics_to_text(lyrics, "Yellow", "Coldplay")
    assert text == "Yellow - Coldplay\n\nFirst line\nSecond"
    assert "[" not in text and "00:" not in text
    assert share.lyrics_to_text(lyrics) == "First line\nSecond"          # no header when title/artist unknown


@pytest.mark.parametrize("lyrics", [
    {"synced": False, "source": "None", "lines": [{"time": None, "text": "No lyrics found for this track."}]},   # the placeholder
    {"source": "LRCLIB", "lines": []}, {"source": "LRCLIB", "lines": [{"text": "  "}]}, {}, None])
def test_no_real_lyrics_means_nothing_to_copy(lyrics):
    assert share.lyrics_to_text(lyrics, "T", "A") is None


# ================================================================== clipboard backends (subprocess mocked)

@pytest.mark.real_clipboard
@pytest.mark.parametrize("platform,available,expected_cmd", [
    ("darwin", [], "pbcopy"),
    ("linux", ["wl-copy", "xclip", "xsel"], "wl-copy"),
    ("linux", ["xclip", "xsel"], "xclip"),
    ("linux", ["xsel"], "xsel"),
])
def test_clipboard_picks_the_right_tool_per_platform(monkeypatch, platform, available, expected_cmd):
    ran = []
    monkeypatch.setattr(share.sys, "platform", platform)
    monkeypatch.setattr(share.shutil, "which", lambda name: f"/usr/bin/{name}" if name in available else None)
    monkeypatch.setattr(share.subprocess, "run", lambda cmd, **kw: ran.append((cmd[0], kw["input"])) or subprocess.CompletedProcess(cmd, 0))
    assert share.copy_native("héllo ♪") == expected_cmd
    assert ran == [(expected_cmd, "héllo ♪".encode("utf-8"))]       # unicode sent as UTF-8


@pytest.mark.real_clipboard
def test_clipboard_reports_failure_when_no_tool_exists_or_tool_fails(monkeypatch):
    monkeypatch.setattr(share.sys, "platform", "linux")
    monkeypatch.setattr(share.shutil, "which", lambda name: None)
    assert share.copy_native("x") is None                                    # caller then falls back to OSC 52
    monkeypatch.setattr(share.shutil, "which", lambda name: "/usr/bin/xclip" if name == "xclip" else None)
    monkeypatch.setattr(share.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    assert share.copy_native("x") is None


# ================================================================== y / Y in the app

@pytest.mark.asyncio
async def test_y_copies_link_of_highlighted_song_and_Y_copies_lyrics(request, monkeypatch):
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [{"title": "Top", "items": [tr(1), tr(2)]}])
    player.current_track = tr(9, title="Playing Now", artist="Someone")
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        table = app.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 3 and app.focused is table)
        table.move_cursor(row=2)                                            # row 0 is the shelf header
        await pilot.press("y")
        assert await wait_for(pilot, lambda: request.node.copied == ["https://music.youtube.com/watch?v=v2"])

        app.lyrics_vid = "v9"
        app.lyrics = {"synced": True, "source": "LRCLIB", "lines": [{"time": 1, "text": "la la"}, {"time": 2, "text": "li li"}]}
        app.lyrics_loading = False
        await pilot.press("Y")                                              # lyrics from any tab
        assert await wait_for(pilot, lambda: len(request.node.copied) == 2)
        assert request.node.copied[1] == "Playing Now - Someone\n\nla la\nli li"

        await pilot.press("4")                                              # on the Lyrics tab plain y copies lyrics too
        assert await wait_for(pilot, lambda: app.active_tab == "lyrics")
        await pilot.press("y")
        assert await wait_for(pilot, lambda: len(request.node.copied) == 3 and request.node.copied[2] == request.node.copied[1])


@pytest.mark.asyncio
async def test_copy_with_nothing_to_copy_does_not_touch_the_clipboard(request):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        await pilot.press("Y")                                              # nothing playing
        await pilot.press("y")                                              # nothing highlighted or playing
        await pilot.pause(0.4)
        assert request.node.copied == []


@pytest.mark.asyncio
async def test_falls_back_to_terminal_clipboard_when_no_system_clipboard(monkeypatch):
    monkeypatch.setattr(share, "copy_native", lambda text: None)
    player.current_track = tr(4)
    osc = []
    app = TuneboxApp()
    app.copy_to_clipboard = lambda text: osc.append(text)
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        await pilot.press("y")
        assert await wait_for(pilot, lambda: osc == ["https://music.youtube.com/watch?v=v4"])


# ================================================================== personal shelves

@pytest.mark.real_recs
def test_local_shelves_use_recent_distinct_songs_and_real_favourites():
    reset_history()
    for i in (1, 2, 3, 2, 4, 2, 1):                    # v2 x3, v1 x2, v3 x1, v4 x1 ; most recent: v1
        add_history(tr(i))
        time.sleep(0.01)
    shelves = {s["title"]: s["items"] for s in recommend.local_shelves()}
    assert [t["videoId"] for t in shelves["Jump back in"]] == ["v1", "v2", "v4", "v3"]      # newest first, each song once
    top = shelves["Your most played"]
    assert [(t["videoId"], t["playCount"]) for t in top] == [("v2", 3), ("v1", 2)]          # single plays are not favourites
    assert top[0]["duration_seconds"] == 180 and top[0]["thumbnail"].startswith("http")       # metadata joined from history
    assert top[0]["album"]["name"] == "Album 2"
    assert recommend.seed_track()["videoId"] == "v1"


@pytest.mark.real_recs
def test_new_profile_has_no_personal_shelves_and_no_seed():
    reset_history()
    assert recommend.local_shelves() == [] and recommend.seed_track() is None


@pytest.mark.real_recs
def test_because_shelf_excludes_seed_and_songs_already_shown(monkeypatch):
    monkeypatch.setattr(yt_client, "get_watch_playlist", lambda vid, limit=10: [tr(1), tr(5), tr(6), tr(7)])
    shelf = recommend.because_shelf(tr(1, title="A very long song title that keeps going and going on"), exclude_ids={"v5"}, size=2)
    assert [t["videoId"] for t in shelf["items"]] == ["v6", "v7"]
    assert shelf["title"].startswith("Because you played A very long song") and shelf["title"].endswith("…")
    monkeypatch.setattr(yt_client, "get_watch_playlist", lambda vid, limit=10: [])
    assert recommend.because_shelf(tr(1)) is None
    assert recommend.because_shelf({"title": "no id"}) is None


@pytest.mark.real_recs
@pytest.mark.asyncio
async def test_home_shows_your_shelves_first_then_because_then_youtubes(monkeypatch):
    reset_history()
    for i in (1, 2, 2):
        add_history(tr(i))
        time.sleep(0.01)
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [{"title": "Top charts", "items": [tr(20)]}])
    monkeypatch.setattr(yt_client, "get_watch_playlist", lambda vid, limit=10: [tr(30), tr(31)])
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        titles = lambda: [i["title"] for i in app.home_items if i.get("type") == "header"]
        assert await wait_for(pilot, lambda: any(t.startswith("Because you played") for t in titles()) and "Top charts" in titles())
        assert titles() == ["Jump back in", "Your most played", "Because you played Song 2", "Top charts"]
        ids = [i["videoId"] for i in app.home_items if i.get("videoId")]
        assert ids[:2] == ["v2", "v1"] and "v30" in ids and ids[-1] == "v20"
        table = app.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == len(app.home_items))


@pytest.mark.real_recs
@pytest.mark.asyncio
async def test_home_picks_up_new_listening_when_you_come_back(monkeypatch):
    reset_history()
    add_history(tr(1))
    calls = []
    monkeypatch.setattr(yt_client, "get_watch_playlist", lambda vid, limit=10: calls.append(vid) or [tr(40)])
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        assert await wait_for(pilot, lambda: app._because_seed == "v1")
        add_history(tr(2))                                                  # you listened to something else meanwhile
        await pilot.press("3")
        assert await wait_for(pilot, lambda: app.active_tab == "queue")
        await pilot.press("1")
        assert await wait_for(pilot, lambda: app.active_tab == "home")
        assert await wait_for(pilot, lambda: app._because_seed == "v2")
        assert calls == ["v1", "v2"]
        assert [i["title"] for i in app.home_items if i.get("type") == "header"][-1] == "Because you played Song 2"
        await pilot.press("3"); await wait_for(pilot, lambda: app.active_tab == "queue")
        await pilot.press("1"); await wait_for(pilot, lambda: app.active_tab == "home")
        await pilot.pause(0.4)
        assert calls == ["v1", "v2"], "no refetch when nothing new was played"


@pytest.mark.real_recs
@pytest.mark.asyncio
async def test_failed_recommendations_are_retried_next_visit_without_breaking_home(monkeypatch):
    reset_history()
    add_history(tr(1))
    attempts = []
    def flaky(vid, limit=10):
        attempts.append(vid)
        return [] if len(attempts) == 1 else [tr(50)]
    monkeypatch.setattr(yt_client, "get_watch_playlist", flaky)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        assert await wait_for(pilot, lambda: len(attempts) == 1)
        assert [i["title"] for i in app.home_items if i.get("type") == "header"] == ["Jump back in"]   # still fine offline
        await pilot.press("3"); await wait_for(pilot, lambda: app.active_tab == "queue")
        await pilot.press("1"); await wait_for(pilot, lambda: app.active_tab == "home")
        assert await wait_for(pilot, lambda: app._because_seed == "v1")
        assert len(attempts) == 2


# ================================================================== inline (graphics protocol) cover art

def gradient(w=480, h=360):
    img = Image.new("RGB", (w, h), (0, 0, 0))
    img.paste((200, 120, 40), (0, 45, w, h - 45))                           # letterboxed like a YouTube thumbnail
    return img


def test_resolve_style_prefers_images_only_when_the_terminal_supports_them(monkeypatch):
    monkeypatch.setitem(inline._state, "protocol", None)
    assert [inline.resolve_style(s) for s in ("auto", "image", "ascii", "blocks", "off", "bogus")] == \
        ["ascii", "ascii", "ascii", "blocks", "off", "ascii"]
    monkeypatch.setitem(inline._state, "protocol", "kitty")
    assert [inline.resolve_style(s) for s in ("auto", "image", "ascii")] == ["image", "image", "ascii"]
    assert inline.make_image_widget() is not None
    monkeypatch.setitem(inline._state, "protocol", None)
    assert inline.make_image_widget() is None


def test_detect_honours_opt_out_and_caches(monkeypatch):
    monkeypatch.setitem(inline._state, "checked", False)
    monkeypatch.setitem(inline._state, "protocol", None)
    monkeypatch.setenv("TUNEBOX_NO_GRAPHICS", "1")
    assert inline.detect() is None and inline._state["checked"] is True


def test_square_for_display_trims_bars_and_bounds_size():
    out = albumart.square_for_display(gradient(960, 720))
    assert out.size == (400, 400)                                           # square, capped
    assert out.getpixel((200, 2)) != (0, 0, 0), "black letterbox bars are gone"


@pytest.fixture
def cover(monkeypatch):
    monkeypatch.setattr(albumart, "fetch_image", lambda track: gradient())
    monkeypatch.setattr(player, "current_track", tr(1), raising=False)
    config.set("art_style", "auto")
    yield
    config.set("art_style", "auto")


@pytest.mark.asyncio
async def test_without_graphics_support_there_is_no_image_widget_and_auto_means_ascii(cover):
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        await ready(pilot, app)
        assert not app.query("#art-img") and not app.query("#art-wrap")
        art = app.query_one("#art", ArtView)
        assert await wait_for(pilot, lambda: art.image is not None and art.display)
        assert "ascii; no image support" in app._art_label()


@pytest.mark.asyncio
async def test_with_graphics_support_auto_draws_the_real_image_and_ascii_is_the_fallback_style(cover, monkeypatch):
    monkeypatch.setitem(inline._state, "protocol", "kitty")                  # a kitty-capable terminal
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        await ready(pilot, app)
        art, wrap, img = app.query_one("#art", ArtView), app.query_one("#art-wrap"), app.query_one("#art-img")
        assert await wait_for(pilot, lambda: img.image is not None), "cover reaches the image widget"
        assert img.image.size == (400, 400) or img.image.size[0] == img.image.size[1]    # trimmed, square
        assert wrap.display and not art.display
        assert wrap.styles.height.value >= app.MIN_ART_ROWS
        assert "kitty image" in app._art_label()
        assert app.export_screenshot(), "painting the image widget must not crash"

        await pilot.press("i")                                              # auto -> ascii: text art takes over
        assert await wait_for(pilot, lambda: config.get("art_style") == "ascii")
        assert await wait_for(pilot, lambda: art.display and not wrap.display)

        await pilot.press("i", "i")                                         # blocks, then off: nothing drawn, no space reserved
        assert await wait_for(pilot, lambda: config.get("art_style") == "off")
        assert await wait_for(pilot, lambda: not art.display and not wrap.display)

        await pilot.press("i")                                              # back to auto: image returns without a refetch
        assert await wait_for(pilot, lambda: config.get("art_style") == "auto" and wrap.display)
        assert await wait_for(pilot, lambda: img.image is not None)


@pytest.mark.asyncio
async def test_inline_image_clears_between_songs_and_ignores_stale_covers(cover, monkeypatch):
    monkeypatch.setitem(inline._state, "protocol", "sixel")
    app = TuneboxApp()
    async with app.run_test(size=(140, 46)) as pilot:
        await ready(pilot, app)
        img = app.query_one("#art-img")
        assert await wait_for(pilot, lambda: img.image is not None)
        monkeypatch.setattr(player, "current_track", tr(2), raising=False)   # next song starts
        assert await wait_for(pilot, lambda: app.art_vid == "v2")
        app._set_art("v1", gradient())                                       # late response for the old song
        assert img.image is None or app.art_vid == "v2"
        assert "sixel image" in app._art_label()


@pytest.mark.asyncio
async def test_inline_image_hidden_on_short_terminals(cover, monkeypatch):
    monkeypatch.setitem(inline._state, "protocol", "kitty")
    app = TuneboxApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await ready(pilot, app)
        assert await wait_for(pilot, lambda: not app.query_one("#art-wrap").display)


def test_old_pillow_disables_graphics_instead_of_crashing_on_paint(monkeypatch):
    """textual-image declares pillow>=10.3 but needs get_flattened_data (Pillow 12.1+)."""
    from PIL import Image as PILImage
    monkeypatch.setitem(inline._state, "checked", False)
    monkeypatch.setitem(inline._state, "protocol", None)
    monkeypatch.delenv("TUNEBOX_NO_GRAPHICS", raising=False)
    monkeypatch.delattr(PILImage.Image, "get_flattened_data")
    assert inline.detect() is None
    assert inline.resolve_style("auto") == "ascii"


def test_graphics_renderables_emit_real_protocol_escape_codes_for_our_cover():
    """What a kitty / Sixel terminal would receive for one of our covers (the terminal side can't be tested here)."""
    from io import StringIO
    from rich.console import Console
    from textual_image.renderable import SixelImage, TGPImage
    cover = albumart.square_for_display(gradient())
    # kitty: unicode-placeholder cells (U+10EEEE; the image itself is uploaded out of band). Sixel: a DCS sequence.
    for renderable, marker in ((TGPImage, "\U0010eeee"), (SixelImage, "\x1bP")):
        buf = StringIO()
        Console(file=buf, force_terminal=True, width=60, height=20, color_system="truecolor").print(renderable(cover, 24, 12))
        assert marker in buf.getvalue(), f"{renderable.__name__} produced no {marker!r} sequence"


def test_radio_results_keep_their_duration_from_the_length_field():
    """Regression: watch-playlist items use 'length', so recommended songs showed 00:00."""
    t = yt_client.format_track({"videoId": "a", "title": "T", "artists": [], "length": "3:45"})
    assert t["duration"] == "3:45" and t["duration_seconds"] == 225
    assert yt_client.format_track({"videoId": "a", "title": "T", "duration": "1:00", "length": "9:99"})["duration"] == "1:00"
    assert yt_client.format_track({"videoId": "a", "title": "T"})["duration_seconds"] == 0


@pytest.mark.asyncio
async def test_unknown_duration_shows_dashes_not_a_fake_zero(monkeypatch):
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [{"title": "Top", "items": [
        tr(1, duration="", duration_seconds=0), tr(2)]}])
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        table = app.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 3)
        time_col = [str(c.label) for c in table.ordered_columns].index("Time")      # shifts when the Album column shows
        assert table.get_cell_at((1, time_col)).plain == "--:--" and table.get_cell_at((2, time_col)).plain == "3:00"
