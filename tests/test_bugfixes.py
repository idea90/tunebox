"""Regression tests for the bug hunt (2026-10). Each test reproduces a bug that was confirmed in the old code."""
import json
import os
import threading
import time

import pygame
import pytest
from textual.widgets import TabbedContent

from tunebox import config as cfg
from tunebox.core import downloader
from tunebox.core.database import add_download, get_downloads, remove_download
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui.app import TuneboxApp
from tunebox.ui.widgets import TrackTable
import tunebox.core.player as pmod


def tr(i, **kw):
    t = {"type": "song", "videoId": f"v{i}", "title": f"T{i}", "artist": "A", "duration_seconds": 100, "duration": "1:40"}
    t.update(kw)
    return t


class FakeMixer:
    """Records what the audio engine is told. queue() holds one file, load() clears it (as measured on real pygame)."""

    def __init__(self):
        self.calls, self.queued, self.loaded, self.pos = [], None, None, 0

    def install(self, mp):
        m = pygame.mixer.music
        mp.setattr(m, "load", lambda path: (self.calls.append(("load", path)), setattr(self, "loaded", path), setattr(self, "queued", None)))
        mp.setattr(m, "play", lambda *a, **k: self.calls.append(("play", k.get("start", a[1] if len(a) > 1 else 0))))
        mp.setattr(m, "queue", lambda path: (self.calls.append(("queue", path)), setattr(self, "queued", path)))
        mp.setattr(m, "stop", lambda: self.calls.append(("stop",)))
        mp.setattr(m, "pause", lambda: self.calls.append(("pause",)))
        mp.setattr(m, "unpause", lambda: self.calls.append(("unpause",)))
        mp.setattr(m, "set_volume", lambda v: None)
        mp.setattr(m, "get_pos", lambda: self.pos)
        mp.setattr(m, "get_busy", lambda: True)
        return self

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def mixer(monkeypatch, tmp_path):
    """Real Player logic, fake audio engine; every song 'downloads' instantly to a real temp file."""
    m = FakeMixer().install(monkeypatch)
    monkeypatch.setattr(pmod, "add_history", lambda t: None)

    def fake_cache(track):
        p = tmp_path / f"{track['videoId']}.mp3"
        p.write_bytes(b"x" * 20000)
        return str(p)

    monkeypatch.setattr(pmod, "cache_track_audio", fake_cache)
    monkeypatch.setattr(pmod, "get_cached_track_path", lambda vid: None)
    cfg.config.set("gapless", True)
    player.queue, player.queue_index, player.current_track = [], -1, None
    player.is_playing = player.is_paused = player.is_loading = False
    player.shuffle, player.repeat_mode, player.autoplay = False, "off", False
    player._armed = player._armed_path = player._current_file = None
    player._bag, player._bag_valid = [], False
    yield m
    player.is_playing = player.is_paused = player.is_loading = False
    player._armed = None
    player.shuffle, player.repeat_mode = False, "off"


def wait_until(cond, timeout=3.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.02)
    return cond()


def armed_id():
    return player._armed[2]["videoId"] if player._armed else None


def start(m, n=4, idx=0):
    """Play a queue and wait until the next song has been handed to the mixer (gapless armed)."""
    player.play(queue=[tr(i) for i in range(n)], index=idx)
    assert wait_until(lambda: player._armed is not None), "next song should be armed"
    return m


# ================================================================== the user's bug: old song keeps playing

def test_old_song_stops_immediately_when_another_is_chosen(mixer, monkeypatch):
    """Reported: switching songs showed 'Loading...' but the previous song kept playing until the download finished."""
    player.play(queue=[tr(1)], index=0)
    assert wait_until(lambda: player.is_playing)
    release = threading.Event()
    real_cache = pmod.cache_track_audio
    monkeypatch.setattr(pmod, "cache_track_audio", lambda t: (release.wait(5), real_cache(t))[1])
    mixer.calls.clear()

    worker = threading.Thread(target=player.play_track, args=(tr(2),))
    worker.start()
    assert wait_until(lambda: player.is_loading)
    assert mixer.names() == ["stop"], f"the old song must be stopped before the download, got {mixer.calls}"
    assert player.current_track["videoId"] == "v2" and player.get_position() == 0.0   # UI shows the new song at 0:00
    release.set()
    worker.join(5)
    assert ("load", mixer.loaded) in mixer.calls and mixer.loaded.endswith("v2.mp3")


