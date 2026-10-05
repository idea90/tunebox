"""Whole-list downloads: the batch engine, the D key, the top-bar progress, and `tunebox download-playlist`."""
import signal
import sys
import threading
import time

import pytest

from tunebox import config as cfg
from tunebox import main as cli
from tunebox.core import batch, downloader
from tunebox.core.database import add_track_to_playlist, create_playlist, get_downloads
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui.app import TuneboxApp
from tunebox.ui.panels import TopBar
from tunebox.ui.screens import ConfirmPrompt
from tunebox.ui.widgets import TrackTable


def song(i, **extra):
    t = {"videoId": f"bt{i}", "title": f"Song {i}", "artist": "Band", "artists": [{"name": "Band", "id": None}],
         "album": {"name": "LP", "id": None}, "duration": "3:00", "duration_seconds": 180, "type": "song"}
    t.update(extra)
    return t


async def wait_for(pilot, cond, timeout=5.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.02)
    return cond()


@pytest.fixture
def fake_download(monkeypatch, tmp_path):
    """Replace the real downloader: records every call, succeeds unless the title is in `fail`."""
    monkeypatch.setitem(cfg.config._data, "download_dir", str(tmp_path))
    monkeypatch.setitem(cfg.config._data, "download_format", "mp3")
    monkeypatch.setitem(cfg.config._data, "download_playlist_folders", True)
    monkeypatch.setattr(batch, "get_downloads", lambda: [])
    state = type("S", (), {"calls": [], "fail": {}, "have": []})()

    def fake(track, output_dir=None, fmt="mp3", progress_hook=None):
        state.calls.append((track["videoId"], output_dir, fmt))
        if track["videoId"] in state.fail:
            downloader.last_error = state.fail[track["videoId"]]
            return None
        return f"{output_dir or tmp_path}/{track['title']}.{fmt}"

    monkeypatch.setattr(downloader, "download_track_file", fake)
    monkeypatch.setattr(batch, "get_downloads", lambda: list(state.have))
    state.dir = tmp_path
    return state


# ------------------------------------------------------------------ plan

def test_plan_dedupes_and_counts_what_is_already_on_disk_in_this_format(fake_download):
    fake_download.have = [{"videoId": "bt1", "format": "mp3"}, {"videoId": "bt2", "format": "flac"}]
    tracks = [song(1), song(2), song(2), song(3), {"title": "removed video"}, {"videoId": "", "title": "gone"}]
    todo, skipped, unavailable = batch.plan(tracks, "mp3")
    assert [t["videoId"] for t in todo] == ["bt2", "bt3"]      # bt1 is done as mp3; bt2 is only a FLAC; bt2 listed twice counts once
    assert (skipped, unavailable) == (1, 2)


# ------------------------------------------------------------------ download_tracks

def test_downloads_every_song_in_order_into_a_folder_named_after_the_playlist(fake_download):
    events = []
    result = batch.download_tracks([song(1), song(2), song(3)], "mp3", subfolder="Road: trip?",
                                   on_progress=lambda ev, done, total, tr, detail: events.append((ev, done, total, tr["videoId"])))
    assert [c[0] for c in fake_download.calls] == ["bt1", "bt2", "bt3"]
    assert all(c[1] == str(fake_download.dir / "Road_ trip_") and c[2] == "mp3" for c in fake_download.calls)   # unsafe characters replaced
    assert (result.total, len(result.downloaded), result.skipped, result.failed, result.cancelled) == (3, 3, 0, [], False)
    assert events == [("start", 0, 3, "bt1"), ("done", 1, 3, "bt1"), ("start", 1, 3, "bt2"), ("done", 2, 3, "bt2"),
                      ("start", 2, 3, "bt3"), ("done", 3, 3, "bt3")]


def test_folders_can_be_turned_off_and_a_missing_name_means_flat(fake_download, monkeypatch):
    batch.download_tracks([song(1)], "mp3", subfolder=None)
    monkeypatch.setitem(cfg.config._data, "download_playlist_folders", False)
    batch.download_tracks([song(2)], "mp3", subfolder="Anything")
    assert [c[1] for c in fake_download.calls] == [None, None]


def test_skipped_songs_count_as_done_and_are_never_downloaded_again(fake_download):
    fake_download.have = [{"videoId": "bt1", "format": "mp3"}]
    events = []
    result = batch.download_tracks([song(1), song(2)], "mp3",
                                   on_progress=lambda ev, done, total, tr, d: events.append((ev, done, total)))
    assert [c[0] for c in fake_download.calls] == ["bt2"]
    assert (result.total, result.skipped, len(result.downloaded)) == (2, 1, 1)
    assert events == [("start", 1, 2), ("done", 2, 2)]            # the skipped one is already counted when the first starts


