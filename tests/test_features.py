"""Tests for session restore, gapless, sleep timer, queue editing, detail pages, suggestions, login, media keys."""
import asyncio
import json
import time
from types import SimpleNamespace

import pygame
import pytest
from textual.widgets import TabbedContent

from tunebox import config as cfg
from tunebox.core import session, downloader, mediakeys
from tunebox.core.database import add_download, get_downloads
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui.app import TuneboxApp
from tunebox.ui.suggest import SearchSuggester
from tunebox.ui.widgets import TrackTable


def tr(i, **kw):
    t = {"type": "song", "videoId": f"v{i}", "title": f"Song {i}", "artist": f"Artist {i}",
         "artists": [{"name": f"Artist {i}", "id": f"UCartist{i}"}], "album": {"name": f"Album {i}", "id": f"MPREalbum{i}"},
         "duration": "3:00", "duration_seconds": 180}
    t.update(kw)
    return t


async def wait_for(pilot, cond, timeout=5.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


async def ready(pilot, app):
    """Wait for the app's own initial Home activation to settle; switching tabs mid-startup races with it."""
    assert await wait_for(pilot, lambda: app.tab_events >= 1 and app.query("#t-detail-albums")), "initial tab never activated"
    await pilot.pause(0.1)


@pytest.fixture(autouse=True)
def quiet_player(monkeypatch):
    """No network prefetch threads; clean player state per test."""
    monkeypatch.setattr(player, "_trigger_prefetch", lambda: None)
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    player.queue, player.queue_index = [], -1
    player.current_track, player.is_playing, player.is_paused = None, False, False
    player.sleep_deadline, player._resume_pos, player._armed = None, 0.0, None
    player.shuffle, player.repeat_mode, player.autoplay = False, "off", False
    yield
    player.sleep_deadline = None
    player.is_playing = False


# ------------------------------------------------------------------ session

@pytest.mark.real_session
def test_session_roundtrip_restores_queue_song_and_position(monkeypatch):
    player.queue, player.queue_index = [tr(1), tr(2), tr(3)], 1
    player.current_track, player.is_playing = player.queue[1], True
    monkeypatch.setattr(player, "get_position", lambda: 42.5)
    assert session.save(player)

    player.queue, player.queue_index, player.current_track, player.is_playing = [], -1, None, False
    assert session.restore(player)
    assert [t["videoId"] for t in player.queue] == ["v1", "v2", "v3"]
    assert player.queue_index == 1 and player.current_track["videoId"] == "v2"
    assert player.is_playing is False                      # restored paused, not auto-playing
    assert player.get_position() == 42.5                   # the seek bar shows where you were


@pytest.mark.real_session
def test_corrupt_or_missing_session_is_ignored():
    cfg.SESSION_FILE.write_text("{not json", encoding="utf-8")
    assert session.restore(player) is False
    cfg.SESSION_FILE.unlink()
    assert session.restore(player) is False


def test_play_after_restore_resumes_mid_song(monkeypatch):
    started = []
    monkeypatch.setattr(player, "_load_and_play", lambda track, seek, record_history: started.append((track["videoId"], seek, record_history)) or True)
    player.restore([tr(1), tr(2)], 1, 77.0)
    player.toggle_pause()
    assert started == [("v2", 77.0, False)]               # resumes at 77s and doesn't double-count history


# ------------------------------------------------------------------ sleep timer

def test_sleep_timer_pauses_playback_when_it_expires(monkeypatch):
    infos = []
    monkeypatch.setattr(player, "on_info", infos.append)
    monkeypatch.setattr(pygame.mixer.music, "pause", lambda: None)
    player.current_track, player.is_playing = tr(1), True
    player.set_sleep_timer(30)
    assert player.sleep_remaining() == 30
    player.sleep_deadline = time.time() - 1                # pretend the 30 minutes passed
    deadline = time.time() + 3
    while not player.is_paused and time.time() < deadline:
        time.sleep(0.05)
    assert player.is_paused and player.sleep_deadline is None
    assert infos and "Sleep timer" in infos[0]


def test_sleep_timer_can_be_cancelled():
    player.set_sleep_timer(15)
    player.set_sleep_timer(0)
    assert player.sleep_deadline is None and player.sleep_remaining() == 0


# ------------------------------------------------------------------ queue editing

def test_move_in_queue_keeps_current_track_current():
    player.queue, player.queue_index = [tr(0), tr(1), tr(2), tr(3)], 1
    assert player.move_in_queue(1, +1) == 2                # move the playing song down
    assert [t["videoId"] for t in player.queue] == ["v0", "v2", "v1", "v3"]
    assert player.queue[player.queue_index]["videoId"] == "v1"
    assert player.move_in_queue(3, +1) == 3                # already last: no-op
    player.move_in_queue(0, +1)                            # swap v0 past v2; current (v1) index unaffected
    assert player.queue[player.queue_index]["videoId"] == "v1"


def test_move_over_playing_track_adjusts_index():
    player.queue, player.queue_index = [tr(0), tr(1), tr(2)], 0
    player.move_in_queue(1, -1)                            # v1 jumps above the playing v0
    assert player.queue[player.queue_index]["videoId"] == "v0" and player.queue_index == 1


# ------------------------------------------------------------------ gapless

def test_gapless_advance_updates_state_without_reloading(monkeypatch):
    seen = []
    monkeypatch.setattr(player, "on_track_change", seen.append)
    monkeypatch.setattr("tunebox.core.player.add_history", lambda t: None)
    player.queue, player.queue_index = [tr(0), tr(1)], 0
    player.current_track, player.is_playing = player.queue[0], True
    player._armed = (player._gen, 1, player.queue[1])
    player._on_gapless_advance()
    assert player.queue_index == 1 and player.current_track["videoId"] == "v1"
    assert player.seek_offset == 0.0 and player._armed is None
    assert [t["videoId"] for t in seen] == ["v1"]


def test_stale_gapless_arm_is_ignored():
    player.queue, player.queue_index = [tr(0), tr(1)], 0
    player.current_track, player.is_playing = player.queue[0], True
    player._armed = (player._gen - 1, 1, player.queue[1])  # armed before the user skipped
    player._on_gapless_advance()
    assert player.queue_index == 0


def test_monitor_detects_mixer_handover_by_position_drop(monkeypatch):
    """The mixer keeps get_busy() true across a queued hand-over; get_pos() restarting is the only signal."""
    monkeypatch.setattr("tunebox.core.player.add_history", lambda t: None)
    player.queue, player.queue_index = [tr(0), tr(1)], 0
    player.current_track, player.is_playing = player.queue[0], True
    player._armed = (player._gen, 1, player.queue[1])
    player._last_pos_ms = 0
    pos = {"ms": 200_000}
    monkeypatch.setattr(pygame.mixer.music, "get_pos", lambda: pos["ms"])
    time.sleep(0.6)                                        # monitor records the high position
    pos["ms"] = 30                                         # queued track started
    deadline = time.time() + 3
    while player.queue_index != 1 and time.time() < deadline:
        time.sleep(0.05)
    assert player.queue_index == 1 and player.current_track["videoId"] == "v1"


# ------------------------------------------------------------------ normalization

@pytest.mark.parametrize("enabled", [True, False])
def test_loudness_normalization_flag_controls_ffmpeg_filter(monkeypatch, enabled):
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls): pass

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(downloader, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(downloader, "get_cached_track_path", lambda vid: None)
    cfg.config.set("normalize_volume", enabled)
    downloader.cache_track_audio({"videoId": "zzz"})
    args = captured.get("postprocessor_args", {}).get("extractaudio", [])
    assert (downloader.LOUDNORM in args) is enabled


# ------------------------------------------------------------------ suggestions / history

def test_search_history_is_deduplicated_recent_first_and_capped():
    cfg.config.set("search_history", [])
    for q in ["daft punk", "Queen", "DAFT PUNK", "  "]:
        cfg.config.add_search(q)
    assert cfg.config.get("search_history") == ["DAFT PUNK", "Queen"]
    for i in range(50):
        cfg.config.add_search(f"q{i}")
    assert len(cfg.config.get("search_history")) == 30


def test_suggester_prefers_history_then_api(monkeypatch):
    cfg.config.set("search_history", ["queen bohemian rhapsody"])
    monkeypatch.setattr(yt_client, "get_suggestions", lambda q: ["daft punk get lucky", "other"])
    s = SearchSuggester()
    assert asyncio.run(s.get_suggestion("que")) == "queen bohemian rhapsody"
    assert asyncio.run(s.get_suggestion("daft")) == "daft punk get lucky"
    assert asyncio.run(s.get_suggestion("d")) is None       # too short to be worth a request
    assert asyncio.run(s.get_suggestion("zzzz")) is None


# ------------------------------------------------------------------ artist page parsing / login / media keys

def test_get_artist_merges_full_song_list_and_albums(monkeypatch):
    fake = SimpleNamespace(get_artist=lambda bid: {
        "name": "Coldplay", "subscribers": "30M",
        "songs": {"results": [{"videoId": "a1", "title": "Yellow", "artists": [{"name": "Coldplay", "id": "UC1"}]}],
                  "browseId": "VLfull"},
        "albums": {"results": [{"browseId": "MPREb_1", "title": "Parachutes", "year": "2000"}]},
        "singles": {"results": [{"browseId": "MPREb_2", "title": "Yellow", "year": "2000"}]},
    })
    monkeypatch.setattr(yt_client, "_yt", fake)
    monkeypatch.setattr(yt_client, "get_playlist", lambda pid: {"tracks": [{"videoId": "a1"}, {"videoId": "a2"}]})
    data = yt_client.get_artist("UC1")
    assert data["title"] == "Coldplay" and [t["videoId"] for t in data["tracks"]] == ["a1", "a2"]
    assert [a["title"] for a in data["albums"]] == ["Parachutes", "Yellow"]
    assert data["albums"][1]["artist"].startswith("Single")


def test_login_success_and_failure(monkeypatch, tmp_path):
    import ytmusicapi
    from tunebox import main
    monkeypatch.setattr(cfg, "AUTH_FILE", cfg.APP_DIR / "ytmusic_auth.json")
    hdr = tmp_path / "h.txt"
    hdr.write_text("cookie: x\nuser-agent: y\n", encoding="utf-8")

    monkeypatch.setattr(ytmusicapi, "setup", lambda filepath, headers_raw: open(filepath, "w").write("{}"))
    class OkYT:
        def __init__(self, auth=None): pass
        def get_library_playlists(self, limit=1): return []
    monkeypatch.setattr(ytmusicapi, "YTMusic", OkYT)
    main.login_cli(str(hdr))
    assert cfg.AUTH_FILE.exists()
    main.logout_cli()
    assert not cfg.AUTH_FILE.exists()

    class BadYT(OkYT):
        def get_library_playlists(self, limit=1): raise RuntimeError("401 unauthorized")
    monkeypatch.setattr(ytmusicapi, "YTMusic", BadYT)
    main.login_cli(str(hdr))
    assert not cfg.AUTH_FILE.exists(), "invalid credentials must not be left on disk"


def test_media_key_mapping_covers_all_transport_keys():
    K = SimpleNamespace(media_play_pause="PP", media_next="NX", media_previous="PV", media_stop="ST")
    m = mediakeys.key_map(SimpleNamespace(Key=K))
    assert m == {"PP": "play_pause", "NX": "next", "PV": "prev", "ST": "stop"}


def test_media_keys_degrade_gracefully_without_pynput(monkeypatch):
    import builtins
    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda name, *a, **k: (_ for _ in ()).throw(ImportError()) if name.startswith("pynput") else real_import(name, *a, **k))
    monkeypatch.setattr(mediakeys, "_listener", None)
    assert mediakeys.start(lambda a: None) is False