def test_a_newer_choice_wins_even_if_an_older_download_finishes_later(mixer, monkeypatch):
    gates = {"v1": threading.Event(), "v2": threading.Event()}
    real_cache = pmod.cache_track_audio
    monkeypatch.setattr(pmod, "cache_track_audio", lambda t: (gates[t["videoId"]].wait(5), real_cache(t))[1])
    a = threading.Thread(target=player.play_track, args=(tr(1),)); a.start()
    assert wait_until(lambda: player.is_loading)
    b = threading.Thread(target=player.play_track, args=(tr(2),)); b.start()
    time.sleep(0.1)
    gates["v1"].set()                       # the OLD download finishes first...
    a.join(5)
    assert not any(c[0] == "load" and c[1].endswith("v1.mp3") for c in mixer.calls), "a superseded song must never start"
    assert player.is_loading, "stay 'loading' so the monitor doesn't treat the silence as end-of-song"
    gates["v2"].set()
    b.join(5)
    assert player.current_track["videoId"] == "v2" and mixer.loaded.endswith("v2.mp3") and not player.is_loading


# ================================================================== gapless vs queue edits

def test_play_next_replaces_the_song_already_handed_to_the_mixer(mixer):
    start(mixer)
    assert armed_id() == "v1"
    player.play_next(tr(9))
    assert wait_until(lambda: armed_id() == "v9")
    assert mixer.queued.endswith("v9.mp3"), "the mixer must now hold the 'play next' song"
    player._on_gapless_advance()
    assert player.current_track["videoId"] == "v9" and player.queue[player.queue_index] is player.current_track


def test_removing_the_armed_song_rearms_and_keeps_queue_and_audio_in_sync(mixer):
    start(mixer)
    player.remove_from_queue(1)                      # remove v1, which the mixer had queued
    assert wait_until(lambda: armed_id() == "v2")
    assert mixer.queued.endswith("v2.mp3")
    player._on_gapless_advance()
    assert player.current_track["videoId"] == "v2" and player.queue[player.queue_index] is player.current_track


def test_clear_queue_unqueues_the_next_song(mixer):
    start(mixer)
    player.clear_queue()
    assert player._armed is None and mixer.queued is None, "nothing may play after clearing"
    assert mixer.loaded.endswith("v0.mp3"), "the current song keeps playing (reloaded in place)"


def test_reorder_rearms_with_the_new_next_song(mixer):
    start(mixer)
    player.move_in_queue(2, -1)                      # v2 now plays next
    assert wait_until(lambda: armed_id() == "v2") and mixer.queued.endswith("v2.mp3")


def test_unrelated_edit_keeps_the_armed_song_without_a_reload(mixer):
    start(mixer)
    loads = mixer.names().count("load")
    player.add_to_queue(tr(9))                       # appended at the end: next song is still v1
    assert armed_id() == "v1" and mixer.names().count("load") == loads, "no needless audio glitch"


def test_unqueue_keeps_the_current_position_and_pause_state(mixer):
    start(mixer)
    player.pause()
    player._paused_pos = 42.0
    mixer.calls.clear()
    player.clear_queue()
    assert ("play", 42.0) in mixer.calls and mixer.calls[-1] == ("pause",)


@pytest.mark.parametrize("change", ["shuffle", "repeat_one"])
def test_mode_changes_rearm_the_next_song(mixer, change):
    start(mixer, n=6)
    if change == "shuffle":
        player.toggle_shuffle()
    else:
        player.cycle_repeat(); player.cycle_repeat()                    # off -> all -> one
        assert player.repeat_mode == "one"
    expected = player._peek_next()[1]
    assert wait_until(lambda: player._armed is not None and player._armed[2] is expected)


# ================================================================== idle add / play next

def test_adding_while_idle_moves_the_queue_to_the_song_that_starts(mixer):
    player.queue, player.queue_index = [tr(0), tr(1), tr(2)], 2       # finished on the last song
    player.add_to_queue(tr(7))
    assert player.queue[player.queue_index] is player.current_track and player.current_track["videoId"] == "v7"
    assert player.next() is False, "nothing after v7: must not replay it"


def test_play_next_while_idle_moves_the_queue_too(mixer):
    player.queue, player.queue_index = [tr(0), tr(1), tr(2)], 2
    player.play_next(tr(8))
    assert player.queue[player.queue_index] is player.current_track and player.current_track["videoId"] == "v8"


# ================================================================== shuffle