def test_a_failed_song_is_reported_and_the_rest_still_download(fake_download):
    fake_download.fail = {"bt2": "Video unavailable"}
    result = batch.download_tracks([song(1), song(2), song(3)], "mp3")
    assert [c[0] for c in fake_download.calls] == ["bt1", "bt2", "bt3"]
    assert len(result.downloaded) == 2
    assert [(t["videoId"], why) for t, why in result.failed] == [("bt2", "Video unavailable")]


def test_an_unexpected_exception_is_a_failure_not_a_crash(fake_download, monkeypatch):
    def boom(track, **kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(downloader, "download_track_file", boom)
    result = batch.download_tracks([song(1)], "mp3")
    assert result.failed and "disk on fire" in result.failed[0][1]


def test_cancel_stops_after_the_song_in_progress(fake_download):
    cancel = threading.Event()

    def progress(event, done, total, track, detail):
        if event == "done" and track["videoId"] == "bt2":
            cancel.set()

    result = batch.download_tracks([song(i) for i in range(1, 6)], "mp3", on_progress=progress, cancel=cancel)
    assert [c[0] for c in fake_download.calls] == ["bt1", "bt2"]
    assert result.cancelled is True and len(result.downloaded) == 2


def test_a_broken_progress_callback_does_not_stop_the_downloads(fake_download):
    def bad(*a):
        raise ValueError("ui exploded")
    result = batch.download_tracks([song(1), song(2)], "mp3", on_progress=bad)
    assert len(result.downloaded) == 2


def test_unknown_format_falls_back_to_the_configured_one(fake_download, monkeypatch):
    monkeypatch.setitem(cfg.config._data, "download_format", "flac")
    batch.download_tracks([song(1)], "wav")
    assert fake_download.calls[0][2] == "flac"


def test_real_downloader_end_to_end_writes_into_the_folder_indexes_and_skips_next_time(monkeypatch, tmp_path):
    """No fakes except yt-dlp itself: files land in the playlist folder, the Downloads index knows them, and a
    second run has nothing left to do."""
    from pathlib import Path
    monkeypatch.setitem(cfg.config._data, "download_dir", str(tmp_path))
    monkeypatch.setitem(cfg.config._data, "download_playlist_folders", True)

    class FakeYDL:
        def __init__(self, opts):
            self.template = opts["outtmpl"]
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls):
            Path(self.template.replace("%(ext)s", "mp3")).write_bytes(b"x" * 20000)

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(downloader, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(downloader, "write_tags", lambda path, track: True)
    tracks = [song(f"e2e{i}") for i in range(3)]
    result = batch.download_tracks(tracks, "mp3", subfolder="E2E Mix")
    assert len(result.downloaded) == 3 and not result.failed
    folder = tmp_path / "E2E Mix"
    assert sorted(p.name for p in folder.iterdir()) == ["Band - Song e2e0.mp3", "Band - Song e2e1.mp3", "Band - Song e2e2.mp3"]
    indexed = {d["videoId"] for d in get_downloads()}
    assert {t["videoId"] for t in tracks} <= indexed

    todo, skipped, _ = batch.plan(tracks, "mp3")
    assert todo == [] and skipped == 3                                  # re-running does nothing


# ------------------------------------------------------------------ ytmusic: whole playlists, not the first 100

def test_get_playlist_can_fetch_everything(monkeypatch):
    seen = []

    class Fake:
        def get_playlist(self, pid, limit=100):
            seen.append(limit)
            return {"title": "Big", "tracks": [{"videoId": "a", "title": "A"}]}

    monkeypatch.setattr(yt_client, "_yt", Fake())
    assert yt_client.get_playlist("PL1")["tracks"][0]["videoId"] == "a"
    yt_client.get_playlist("PL1", limit=None)
    assert seen == [100, None]


# ------------------------------------------------------------------ the D key

@pytest.fixture
def app_env(monkeypatch, fake_download):
    import pygame
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(player, "_trigger_prefetch", lambda: None)
    monkeypatch.setattr(player, "queue", [], raising=False)
    monkeypatch.setattr(player, "current_track", None, raising=False)
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [{"title": "Top", "items": [song(1), song(2)]}])
    return fake_download