# ------------------------------------------------------------------ UI

@pytest.mark.asyncio
async def test_artist_row_opens_detail_page_and_play_all(monkeypatch):
    played = []
    monkeypatch.setattr(player, "play", lambda track=None, queue=None, index=0: played.append((queue, index)))
    monkeypatch.setattr(yt_client, "search", lambda q, filter_type=None, limit=25: [
        {"type": "artist", "browseId": "UCx", "title": "Coldplay", "name": "Coldplay"}])
    monkeypatch.setattr(yt_client, "get_artist", lambda bid, name="": {
        "title": "Coldplay", "subscribers": "30M", "tracks": [tr(1), tr(2)],
        "albums": [{"type": "album", "browseId": "MPREb_1", "title": "Parachutes", "artist": "Album - 2000"}]})
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        app.query_one(TabbedContent).active = "search"
        assert await wait_for(pilot, lambda: app.active_tab == "search")
        app._set_search("coldplay", yt_client.search("coldplay"))
        table = app.query_one("#t-search", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 1)
        await pilot.pause(0.3)                              # let the row paint, as a person would before clicking
        await pilot.click("#t-search", offset=(10, 1))
        assert await wait_for(pilot, lambda: app.active_tab == "detail")
        assert await wait_for(pilot, lambda: len(app.detail.get("tracks", [])) == 2)
        assert await wait_for(pilot, lambda: app.query_one("#t-detail", TrackTable).row_count == 2)
        assert await wait_for(pilot, lambda: app.query_one("#t-detail-albums", TrackTable).row_count == 1)
        await pilot.click("#d-play")
        assert await wait_for(pilot, lambda: bool(played))
        assert [t["videoId"] for t in played[-1][0]] == ["v1", "v2"] and played[-1][1] == 0
        await pilot.click("#d-back")
        assert await wait_for(pilot, lambda: app.active_tab == "search")