def test_shuffle_repeat_all_starts_a_new_cycle(mixer):
    player.shuffle, player.repeat_mode = True, "all"
    player.play(queue=[tr(i) for i in range(4)], index=0)
    played = [player.current_track["videoId"]]
    for _ in range(11):
        assert player.next()
        played.append(player.current_track["videoId"])
    for cycle in (played[0:4], played[4:8]):
        assert sorted(cycle) == ["v0", "v1", "v2", "v3"], f"each cycle plays every song once: {played}"


def test_play_next_wins_over_shuffle_order(mixer):
    player.shuffle = True
    player.play(queue=[tr(i) for i in range(6)], index=0)
    player.play_next(tr(9))
    player.next()
    assert player.current_track["videoId"] == "v9"


def test_upcoming_matches_what_actually_plays(mixer):
    player.shuffle = True
    player.play(queue=[tr(i) for i in range(6)], index=0)
    promised = [t["videoId"] for t in player.upcoming(8)]
    actual = []
    while player.next():
        actual.append(player.current_track["videoId"])
    assert promised == actual


def test_shuffle_survives_session_restore(mixer):
    player.restore([tr(i) for i in range(4)], 1, 30.0)
    player.shuffle = True
    assert player._pick_next_index() is not None, "a restored queue must still shuffle (bag rebuilt lazily)"


# ================================================================== seek while paused

def test_seek_while_paused_stays_paused(mixer):
    player.play(queue=[tr(1)], index=0)
    player.pause()
    mixer.calls.clear()
    assert player.seek(50, relative=False)
    assert player.is_paused and player.get_position() == 50.0
    assert mixer.calls[-1] == ("pause",)


# ================================================================== search race + Up Next (UI)

async def _wait(pilot, cond, timeout=5.0):
    t = 0.0
    while not cond() and t < timeout:
        await pilot.pause(0.05); t += 0.05
    return cond()


@pytest.mark.asyncio
async def test_slow_older_search_cannot_overwrite_newer_results(monkeypatch):
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    def fake_search(q, filter_type=None, limit=25):
        if q == "first":
            time.sleep(0.8)
        return [{"type": "song", "videoId": f"{q}-1", "title": q, "artist": "A"}]
    monkeypatch.setattr(yt_client, "search", fake_search)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        assert await _wait(pilot, lambda: app.tab_events >= 1)
        await pilot.press("2")
        assert await _wait(pilot, lambda: getattr(app.focused, "id", None) == "search-input")
        await pilot.press(*"first", "enter")
        inp = app.query_one("#search-input")
        inp.value = ""
        await pilot.press(*"second", "enter")
        assert await _wait(pilot, lambda: app.search_query == "second"), (
            f"query={app.search_query!r} seq={app._search_seq} input={inp.value!r} focused={app.focused!r} tab={app.active_tab}")
        await pilot.pause(1.2)                                         # let the slow 'first' search finish
        assert app.search_query == "second" and app.search_items[0]["videoId"] == "second-1"


@pytest.mark.asyncio
async def test_up_next_shows_the_real_shuffle_order_and_clicks_the_right_song(mixer, monkeypatch):
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    jumped = []
    monkeypatch.setattr(player, "jump_to", lambda i: jumped.append(i))
    player.shuffle = True
    player.play(queue=[tr(i) for i in range(6)], index=0)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        table = app.query_one("#t-upnext", TrackTable)
        assert await _wait(pilot, lambda: table.row_count == 5)
        # after the first refresh sizes the cover art, Up Next must still show several songs at 40 lines
        assert await _wait(pilot, lambda: table.size.height >= 5), f"Up Next squeezed to {table.size.height} rows"
        assert [t["videoId"] for t in table.items] == [t["videoId"] for t in player.upcoming(8)]
        assert table.items[0] is player._peek_next()[1]
        await pilot.pause(0.3)
        await pilot.click("#t-upnext", offset=(8, 2))                  # second row
        assert await _wait(pilot, lambda: bool(jumped))
        assert player.queue[jumped[0]] is table.items[1]


@pytest.mark.asyncio
async def test_unexpected_repeat_mode_does_not_crash_the_app(monkeypatch):
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    player.repeat_mode = "ONE"
    try:
        app = TuneboxApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(1.0)
        assert app._exception is None
    finally:
        player.repeat_mode = "off"


# ================================================================== cache limit / download format / names

def test_cache_evicts_least_recently_used_beyond_the_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(downloader, "_cache_limit_bytes", lambda: 3 * 1000)
    now = time.time()
    for i in range(6):                                                 # f0 oldest ... f5 newest, 1000 bytes each
        p = tmp_path / f"f{i}.mp3"
        p.write_bytes(b"x" * 1000)
        os.utime(p, (now - 100 + i, now - 100 + i))
    removed = downloader.enforce_cache_limit(keep=2)
    assert removed == 3 and sorted(p.name for p in tmp_path.iterdir()) == ["f3.mp3", "f4.mp3", "f5.mp3"]


