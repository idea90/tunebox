"""The redesigned interface: empty states, quiet selection, adaptive tabs and columns, lyrics layout."""
import pytest
from rich.text import Text
from textual.widgets import Static, TabbedContent

from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui.app import TuneboxApp
from tunebox.ui.panels import TopBar
from tunebox.ui.widgets import TrackTable, center_text


def song(i, **extra):
    t = {"videoId": f"look{i}", "title": f"Look {i}", "artist": f"Artist {i}", "artists": [{"name": f"Artist {i}", "id": None}],
         "album": {"name": f"Album {i}", "id": None}, "duration": "3:00", "duration_seconds": 180, "type": "song"}
    t.update(extra)
    return t


async def wait_for(pilot, cond, timeout=5.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


@pytest.fixture(autouse=True)
def calm(monkeypatch):
    import pygame
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(player, "_trigger_prefetch", lambda: None)
    monkeypatch.setattr(player, "queue", [], raising=False)
    monkeypatch.setattr(player, "current_track", None, raising=False)
    monkeypatch.setattr(player, "is_playing", False, raising=False)
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [{"title": "Top", "items": [song(1), song(2), song(3)]}])


def text_of(widget) -> str:
    content = getattr(widget, "content", None) or widget.renderable
    return content.plain if hasattr(content, "plain") else str(content)


# ------------------------------------------------------------------ pure helpers

def test_center_text_pads_every_line_to_the_middle():
    out = center_text(Text("ab\nabcd"), 10)
    assert out.plain == "    ab\n   abcd"
    assert center_text(Text("toolongforthis"), 4).plain == "toolongforthis"       # never negative padding


# ------------------------------------------------------------------ empty states

@pytest.mark.asyncio
async def test_an_empty_queue_explains_itself_and_a_filled_one_shows_the_list():
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("3")
        assert await wait_for(pilot, lambda: app.active_tab == "queue")
        table, hint = app.query_one("#t-queue", TrackTable), app.query_one("#e-queue", Static)
        assert await wait_for(pilot, lambda: hint.display and not table.display)
        assert "queue is empty" in text_of(hint)

        player.queue = [song(7), song(8)]
        app.refresh_tables()
        assert await wait_for(pilot, lambda: table.display and not hint.display and table.row_count == 2)


@pytest.mark.asyncio
async def test_search_tab_invites_you_to_search_then_reports_no_results():
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("2")
        hint = app.query_one("#e-search", Static)
        assert await wait_for(pilot, lambda: hint.display and "Search YouTube Music" in text_of(hint))
        app.search_query = "zzzz"
        app.refresh_tables()
        assert await wait_for(pilot, lambda: 'No results for "zzzz"' in text_of(hint))


@pytest.mark.asyncio
async def test_library_and_downloads_say_what_to_do(monkeypatch):
    monkeypatch.setattr("tunebox.ui.mixins.refresh.get_downloads", lambda: [])
    monkeypatch.setattr("tunebox.ui.mixins.data.get_favorites", lambda: [])
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("6")
        assert await wait_for(pilot, lambda: "No downloads yet" in text_of(app.query_one("#e-downloads", Static)))
        await pilot.press("5")
        assert await wait_for(pilot, lambda: "No favorites yet" in text_of(app.query_one("#e-library", Static)))
        app.library_sub = "yt_liked"
        app.refresh_tables()
        assert await wait_for(pilot, lambda: "tunebox login" in text_of(app.query_one("#e-library", Static)))


# ------------------------------------------------------------------ quiet selection

@pytest.mark.asyncio
async def test_home_shows_no_selection_bar_until_you_move_and_p_still_means_the_playing_song(monkeypatch):
    monkeypatch.setattr(player, "current_track", song(9), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        table = app.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 4)          # a section title + 3 songs
        assert await wait_for(pilot, lambda: app.focused is table)
        assert table.show_cursor is False                                    # the cursor is parked on the title
        assert app._target_track()["videoId"] == "look9"                     # nothing highlighted: the playing song

        await pilot.press("down")
        assert await wait_for(pilot, lambda: table.show_cursor is True)
        assert app._target_track()["videoId"] == "look1"                     # now a real selection


@pytest.mark.asyncio
async def test_a_list_that_starts_with_a_song_shows_its_cursor_at_once(monkeypatch):
    player.queue = [song(1), song(2)]
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("3")
        table = app.query_one("#t-queue", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 2)
        assert table.show_cursor is True
        assert app.query_one("#t-upnext", TrackTable).show_cursor is False   # Up Next is a read-out, never a selection


# ------------------------------------------------------------------ columns and tabs

@pytest.mark.asyncio
async def test_album_column_appears_only_when_there_is_room(monkeypatch):
    app = TuneboxApp()
    async with app.run_test(size=(150, 40)) as pilot:
        table = app.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 4)
        names = [str(c.label) for c in table.ordered_columns]
        assert names == ["#", "Title", "Artist", "Album", "Time", "♥"] and table.heart_col == 5
        assert table.get_cell_at((1, 3)).plain == "Album 1"

    narrow = TuneboxApp()
    async with narrow.run_test(size=(100, 36)) as pilot:
        table = narrow.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 4)
        assert [str(c.label) for c in table.ordered_columns] == ["#", "Title", "Artist", "Time", "♥"]
        assert table.heart_col == 4