async def prompt_text(pilot, app) -> str:
    """Text of the open yes / no question (waits for the dialog to finish building)."""
    assert await wait_for(pilot, lambda: bool(app.screen.query("#confirm-text")))
    return text_of(app.screen.query_one("#confirm-text"))


def rendered(widget) -> str:
    """What a widget holding a Rich renderable (the top bar's table) actually shows."""
    import io
    from rich.console import Console
    console = Console(width=150, record=True, file=io.StringIO())
    console.print(getattr(widget, "content", None) or widget.renderable)
    return console.export_text()


def toasts(app):
    return [str(n.message) for n in app._notifications]


def text_of(widget) -> str:
    content = getattr(widget, "content", None) or widget.renderable
    return content.plain if hasattr(content, "plain") else str(content)


class SlowBatch:
    """Stands in for batch.download_tracks: reports progress, then waits until released (or cancelled)."""
    def __init__(self, monkeypatch, result_factory=None, skipped=0):
        self.release, self.calls, self.cancel = threading.Event(), [], None
        self.result_factory, self.skipped = result_factory, skipped      # skipped songs count as done, as in the real engine
        monkeypatch.setattr(batch, "download_tracks", self)

    def __call__(self, tracks, fmt=None, subfolder=None, on_progress=None, cancel=None):
        self.calls.append((list(tracks), fmt, subfolder))
        self.cancel = cancel
        total = len(tracks)
        on_progress("start", self.skipped, total, tracks[0], "")
        on_progress("done", self.skipped + 1, total, tracks[0], "x")
        deadline = time.time() + 15                       # a failing test must never leave this thread waiting forever
        while not (self.release.is_set() or (cancel is not None and cancel.is_set())) and time.time() < deadline:
            time.sleep(0.02)
        if self.result_factory:
            return self.result_factory(tracks, cancel)
        done = tracks if not (cancel and cancel.is_set()) else tracks[:1]
        return batch.BatchResult(total=total, downloaded=[f"/d/{t['title']}.mp3" for t in done],
                                 cancelled=bool(cancel and cancel.is_set()))


def open_playlist_page(app, monkeypatch, page_tracks, full_tracks):
    calls = []

    def get_playlist(pid, limit=100):
        calls.append((pid, limit))
        return {"title": "My Mix", "tracks": page_tracks if limit == 100 else full_tracks}

    monkeypatch.setattr(yt_client, "get_playlist", get_playlist)
    app.open_detail("playlist", {"browseId": "PLmix", "title": "My Mix"})
    return calls


@pytest.mark.asyncio
async def test_D_on_a_playlist_page_fetches_all_of_it_asks_then_downloads_with_progress(app_env, monkeypatch):
    slow = SlowBatch(monkeypatch, skipped=1)
    app_env.have = [{"videoId": "bt1", "format": "mp3"}]
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        calls = open_playlist_page(app, monkeypatch, [song(1), song(2)], [song(i) for i in range(1, 6)])
        assert await wait_for(pilot, lambda: app.active_tab == "detail" and len(app.detail.get("tracks", [])) == 2)
        app.detail_table_focus = app.query_one("#t-detail", TrackTable).focus()
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        message = await prompt_text(pilot, app)
        assert 'Download 4 songs from "My Mix" as MP3?' in message          # 5 in the playlist, 1 already on disk
        assert "1 song already downloaded will be skipped." in message
        assert ("PLmix", None) in calls                                       # the whole playlist was fetched, not the first 100

        await pilot.press("enter")
        assert await wait_for(pilot, lambda: app.batch is not None and slow.calls)
        assert [t["videoId"] for t in slow.calls[0][0]] == ["bt1", "bt2", "bt3", "bt4", "bt5"]
        assert slow.calls[0][1:] == ("mp3", "My Mix")
        bar = app.query_one("#topbar", TopBar)
        assert await wait_for(pilot, lambda: bar._shown and bar._shown[3] == "2/5")   # 1 skipped + 1 done so far
        assert await wait_for(pilot, lambda: "↓ 2/5" in rendered(bar))

        slow.release.set()
        assert await wait_for(pilot, lambda: app.batch is None)
        assert await wait_for(pilot, lambda: any('Downloaded 5 songs from "My Mix"' in t for t in toasts(app)))
        assert await wait_for(pilot, lambda: bar._shown and bar._shown[3] == "")       # progress cleared