def test_cache_hit_counts_as_recent_use(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "CACHE_DIR", tmp_path)
    p = tmp_path / "abc.mp3"
    p.write_bytes(b"x" * 20000)
    os.utime(p, (1, 1))
    assert downloader.get_cached_track_path("abc") == str(p)
    assert p.stat().st_mtime > time.time() - 60


def test_cache_disabled_keeps_only_the_newest_songs(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "CACHE_DIR", tmp_path)
    monkeypatch.setitem(cfg.config._data, "cache_enabled", False)
    for i in range(5):
        p = tmp_path / f"s{i}.mp3"; p.write_bytes(b"x" * 10); os.utime(p, (i + 1, i + 1))
    downloader.enforce_cache_limit(keep=3)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s2.mp3", "s3.mp3", "s4.mp3"]


@pytest.mark.parametrize("value,expected", [("flac", "flac"), ("M4A", "m4a"), ("wav", "mp3"), (None, "mp3")])
def test_download_format_comes_from_config(monkeypatch, value, expected):
    monkeypatch.setitem(cfg.config._data, "download_format", value)
    assert downloader.download_format() == expected


def test_different_songs_with_the_same_name_get_different_files(tmp_path):
    existing = tmp_path / "Coldplay - Yellow.mp3"
    existing.write_bytes(b"studio")
    add_download({"videoId": "studio", "title": "Yellow", "artist": "Coldplay"}, str(existing), 6, "mp3")
    assert downloader._unique_stem(tmp_path, "Coldplay - Yellow", "studio") == "Coldplay - Yellow"     # same song again
    assert downloader._unique_stem(tmp_path, "Coldplay - Yellow", "live") == "Coldplay - Yellow [live]"
    assert downloader._unique_stem(tmp_path, "Coldplay - Clocks", "x") == "Coldplay - Clocks"


def test_deleting_one_entry_never_deletes_a_file_another_entry_uses(tmp_path):
    shared = tmp_path / "Old - Collision.mp3"
    shared.write_bytes(b"x")
    add_download({"videoId": "a1", "title": "Collision", "artist": "Old"}, str(shared), 1, "mp3")
    add_download({"videoId": "a2", "title": "Collision", "artist": "Old"}, str(shared), 1, "mp3")
    remove_download("a1")
    assert shared.exists() and any(d["videoId"] == "a2" for d in get_downloads())


# ================================================================== config

@pytest.fixture
def fresh_config():
    """A brand-new Config loaded from disk, with the real file restored afterwards."""
    original_file = cfg.CONFIG_FILE.read_text(encoding="utf-8") if cfg.CONFIG_FILE.exists() else None
    original_instance = cfg.Config._instance

    def make():
        cfg.Config._instance = None
        return cfg.Config()

    yield make
    cfg.Config._instance = original_instance
    for extra in (cfg.CONFIG_FILE.with_suffix(".json.corrupt"), cfg.CONFIG_FILE.with_suffix(".json.tmp")):
        extra.unlink(missing_ok=True)
    if original_file is not None:
        cfg.CONFIG_FILE.write_text(original_file, encoding="utf-8")


def test_config_save_is_atomic(fresh_config):
    c = fresh_config()
    c.set("volume", 33)
    assert json.loads(cfg.CONFIG_FILE.read_text(encoding="utf-8"))["volume"] == 33
    assert not cfg.CONFIG_FILE.with_suffix(".json.tmp").exists()


def test_truncated_config_is_kept_and_reported_not_silently_lost(fresh_config):
    cfg.CONFIG_FILE.write_text('{"volume": 70', encoding="utf-8")
    c = fresh_config()
    assert c.get("volume") == 80 and "could not be read" in c.load_error
    assert cfg.CONFIG_FILE.with_suffix(".json.corrupt").read_text(encoding="utf-8") == '{"volume": 70'


def test_bad_values_in_config_are_sanitized(fresh_config):
    cfg.CONFIG_FILE.write_text(json.dumps({"volume": "loud", "repeat_mode": "ONE", "shuffle": "yes",
                                           "art_style": "neon", "search_history": "x"}), encoding="utf-8")
    c = fresh_config()
    assert c.get("volume") == 80 and c.get("repeat_mode") == "one" and c.get("shuffle") is False
    assert c.get("art_style") == "auto" and c.get("search_history") == [] and c.load_error == ""