@pytest.mark.asyncio
async def test_g_and_b_open_artist_and_album_for_playing_song(monkeypatch):
    opened = []
    monkeypatch.setattr(yt_client, "get_artist", lambda bid, name="": opened.append(("artist", bid)) or {"title": "A", "tracks": [], "albums": []})
    monkeypatch.setattr(yt_client, "get_album", lambda bid: opened.append(("album", bid)) or {"title": "B", "tracks": []})
    player.current_track = tr(5)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        await pilot.press("g")
        assert await wait_for(pilot, lambda: ("artist", "UCartist5") in opened)
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: app.active_tab != "detail")
        await pilot.press("b")
        assert await wait_for(pilot, lambda: ("album", "MPREalbum5") in opened)


@pytest.mark.asyncio
async def test_queue_keys_play_next_add_and_reorder(monkeypatch):
    nexts, adds = [], []
    monkeypatch.setattr(player, "play_next", lambda t: nexts.append(t["videoId"]))
    monkeypatch.setattr(player, "add_to_queue", lambda t: adds.append(t["videoId"]))
    player.current_track = tr(1)
    player.queue, player.queue_index = [tr(1), tr(2), tr(3)], 0
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        await pilot.press("N")
        assert await wait_for(pilot, lambda: nexts == ["v1"])
        await pilot.press("E")
        assert await wait_for(pilot, lambda: adds == ["v1"])

        await pilot.press("3")
        assert await wait_for(pilot, lambda: app.active_tab == "queue")
        table = app.query_one("#t-queue", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 3 and app.focused is table)
        table.move_cursor(row=1)
        await pilot.press("shift+down")
        assert await wait_for(pilot, lambda: [t["videoId"] for t in player.queue] == ["v1", "v3", "v2"])
        assert table.cursor_row == 2                       # the cursor follows the moved song
        await pilot.press("shift+up")
        assert await wait_for(pilot, lambda: [t["videoId"] for t in player.queue] == ["v1", "v2", "v3"])