@pytest.mark.asyncio
async def test_tab_labels_lose_their_digits_when_the_terminal_is_narrow_and_the_details_tab_waits_for_a_page(monkeypatch):
    monkeypatch.setattr(yt_client, "get_album", lambda bid: {"title": "A", "tracks": [song(1)]})
    wide = TuneboxApp()
    async with wide.run_test(size=(150, 40)) as pilot:
        tabs = wide.query_one(TabbedContent)
        assert await wait_for(pilot, lambda: str(tabs.get_tab("home").label) == "1 Home")
        assert str(tabs.get_tab("settings").label) == "7 Settings"
        assert await wait_for(pilot, lambda: tabs.get_tab("detail").has_class("-hidden"))   # hidden until a page is opened
        wide.open_detail("album", {"browseId": "MPRx", "title": "A"})
        assert await wait_for(pilot, lambda: wide.active_tab == "detail" and not tabs.get_tab("detail").has_class("-hidden"))
        assert str(tabs.get_tab("detail").label) == "8 Details"

    narrow = TuneboxApp()
    async with narrow.run_test(size=(100, 36)) as pilot:
        tabs = narrow.query_one(TabbedContent)
        assert await wait_for(pilot, lambda: str(tabs.get_tab("home").label) == "Home")
        assert str(tabs.get_tab("downloads").label) == "Downloads"


# ------------------------------------------------------------------ player card and top bar

@pytest.mark.asyncio
async def test_player_card_shows_artist_album_and_a_separate_status_line(monkeypatch):
    monkeypatch.setattr(player, "current_track", song(4), raising=False)
    monkeypatch.setattr(player, "is_playing", True, raising=False)
    monkeypatch.setattr(player, "is_paused", False, raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        assert await wait_for(pilot, lambda: "Artist 4" in text_of(app.query_one("#np-artist", Static)))
        assert "Album 4" in text_of(app.query_one("#np-artist", Static))
        assert "[" not in text_of(app.query_one("#np-artist", Static))       # the status is no longer squeezed in brackets
        assert "Playing" in text_of(app.query_one("#np-status", Static))
        monkeypatch.setattr(player, "is_paused", True, raising=False)
        assert await wait_for(pilot, lambda: "Paused" in text_of(app.query_one("#np-status", Static)))


@pytest.mark.asyncio
async def test_top_bar_shows_account_and_sleep_state(monkeypatch):
    monkeypatch.setattr(yt_client, "authenticated", False, raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        bar = app.query_one("#topbar", TopBar)
        assert await wait_for(pilot, lambda: bar._shown and bar._shown[:2] == (False, 0))
        monkeypatch.setattr(yt_client, "authenticated", True, raising=False)
        player.set_sleep_timer(15)
        assert await wait_for(pilot, lambda: bar._shown and bar._shown[:2] == (True, 15))
        player.set_sleep_timer(0)


# ------------------------------------------------------------------ lyrics layout

@pytest.mark.asyncio
async def test_lyrics_pane_hides_a_meaningless_source_and_keeps_one_row_per_line(monkeypatch):
    monkeypatch.setattr(player, "current_track", song(5), raising=False)
    monkeypatch.setattr(player, "is_playing", True, raising=False)
    monkeypatch.setattr(player, "get_position", lambda: 0.0)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        app.lyrics_vid, app.lyrics_loading = "look5", False
        long_line = "a very long lyric line " * 12
        app.lyrics = {"lines": [{"time": 0.0, "text": "first"}, {"time": 5.0, "text": long_line}], "synced": True, "source": "None"}
        await pilot.press("4")
        view = app.query_one("#lyrics-view")
        assert await wait_for(pilot, lambda: app.active_tab == "lyrics" and view.window_start == -3 and view.lines)
        shown = text_of(view).split("\n")
        assert shown[0].strip() == "" and "None" not in text_of(view)         # no "None" heading
        assert shown[2].strip() == "first"                                   # row 3 of the pane = line 0, as clicks assume
        assert shown[3].strip().endswith("…") and len(shown) <= 5 + 40  # a long line is cut, not wrapped over rows


@pytest.mark.asyncio
async def test_changing_theme_recolours_the_list_cells_not_just_the_chrome(monkeypatch):
    monkeypatch.setattr(player, "current_track", song(1), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        table = app.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 4)

        def title_colour():
            return str(table.get_cell_at((1, 1)).style)          # row 1 is the playing song

        before = app.current_theme.primary
        assert before in title_colour()
        app.action_cycle_theme()
        assert await wait_for(pilot, lambda: app.current_theme.primary != before)
        assert await wait_for(pilot, lambda: app.current_theme.primary in title_colour())
        assert before not in title_colour()