@pytest.mark.asyncio
async def test_escape_on_the_question_downloads_nothing(app_env, monkeypatch):
    slow = SlowBatch(monkeypatch)
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        open_playlist_page(app, monkeypatch, [song(1)], [song(1), song(2)])
        assert await wait_for(pilot, lambda: app.active_tab == "detail" and app.detail.get("tracks"))
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: not isinstance(app.screen, ConfirmPrompt))
        await pilot.pause(0.3)
        assert slow.calls == [] and app.batch is None


@pytest.mark.asyncio
async def test_pressing_D_while_downloading_offers_to_stop(app_env, monkeypatch):
    slow = SlowBatch(monkeypatch)
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        open_playlist_page(app, monkeypatch, [song(1)], [song(i) for i in range(1, 5)])
        assert await wait_for(pilot, lambda: app.active_tab == "detail" and app.detail.get("tracks"))
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        await pilot.press("y")
        assert await wait_for(pilot, lambda: app.batch is not None and slow.calls)
        await pilot.press("D")                                                 # again: stop?
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        assert 'Stop downloading "My Mix"?' in await prompt_text(pilot, app)
        await pilot.press("escape")                                            # no: keep going
        await pilot.pause(0.2)
        assert app.batch is not None and not slow.cancel.is_set()
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        await pilot.press("enter")                                             # yes: stop
        assert await wait_for(pilot, lambda: slow.cancel.is_set())
        assert await wait_for(pilot, lambda: app.batch is None)
        assert await wait_for(pilot, lambda: any(t.startswith("Stopped: 1 of 4 downloaded") for t in toasts(app)))


@pytest.mark.asyncio
async def test_failures_are_listed_in_the_summary_and_the_app_recovers(app_env, monkeypatch):
    def result(tracks, cancel):
        return batch.BatchResult(total=3, downloaded=["/d/a.mp3"], failed=[(song(2), "Video unavailable"), (song(3), "Private video")])

    slow = SlowBatch(monkeypatch, result)
    slow.release.set()
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        open_playlist_page(app, monkeypatch, [song(1)], [song(1), song(2), song(3)])
        assert await wait_for(pilot, lambda: app.active_tab == "detail" and app.detail.get("tracks"))
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: any("Downloaded 1 of 3" in t for t in toasts(app)))
        summary = next(t for t in toasts(app) if "Downloaded 1 of 3" in t)
        assert "Song 2 (Video unavailable)" in summary and "Song 3 (Private video)" in summary and "retry" in summary
        assert app.batch is None


@pytest.mark.asyncio
async def test_a_crash_in_the_batch_never_leaves_the_app_stuck_downloading(app_env, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(batch, "download_tracks", broken)
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        open_playlist_page(app, monkeypatch, [song(1)], [song(1)])
        assert await wait_for(pilot, lambda: app.active_tab == "detail" and app.detail.get("tracks"))
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: any("kaboom" in t for t in toasts(app)))
        assert await wait_for(pilot, lambda: app.batch is None)


@pytest.mark.asyncio
async def test_everything_already_downloaded_says_so_instead_of_asking(app_env, monkeypatch):
    slow = SlowBatch(monkeypatch)
    app_env.have = [{"videoId": "bt1", "format": "mp3"}, {"videoId": "bt2", "format": "mp3"}]
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        open_playlist_page(app, monkeypatch, [song(1)], [song(1), song(2)])
        assert await wait_for(pilot, lambda: app.active_tab == "detail" and app.detail.get("tracks"))
        await pilot.press("D")
        assert await wait_for(pilot, lambda: any("already downloaded" in t for t in toasts(app)))
        assert not isinstance(app.screen, ConfirmPrompt) and slow.calls == []


@pytest.mark.asyncio
async def test_D_on_the_home_list_does_nothing_by_design(app_env, monkeypatch):
    slow = SlowBatch(monkeypatch)
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        table = app.query_one("#t-home", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 3 and app.focused is table)
        await pilot.press("D")
        assert await wait_for(pilot, lambda: any("Open a playlist or album" in t for t in toasts(app)))
        assert not isinstance(app.screen, ConfirmPrompt) and slow.calls == []


@pytest.mark.asyncio
async def test_D_on_the_queue_tab_downloads_the_queue_flat(app_env, monkeypatch):
    slow = SlowBatch(monkeypatch)
    player.queue = [song(7), song(8), song(7)]                                # a repeated song is only fetched once
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("3")
        assert await wait_for(pilot, lambda: app.active_tab == "queue")
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        assert 'Download 2 songs from "the queue" as MP3?' in await prompt_text(pilot, app)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: bool(slow.calls))
        assert slow.calls[0][2] is None                                       # flat: no folder for the queue
        slow.release.set()
        assert await wait_for(pilot, lambda: app.batch is None)