@pytest.mark.asyncio
async def test_delete_download_removes_file_and_row(tmp_path):
    f = tmp_path / "Artist - Song.mp3"
    f.write_bytes(b"x" * 2048)
    add_download(tr(9), str(f), f.stat().st_size, "mp3")
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("6")
        table = app.query_one("#t-downloads", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count >= 1 and app.focused is table)
        row = next(i for i, it in enumerate(table.items) if it["videoId"] == "v9")
        table.move_cursor(row=row)
        await pilot.press("x")
        assert await wait_for(pilot, lambda: not f.exists())
        assert all(d["videoId"] != "v9" for d in get_downloads())


@pytest.mark.asyncio
async def test_sleep_key_cycles_and_settings_show_disk_usage():
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        await pilot.press("z")
        assert await wait_for(pilot, lambda: player.sleep_remaining() == 15)
        await pilot.press("z")
        assert await wait_for(pilot, lambda: player.sleep_remaining() == 30)
        for _ in range(3):
            await pilot.press("z")
        assert await wait_for(pilot, lambda: player.sleep_deadline is None), "cycles back to off"
        await pilot.press("7")
        assert await wait_for(pilot, lambda: app.active_tab == "settings")
        usage = app.query_one("#s-usage")
        assert await wait_for(pilot, lambda: "Audio cache" in str(usage.render()))


