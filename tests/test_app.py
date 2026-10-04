"""Headless UI tests: real Textual app, simulated mouse/keyboard, network+audio mocked."""
import pytest
from textual import events
from textual.widgets import TabbedContent

from tunebox.core.database import get_favorite_ids
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui.app import TuneboxApp
from tunebox.ui.widgets import TrackTable, SeekBar, VolumeBar


def song(i):
    return {"type": "song", "videoId": f"vid{i}", "title": f"Song {i} [Remastered]", "artist": f"Artist {i}",
            "duration": "3:00", "duration_seconds": 180}


SONGS = [song(i) for i in range(1, 6)]


@pytest.fixture
def calls(monkeypatch):
    rec = {"play": [], "pause": 0, "seek": []}
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [{"title": "Top", "items": SONGS}])
    monkeypatch.setattr(yt_client, "search", lambda q, filter_type=None, limit=25: [song(9)])
    monkeypatch.setattr(player, "play", lambda track=None, queue=None, index=0: rec["play"].append((queue, index)))
    monkeypatch.setattr(player, "toggle_pause", lambda: rec.__setitem__("pause", rec["pause"] + 1))
    monkeypatch.setattr(player, "seek", lambda s, relative=True: rec["seek"].append((s, relative)) or True)
    monkeypatch.setattr(player, "current_track", None, raising=False)
    return rec


async def wait_for(pilot, cond, timeout=5.0):
    """Poll instead of sleeping a fixed time, so slow CI machines don't flake."""
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