@pytest.mark.asyncio
async def test_D_on_a_highlighted_local_playlist_in_the_library(app_env, monkeypatch):
    slow = SlowBatch(monkeypatch)
    pl = create_playlist("Gym mix")
    for i in (21, 22):
        add_track_to_playlist(pl["id"], song(i))
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("5")
        assert await wait_for(pilot, lambda: app.active_tab == "library")
        await pilot.click("#lib-playlists")
        table = app.query_one("#t-library", TrackTable)
        assert await wait_for(pilot, lambda: any(i.get("title") == "Gym mix" for i in table.items))
        table.move_cursor(row=next(i for i, it in enumerate(table.items) if it.get("title") == "Gym mix"))
        table.focus()
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        assert 'Download 2 songs from "Gym mix"' in await prompt_text(pilot, app)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: bool(slow.calls))
        assert [t["videoId"] for t in slow.calls[0][0]] == ["bt21", "bt22"] and slow.calls[0][2] == "Gym mix"
        slow.release.set()
        assert await wait_for(pilot, lambda: app.batch is None)


@pytest.mark.asyncio
async def test_D_on_a_highlighted_album_row_in_search_results_fetches_the_album(app_env, monkeypatch):
    slow = SlowBatch(monkeypatch)
    slow.release.set()
    fetched = []
    monkeypatch.setattr(yt_client, "get_album", lambda bid: fetched.append(bid) or {"title": "Night LP", "tracks": [song(31), song(32), song(33)]})
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        app.search_items = [{"type": "album", "title": "Night LP", "browseId": "MPREx", "artist": "Band"}]
        app.refresh_tables()
        await pilot.press("2")
        table = app.query_one("#t-search", TrackTable)
        assert await wait_for(pilot, lambda: app.active_tab == "search" and table.row_count == 1)
        table.focus()
        await pilot.press("D")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmPrompt))
        assert 'Download 3 songs from "Night LP"' in await prompt_text(pilot, app)
        assert fetched == ["MPREx"]


@pytest.mark.asyncio
async def test_the_detail_page_has_a_download_all_button_and_settings_can_change_the_format(app_env):
    app = TuneboxApp()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.pause(0.3)
        assert app.query_one("#d-download")._chip_action == "download_all"
        chip = app.query_one("#s-dlfmt")
        assert await wait_for(pilot, lambda: "Download format: MP3" in text_of(chip))
        for expected in ("M4A", "FLAC", "MP3"):
            app.action_cycle_download_format()
            assert await wait_for(pilot, lambda: f"Download format: {expected}" in text_of(chip))
        assert downloader.download_format() == "mp3"


# ------------------------------------------------------------------ command line

@pytest.mark.parametrize("source,expected", [
    ("https://music.youtube.com/playlist?list=PLabc_-9&si=zzz", ("playlist", "PLabc_-9")),
    ("https://www.youtube.com/watch?v=xyz&list=PLdef", ("playlist", "PLdef")),
    ("VLPLghi", ("playlist", "PLghi")),
    ("PLjkl", ("playlist", "PLjkl")),
    ("OLAK5uy_mno", ("playlist", "OLAK5uy_mno")),
    ("MPREb_pqr", ("album", "MPREb_pqr")),
    ("https://music.youtube.com/browse/MPREb_stu", ("album", "MPREb_stu")),
])
def test_playlist_source_forms(source, expected, monkeypatch):
    monkeypatch.setattr(cli, "get_playlists", lambda: [])
    if source.startswith("https://music.youtube.com/browse/"):
        source = source.rsplit("/", 1)[1]                              # a bare browse id; links to /browse/ are not supported
    assert cli.resolve_playlist_source(source) == expected


def test_a_local_playlist_name_wins_and_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(cli, "get_playlists", lambda: [{"id": "abc123", "title": "Road Trip"}])
    assert cli.resolve_playlist_source("road trip") == ("local", "abc123")
    assert cli.resolve_playlist_source("PLroad") == ("playlist", "PLroad")