@pytest.mark.real_session
@pytest.mark.asyncio
async def test_app_restores_session_on_launch_and_saves_on_quit(monkeypatch):
    monkeypatch.setattr(player, "get_position", lambda: 61.0)
    player.queue, player.queue_index, player.current_track = [tr(1), tr(2)], 1, tr(2)
    assert session.save(player)
    player.queue, player.queue_index, player.current_track = [], -1, None

    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        assert await wait_for(pilot, lambda: player.current_track and player.current_track["videoId"] == "v2")
        assert [t["videoId"] for t in player.queue] == ["v1", "v2"] and player.is_playing is False
        assert "Song 2" in str(app.query_one("#np-title").render())
        await pilot.press("q")
    saved = json.loads(cfg.SESSION_FILE.read_text(encoding="utf-8"))
    assert saved["index"] == 1 and saved["position"] == 61.0, "quit must save the position before stop() zeroes it"


@pytest.mark.asyncio
async def test_library_yt_chips_hidden_when_anonymous(monkeypatch):
    monkeypatch.setattr(yt_client, "authenticated", False, raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        assert await wait_for(pilot, lambda: app.query_one("#lib-yt_liked").display is False)   # hidden on the first tick
        monkeypatch.setattr(yt_client, "authenticated", True, raising=False)
        monkeypatch.setattr(yt_client, "get_liked_tracks", lambda limit=200: [tr(1), tr(2)])
        await pilot.press("5")
        assert await wait_for(pilot, lambda: app.active_tab == "library")
        assert await wait_for(pilot, lambda: app.query_one("#lib-yt_liked").display is True)
        await pilot.click("#lib-yt_liked")
        table = app.query_one("#t-library", TrackTable)
        assert await wait_for(pilot, lambda: table.row_count == 2)


def test_seek_resets_gapless_baseline_so_it_is_not_mistaken_for_a_track_change(tmp_path, monkeypatch):
    """Regression (found only with real playback): after a seek the old high position made the monitor
    think the queued track had started, advancing the UI to the next song ~5s early."""
    import subprocess
    ffmpeg = downloader.get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    tone = tmp_path / "tone.mp3"
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", str(tone)],
                   check=True, capture_output=True)
    monkeypatch.setattr("tunebox.core.player.add_history", lambda t: None)
    track = tr(1, filePath=str(tone))
    player.queue, player.queue_index = [track, tr(2)], 0
    player._last_pos_ms = 250_000                          # baseline left over from earlier playback
    player._armed = (player._gen, 1, player.queue[1])
    assert player._load_and_play(track, 2.0, record_history=False)
    assert player._last_pos_ms == 0 and player._armed is None
    assert player.queue_index == 0 and player.current_track["videoId"] == "v1"
    player.stop()