async def settle(pilot, app, ticks=6):
    for _ in range(ticks):
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_home_loads_with_header_row(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        table = app.query_one("#t-home", TrackTable)
        assert table.row_count == 6          # shelf header + 5 songs
        assert table.items[0]["type"] == "header"


@pytest.mark.asyncio
async def test_single_click_plays_track_with_queue_context(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        table = app.query_one("#t-home", TrackTable)
        # header row is y=1 (after column header row 0 in the table region); song 1 is the next row
        await pilot.click("#t-home", offset=(10, 2))
        assert await wait_for(pilot, lambda: bool(calls["play"])), "single click should start playback"
        queue, index = calls["play"][-1]
        assert [t["videoId"] for t in queue] == [s["videoId"] for s in SONGS]
        assert index == 0


@pytest.mark.asyncio
async def test_heart_column_click_toggles_favorite(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        table = app.query_one("#t-home", TrackTable)
        heart_x = table.size.width - 4
        before = set(get_favorite_ids())
        await pilot.click("#t-home", offset=(heart_x, 2))
        await settle(pilot, app)
        assert "vid1" in get_favorite_ids() and "vid1" not in before
        assert not calls["play"], "clicking the heart must not start playback"
        await pilot.click("#t-home", offset=(heart_x, 2))
        await settle(pilot, app)
        assert "vid1" not in get_favorite_ids()


@pytest.mark.asyncio
async def test_right_click_toggles_pause(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        await pilot.click("#t-home", offset=(10, 3), button=3)
        assert await wait_for(pilot, lambda: calls["pause"] == 1)
        assert not calls["play"]


@pytest.mark.asyncio
async def test_scroll_over_player_card_changes_volume(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        player.set_volume(50)
        np_widget = app.query_one("#np")
        np_widget.post_message(events.MouseScrollUp(np_widget, 5, 2, 0, 0, 0, False, False, False))
        await settle(pilot, app)
        assert player.volume == 55


@pytest.mark.asyncio
async def test_volume_bar_click_sets_volume(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        bar = app.query_one("#vol", VolumeBar)
        await pilot.click("#vol", offset=(bar.size.width - 7, 0))
        await settle(pilot, app)
        assert player.volume >= 90


@pytest.mark.asyncio
async def test_seek_bar_click_seeks_proportionally(calls, monkeypatch):
    import pygame
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)  # keep the monitor thread from ending the fake track
    monkeypatch.setattr(player, "is_playing", True, raising=False)
    monkeypatch.setattr(player, "track_duration", 200.0, raising=False)
    monkeypatch.setattr(player, "get_position", lambda: 10.0)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        bar = app.query_one("#seek", SeekBar)
        left = len("00:10 ")
        mid = left + (bar.size.width - left - len(" 03:20")) // 2
        await pilot.click("#seek", offset=(mid, 0))
        assert await wait_for(pilot, lambda: bool(calls["seek"])), "seek bar click should seek"
        target, relative = calls["seek"][-1]
        assert relative is False and 80 < target < 120


@pytest.mark.asyncio
async def test_number_keys_switch_tabs_and_search_runs(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press("3")
        assert app.query_one(TabbedContent).active == "queue"
        await pilot.press("2")
        assert await wait_for(pilot, lambda: getattr(app.focused, "id", None) == "search-input"), "search box should be focused"
        await pilot.press(*"hello", "enter")
        assert await wait_for(pilot, lambda: bool(app.search_items))
        assert app.search_items[0]["videoId"] == "vid9"


@pytest.mark.asyncio
async def test_titles_with_brackets_render_literally(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        table = app.query_one("#t-home", TrackTable)
        cell = table.get_cell_at((1, 1))
        assert "[Remastered]" in cell.plain


@pytest.mark.asyncio
async def test_footer_and_theme_cycle(calls):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        first = app.theme
        await pilot.press("t")
        assert app.theme != first


@pytest.mark.asyncio
async def test_clicking_a_synced_lyric_line_seeks_to_that_line(calls, monkeypatch):
    import pygame
    lines = [{"time": float(i * 10), "text": f"line {i}"} for i in range(30)]
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(player, "current_track", song(1), raising=False)
    monkeypatch.setattr(player, "is_playing", True, raising=False)
    monkeypatch.setattr(player, "get_position", lambda: 0.0)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        app.lyrics_vid = "vid1"
        app.lyrics = {"lines": lines, "synced": True, "source": "TEST"}
        app.lyrics_loading = False
        await pilot.press("4")
        view = app.query_one("#lyrics-view")
        # the lyrics paint on the app's 0.5s tick; click only once they are on screen, as a person would
        assert await wait_for(pilot, lambda: app.active_tab == "lyrics" and view.window_start == -3 and view.lines)
        # rows: 0 padding, 1 source label, 2 blank, 3 = "line 0", 5 = "line 2"
        await pilot.click("#lyrics-view", offset=(8, 5))
        assert await wait_for(pilot, lambda: bool(calls["seek"]))
        assert calls["seek"][-1] == (20.0, False)


@pytest.mark.asyncio
async def test_documented_keyboard_shortcuts(calls, monkeypatch):
    """Every key the README documents must actually be bound (key-name typos fail silently)."""
    import pygame
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(player, "current_track", song(1), raising=False)
    monkeypatch.setattr(player, "is_playing", True, raising=False)
    monkeypatch.setattr(player, "track_duration", 180.0, raising=False)
    monkeypatch.setattr(player, "get_position", lambda: 30.0)
    radio = []
    monkeypatch.setattr(yt_client, "get_watch_playlist", lambda vid, limit=25: radio.append(vid) or [song(7)])
    player.shuffle, player.repeat_mode, player.autoplay = False, "off", False
    player.queue, player.queue_index = [song(1)], 0

    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        player.set_volume(50)
        await pilot.press("plus");  assert player.volume == 55
        await pilot.press("minus"); assert player.volume == 50
        await pilot.press("s");     assert player.shuffle is True
        await pilot.press("r");     assert player.repeat_mode == "all"
        await pilot.press("a");     assert player.autoplay is True
        await pilot.press("space"); assert await wait_for(pilot, lambda: calls["pause"] == 1)
        await pilot.press("right_square_bracket")
        assert await wait_for(pilot, lambda: (10, True) in calls["seek"])
        await pilot.press("left_square_bracket")
        assert await wait_for(pilot, lambda: (-10, True) in calls["seek"])
        await pilot.press("f")
        assert "vid1" in get_favorite_ids()
        await pilot.press("f")
        assert "vid1" not in get_favorite_ids()
        await pilot.press("R")
        assert await wait_for(pilot, lambda: radio == ["vid1"]), "R should start a radio queue"
        await pilot.press("slash")
        assert await wait_for(pilot, lambda: getattr(app.focused, "id", None) == "search-input")
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: getattr(app.focused, "id", None) != "search-input")
        tabs = app.query_one(TabbedContent)
        await pilot.press("5")
        # app.active_tab updates only after Textual's tab round-trip finishes (tabs.active is set instantly)
        assert await wait_for(pilot, lambda: app.active_tab == "library")
        await pilot.press("7")
        assert await wait_for(pilot, lambda: app.active_tab == "settings")


@pytest.mark.asyncio
async def test_playlist_flow_create_add_list_and_play(calls, monkeypatch):
    from tunebox.core.database import get_playlists, get_playlist
    monkeypatch.setattr(player, "current_track", song(3), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press("P")
        assert await wait_for(pilot, lambda: getattr(app.focused, "id", None) == "new-name" or app.screen.query("#new-name"))
        app.screen.query_one("#new-name").focus()
        await pilot.press(*"Road trip", "enter")
        assert await wait_for(pilot, lambda: any(p["title"] == "Road trip" for p in get_playlists()))
        pl = next(p for p in get_playlists() if p["title"] == "Road trip")
        assert [t["videoId"] for t in get_playlist(pl["id"])["tracks"]] == ["vid3"]

        await pilot.press("5")
        await pilot.click("#lib-playlists")
        table = app.query_one("#t-library", TrackTable)
        assert await wait_for(pilot, lambda: any(i.get("title") == "Road trip" for i in table.items))
        row = next(i for i, it in enumerate(table.items) if it.get("title") == "Road trip")
        await pilot.click("#t-library", offset=(10, row + 1))
        assert await wait_for(pilot, lambda: bool(calls["play"]))
        queue, index = calls["play"][-1]
        assert [t["videoId"] for t in queue] == ["vid3"] and index == 0


@pytest.mark.asyncio
async def test_every_chip_click_runs_its_action(calls, monkeypatch):
    """Regression: Chip.on_click didn't await run_action, so no chip ever did anything."""
    import warnings
    monkeypatch.setattr(player, "current_track", song(1), raising=False)
    skips = []
    monkeypatch.setattr(player, "next", lambda auto=False: skips.append("next") or True)
    monkeypatch.setattr(player, "previous", lambda: skips.append("prev") or True)
    player.shuffle, player.repeat_mode, player.autoplay = False, "off", False
    app = TuneboxApp()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)       # "coroutine was never awaited"
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.click("#c-shuf");  assert await wait_for(pilot, lambda: player.shuffle is True)
            await pilot.click("#c-rep");   assert await wait_for(pilot, lambda: player.repeat_mode == "all")
            await pilot.click("#c-auto");  assert await wait_for(pilot, lambda: player.autoplay is True)
            await pilot.click("#c-play");  assert await wait_for(pilot, lambda: calls["pause"] == 1)
            await pilot.click("#c-next");  assert await wait_for(pilot, lambda: "next" in skips)
            await pilot.click("#c-prev");  assert await wait_for(pilot, lambda: "prev" in skips)
            await pilot.click("#c-fav");   assert await wait_for(pilot, lambda: "vid1" in get_favorite_ids())
            first = app.theme
            await pilot.press("7")
            assert await wait_for(pilot, lambda: app.active_tab == "settings")
            await pilot.click("#s-theme"); assert await wait_for(pilot, lambda: app.theme != first)
            await pilot.click("#s-shuf");  assert await wait_for(pilot, lambda: player.shuffle is False)