@pytest.fixture
def cli_env(monkeypatch, fake_download):
    monkeypatch.setattr(cli, "get_playlists", lambda: [])
    seen = []

    def get_playlist(pid, limit=100):
        seen.append((pid, limit))
        return {"title": "Big Mix", "tracks": [song(1), song(2), song(3)]}

    monkeypatch.setattr(yt_client, "get_playlist", get_playlist)
    fake_download.fetches = seen
    return fake_download


def test_cli_downloads_the_whole_playlist(cli_env, capsys):
    cli.download_playlist_cli("PLbig", fmt="mp3", assume_yes=True)
    out = capsys.readouterr().out
    assert cli_env.fetches == [("PLbig", None)]                        # all of it, not the first 100
    assert [c[0] for c in cli_env.calls] == ["bt1", "bt2", "bt3"]
    assert all(c[1].endswith("Big Mix") for c in cli_env.calls)
    assert "3 to download as MP3" in out and "Downloaded 3 songs" in out and "(1/3)" in out


def test_cli_asks_first_and_respects_no(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    cli.download_playlist_cli("PLbig", fmt="mp3")
    assert cli_env.calls == [] and "Cancelled" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    cli.download_playlist_cli("PLbig", fmt="mp3")
    assert len(cli_env.calls) == 3


def test_cli_without_a_terminal_needs_yes(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    cli.download_playlist_cli("PLbig", fmt="mp3")
    assert cli_env.calls == [] and "--yes" in capsys.readouterr().out


def test_cli_reports_failures_and_what_to_do(cli_env, capsys):
    cli_env.fail = {"bt2": "Video unavailable"}
    cli.download_playlist_cli("PLbig", fmt="m4a", assume_yes=True)
    out = capsys.readouterr().out
    assert "failed: Video unavailable" in out and "2 downloaded, 1 failed" in out and "retry" in out
    assert all(c[2] == "m4a" for c in cli_env.calls)


def test_cli_nothing_found_and_nothing_new(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(yt_client, "get_playlist", lambda pid, limit=100: {"title": "x", "tracks": []})
    cli.download_playlist_cli("PLnone", fmt="mp3", assume_yes=True)
    assert "Nothing found" in capsys.readouterr().out and cli_env.calls == []

    monkeypatch.setattr(yt_client, "get_playlist", lambda pid, limit=100: {"title": "x", "tracks": [song(1)]})
    cli_env.have = [{"videoId": "bt1", "format": "mp3"}]
    cli.download_playlist_cli("PLdone", fmt="mp3", assume_yes=True)
    assert "Nothing to do" in capsys.readouterr().out and cli_env.calls == []


def test_cli_downloads_one_of_your_own_playlists(cli_env, monkeypatch):
    monkeypatch.setattr(cli, "get_playlists", lambda: [{"id": "loc1", "title": "Mine"}])
    monkeypatch.setattr(cli, "get_local_playlist", lambda pid: {"title": "Mine", "tracks": [song(9)]} if pid == "loc1" else None)
    cli.download_playlist_cli("mine", fmt="mp3", assume_yes=True)
    assert [c[0] for c in cli_env.calls] == ["bt9"] and cli_env.fetches == []


def test_cli_ctrl_c_stops_after_the_song_and_restores_the_handler(cli_env, monkeypatch, capsys):
    before = signal.getsignal(signal.SIGINT)
    original = downloader.download_track_file

    def interrupting(track, output_dir=None, fmt="mp3", progress_hook=None):
        out = original(track, output_dir=output_dir, fmt=fmt)
        if track["videoId"] == "bt1":
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)            # as if the user pressed Ctrl+C
        return out

    monkeypatch.setattr(downloader, "download_track_file", interrupting)
    cli.download_playlist_cli("PLbig", fmt="mp3", assume_yes=True)
    assert [c[0] for c in cli_env.calls] == ["bt1"]
    assert "Stopped" in capsys.readouterr().out
    signal.signal(signal.SIGINT, before)                                   # the stop handler restores SIG_DFL itself; put it back


def test_cli_argument_parsing(monkeypatch):
    got = {}
    monkeypatch.setattr(cli, "download_playlist_cli", lambda source, fmt, assume_yes: got.update(source=source, fmt=fmt, yes=assume_yes))
    monkeypatch.setattr(sys, "argv", ["tunebox", "download-playlist", "PLx", "-y", "-f", "flac"])
    cli.main()
    assert got == {"source": "PLx", "fmt": "flac", "yes": True}
    monkeypatch.setattr(sys, "argv", ["tunebox", "download-playlist", "PLy"])
    cli.main()
    assert got["fmt"] == downloader.download_format() and got["yes"] is False