@pytest.mark.asyncio
async def test_table_filled_while_hidden_shows_its_rows_when_revealed():
    """Regression (found with a live artist page): items set while display=False were never rendered
    because the later resize saw unchanged widths and skipped the rebuild.
    Uses a bare TrackTable in a minimal app so nothing else (the real app's refresh loop) touches it."""
    from textual.app import App
    from textual.theme import Theme
    from tunebox.ui.theme import TEXTUAL_PALETTES

    class Bare(App):
        def compose(self):
            yield TrackTable(id="t")

        def on_mount(self):
            self.register_theme(Theme(name="bare", foreground="#ffffff", dark=True, **TEXTUAL_PALETTES["metro_dark"]))
            self.theme = "bare"

    app = Bare()
    async with app.run_test(size=(100, 30)) as pilot:
        table = app.query_one("#t", TrackTable)
        await pilot.pause(0.2)
        table.sync([], set(), None, False)                  # visible + empty first (what the real flow does)
        table.display = False
        await pilot.pause(0.2)
        table.sync([tr(1), tr(2), tr(3)], set(), None, False)
        assert table.row_count == 0                         # hidden: nothing laid out yet
        table.display = True
        assert await wait_for(pilot, lambda: table.row_count == 3), (
            f"rows={table.row_count} width={table.size.width} dirty={table._dirty} items={len(table.items)}")


# ------------------------------------------------------------------ artist page resilience

def _fake_yt(get_artist):
    return SimpleNamespace(get_artist=get_artist)


def test_artist_page_retries_once_when_youtube_returns_unparseable_layout(monkeypatch):
    """YouTube intermittently answers with a layout ytmusicapi can't parse (KeyError: musicImmersiveHeaderRenderer)."""
    calls = []

    def flaky(bid):
        calls.append(bid)
        if len(calls) == 1:
            raise KeyError("musicImmersiveHeaderRenderer")
        return {"name": "AJR", "songs": {"results": [{"videoId": "a1", "title": "Finale", "artists": []}]}}

    monkeypatch.setattr(yt_client, "_yt", _fake_yt(flaky))
    data = yt_client.get_artist("UC1", "AJR")
    assert len(calls) == 2 and data["title"] == "AJR" and "limited" not in data
    assert [t["videoId"] for t in data["tracks"]] == ["a1"]


def test_artist_page_falls_back_to_search_when_it_keeps_failing(monkeypatch):
    def broken(bid):
        raise KeyError("musicImmersiveHeaderRenderer")

    def fake_search(query, filter_type=None, limit=25):
        if filter_type == "songs":
            return [tr(1, artist="AJR"), tr(2, artist="Some Cover Band"), tr(3, artist="AJR, Weezer")]
        return [{"type": "album", "browseId": "MPREb_x", "title": "Neotheater", "artist": "AJR"},
                {"type": "album", "browseId": "MPREb_y", "title": "Other", "artist": "Someone Else"}]

    monkeypatch.setattr(yt_client, "_yt", _fake_yt(broken))
    def real_like_search(query, filter_type=None, limit=25):
        yt_client.last_error = ""                  # a successful search clears last_error, like the real one
        return fake_search(query, filter_type, limit)

    monkeypatch.setattr(yt_client, "search", real_like_search)
    data = yt_client.get_artist("UC1", "AJR")
    assert data["limited"] is True and data["title"] == "AJR"
    assert [t["videoId"] for t in data["tracks"]] == ["v1", "v3"]          # only this artist's songs
    assert [a["title"] for a in data["albums"]] == ["Neotheater"]
    assert "KeyError" in yt_client.last_error                               # the real reason is kept for diagnostics


def test_artist_fallback_without_a_name_returns_an_empty_page_not_a_crash(monkeypatch):
    monkeypatch.setattr(yt_client, "_yt", _fake_yt(lambda bid: (_ for _ in ()).throw(KeyError("x"))))
    data = yt_client.get_artist("UC1")
    assert data["tracks"] == [] and data["albums"] == [] and data["limited"] is True


@pytest.mark.asyncio
async def test_detail_page_explains_when_it_is_showing_search_results(monkeypatch):
    monkeypatch.setattr(yt_client, "get_artist", lambda bid, name="": {
        "title": name, "tracks": [tr(1)], "albums": [], "limited": True})
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(pilot, app)
        app.open_detail("artist", {"browseId": "UCx", "title": "AJR"})
        sub = app.query_one("#d-sub")
        assert await wait_for(pilot, lambda: "unavailable" in str(sub.render()))
        assert "Showing search results" in str(sub.render())
        assert await wait_for(pilot, lambda: app.query_one("#t-detail", TrackTable).row_count == 1)
