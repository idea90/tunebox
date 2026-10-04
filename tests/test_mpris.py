"""MPRIS: the data the desktop sees, and the real D-Bus service against a private session bus."""
import asyncio
import os
import shutil
import subprocess
import time

import pytest

from tunebox.core import mpris
from tunebox.core.player import Player, player

pytest.importorskip("dbus_next")

NOT_ON_LINUX = pytest.mark.skipif(not os.sys.platform.startswith("linux") or not shutil.which("dbus-daemon"),
                                  reason="needs Linux and dbus-daemon")


def song(i=1, **extra):
    t = {"videoId": f"id-{i}_x", "title": f"Song {i}", "artist": "Daft Punk",
         "artists": [{"name": "Daft Punk", "id": None}], "album": {"name": "RAM", "id": None},
         "duration_seconds": 240, "type": "song"}
    t.update(extra)
    return t


# ------------------------------------------------------------------ plain data

def test_track_id_is_a_valid_object_path_and_unique():
    tid = mpris.track_id({"videoId": "dQw4w9-XcQ_"})
    assert tid == "/org/tunebox/track/dQw4w9_2dXcQ_"
    assert all(c.isalnum() or c in "/_" for c in tid)
    assert mpris.track_id({"videoId": "a-b"}) != mpris.track_id({"videoId": "a_b"})
    assert mpris.track_id(None) == mpris.NO_TRACK == mpris.track_id({"title": "no id"})


@pytest.mark.parametrize("playing,paused,expected", [(True, False, "Playing"), (True, True, "Paused"),
                                                     (False, True, "Paused"), (False, False, "Stopped")])
def test_playback_status(playing, paused, expected):
    assert mpris.playback_status(playing, paused) == expected


def test_loop_status_round_trips():
    assert {m: mpris.LOOP_STATUS[m] for m in ("off", "one", "all")} == {"off": "None", "one": "Track", "all": "Playlist"}
    assert [mpris.REPEAT_FROM_LOOP[v] for v in ("None", "Track", "Playlist")] == ["off", "one", "all"]


def test_metadata_has_what_desktops_show():
    meta = mpris.metadata_fields(song(1), 240_000_000, "file:///cover.jpg")
    assert meta["xesam:title"] == ("s", "Song 1")
    assert meta["xesam:artist"] == ("as", ["Daft Punk"])
    assert meta["xesam:album"] == ("s", "RAM")
    assert meta["mpris:length"] == ("x", 240_000_000)
    assert meta["mpris:artUrl"] == ("s", "file:///cover.jpg")
    assert meta["xesam:url"] == ("s", "https://music.youtube.com/watch?v=id-1_x")
    assert meta["mpris:trackid"][0] == "o"


def test_metadata_leaves_out_what_is_unknown_and_handles_nothing_playing():
    meta = mpris.metadata_fields({"videoId": "v", "title": "T", "artist": "Solo"}, 0, None)
    assert set(meta) == {"mpris:trackid", "xesam:title", "xesam:artist", "xesam:url"}
    assert meta["xesam:artist"] == ("as", ["Solo"])
    assert mpris.metadata_fields(None) == {"mpris:trackid": ("o", mpris.NO_TRACK)}


def test_snapshot_reflects_the_player(monkeypatch):
    monkeypatch.setattr(player, "current_track", song(2), raising=False)
    monkeypatch.setattr(player, "queue", [song(1), song(2)], raising=False)
    monkeypatch.setattr(player, "is_playing", True, raising=False)
    monkeypatch.setattr(player, "is_paused", False, raising=False)
    monkeypatch.setattr(player, "track_duration", 240.0, raising=False)
    monkeypatch.setattr(player, "volume", 35, raising=False)
    monkeypatch.setattr(player, "repeat_mode", "one", raising=False)
    monkeypatch.setattr(player, "shuffle", True, raising=False)
    snap = mpris.snapshot_from_player(player)
    assert snap["status"] == "Playing" and snap["loop"] == "Track" and snap["shuffle"] is True
    assert snap["volume"] == pytest.approx(0.35) and snap["length_us"] == 240_000_000
    assert snap["can_play"] and snap["can_seek"] and snap["can_go_next"] and snap["can_go_previous"]
    assert snap["track"]["videoId"] == "id-2_x"

    monkeypatch.setattr(player, "current_track", None, raising=False)
    monkeypatch.setattr(player, "queue", [], raising=False)
    monkeypatch.setattr(player, "is_playing", False, raising=False)
    snap = mpris.snapshot_from_player(player)
    assert snap["status"] == "Stopped" and snap["track"] is None
    assert not snap["can_play"] and not snap["can_seek"] and not snap["can_go_next"]


# ------------------------------------------------------------------ cover files for the desktop

def test_art_url_writes_a_square_jpeg_and_prunes_old_ones(monkeypatch, tmp_path):
    from PIL import Image
    from tunebox.core import albumart
    monkeypatch.setattr(mpris, "MPRIS_ART_DIR", tmp_path)
    monkeypatch.setattr(mpris, "ART_FILES_MAX", 3)
    fetched = []
    monkeypatch.setattr(albumart, "fetch_image", lambda t: fetched.append(t["videoId"]) or Image.new("RGB", (640, 360), (9, 99, 199)))

    urls = []
    for i in range(5):
        urls.append(mpris.art_url_for(song(i)))
        os.utime(tmp_path / f"id-{i}_x.jpg", (1000 + i, 1000 + i))
    assert urls[0].startswith("file://") and urls[0].endswith("id-0_x.jpg")
    img = Image.open(tmp_path / "id-4_x.jpg")
    assert img.format == "JPEG" and img.width == img.height              # centred square, never the 16:9 frame
    assert len(list(tmp_path.glob("*.jpg"))) == 3
    assert mpris.art_url_for(song(4)) == urls[4] and fetched.count("id-4_x") == 1       # reused, not re-fetched

    monkeypatch.setattr(albumart, "fetch_image", lambda t: None)
    assert mpris.art_url_for(song(99)) is None                           # no cover: no artUrl
    assert mpris.art_url_for({"title": "no id"}) is None


# ------------------------------------------------------------------ player additions MPRIS needs

def test_set_repeat_and_set_shuffle(monkeypatch):
    p = Player()
    monkeypatch.setattr(p, "_rearm", lambda: None)
    p.repeat_mode, p.shuffle = "off", False
    assert p.set_repeat("all") == "all" and p.repeat_mode == "all"
    assert p.set_repeat("bogus") == "all"                                # unknown mode: ignored
    assert p.cycle_repeat() == "one" and p.cycle_repeat() == "off"       # cycling still works through set_repeat
    assert p.set_shuffle(True) is True and p.shuffle is True
    assert p.set_shuffle(True) is True and p.shuffle is True             # already on: no toggle back
    assert p.set_shuffle(False) is False
    p.repeat_mode, p.shuffle = "off", False


# ------------------------------------------------------------------ the real service on a private bus

@pytest.fixture
def session_bus(monkeypatch):
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                              stdout=subprocess.PIPE, text=True)
    address = daemon.stdout.readline().strip()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    yield address
    mpris.stop()
    daemon.terminate()
    daemon.wait(timeout=5)


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


@NOT_ON_LINUX
@pytest.mark.real_mpris
def test_service_end_to_end_over_dbus(session_bus):
    from dbus_next import BusType
    from dbus_next.aio import MessageBus

    calls, state = [], {"pos": 12.5}
    ready = []
    svc = mpris.MprisService()
    assert svc.start(lambda action, arg=None: calls.append((action, arg)), lambda: state["pos"], ready.append)
    assert wait_until(lambda: ready == [True])
    assert svc.bus_name == mpris.BUS_NAME

    svc.update({**mpris.snapshot_from_player(_FakePlayer())})

    async def client():
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        intro = await bus.introspect(mpris.BUS_NAME, mpris.OBJECT_PATH)
        obj = bus.get_proxy_object(mpris.BUS_NAME, mpris.OBJECT_PATH, intro)
        root = obj.get_interface("org.mpris.MediaPlayer2")
        pl = obj.get_interface("org.mpris.MediaPlayer2.Player")
        props = obj.get_interface("org.freedesktop.DBus.Properties")
        seen = []
        props.on_properties_changed(lambda iface, changed, inv: seen.append((iface, changed)))
        seeked = []
        pl.on_seeked(seeked.append)

        out = {}
        out["identity"] = await root.get_identity()
        out["can_quit"] = await root.get_can_quit()
        out["status"] = await pl.get_playback_status()
        out["loop"] = await pl.get_loop_status()
        out["shuffle"] = await pl.get_shuffle()
        out["volume"] = await pl.get_volume()
        out["position"] = await pl.get_position()
        out["meta"] = await pl.get_metadata()
        out["can_control"] = await pl.get_can_control()

        await pl.call_play_pause()
        await pl.call_next()
        await pl.call_previous()
        await pl.call_pause()
        await pl.call_play()
        await pl.call_stop()
        await pl.call_seek(2_500_000)
        await pl.call_set_position(out["meta"]["mpris:trackid"].value, 30_000_000)
        await pl.call_set_position("/org/tunebox/track/other", 99_000_000)    # another song: must be ignored
        await pl.set_volume(0.25)
        await pl.set_loop_status("Playlist")
        await pl.set_shuffle(True)
        await root.call_quit()

        state["pos"] = 100.0                                   # the song jumped (seek bar, restart...): Seeked
        svc.update({**mpris.snapshot_from_player(_FakePlayer(playing=False, paused=True, volume=60))})
        await asyncio.sleep(0.4)
        out["changes"], out["seeked"] = seen, seeked
        bus.disconnect()
        return out

    out = asyncio.run(client())
    assert out["identity"] == "Tunebox" and out["can_quit"] is True and out["can_control"] is True
    assert out["status"] == "Playing" and out["loop"] == "Track" and out["shuffle"] is False
    assert out["volume"] == pytest.approx(0.8) and out["position"] == 12_500_000
    meta = out["meta"]
    assert meta["xesam:title"].value == "Song 1" and meta["xesam:artist"].value == ["Daft Punk"]
    assert meta["mpris:length"].value == 240_000_000

    assert calls == [("play_pause", None), ("next", None), ("prev", None), ("pause", None), ("play", None),
                     ("stop", None), ("seek", 2.5), ("set_position", 30.0), ("volume", 0.25), ("loop", "all"),
                     ("shuffle", True), ("quit", None)]

    changed = {}
    for iface, c in out["changes"]:
        if iface == "org.mpris.MediaPlayer2.Player":
            changed.update({k: v.value for k, v in c.items()})
    assert out["seeked"] == [100_000_000]
    assert changed["PlaybackStatus"] == "Paused"
    assert changed["Volume"] == pytest.approx(0.6)


@NOT_ON_LINUX
@pytest.mark.real_mpris
def test_a_second_instance_gets_its_own_name_and_no_bus_means_no_service(session_bus, monkeypatch):
    first, second = mpris.MprisService(), mpris.MprisService()
    ready1, ready2 = [], []
    assert first.start(lambda a, b=None: None, lambda: 0.0, ready1.append)
    assert wait_until(lambda: ready1 == [True])
    assert second.start(lambda a, b=None: None, lambda: 0.0, ready2.append)
    assert wait_until(lambda: ready2 == [True])
    assert first.bus_name == mpris.BUS_NAME and second.bus_name == f"{mpris.BUS_NAME}.instance{os.getpid()}"
    second.stop()
    first.stop()

    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS")
    monkeypatch.setattr(mpris, "_bus_available", lambda: False)
    assert mpris.MprisService().start(lambda a, b=None: None, lambda: 0.0) is False


class _FakePlayer:
    def __init__(self, playing=True, paused=False, volume=80):
        self.current_track, self.queue = song(1), [song(1), song(2)]
        self.is_playing, self.is_paused, self.track_duration = playing, paused, 240.0
        self.volume, self.repeat_mode, self.shuffle = volume, "one", False


# ------------------------------------------------------------------ the app side

import threading  # noqa: E402

from tunebox.core.ytmusic import yt_client  # noqa: E402
from tunebox.ui.app import TuneboxApp  # noqa: E402


@pytest.fixture
def quiet_app(monkeypatch):
    import pygame
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)      # keep the monitor thread from "ending" fake songs
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    monkeypatch.setattr(player, "_trigger_prefetch", lambda: None)
    log = []
    monkeypatch.setattr(player, "toggle_pause", lambda: log.append(("toggle", None)))
    monkeypatch.setattr(player, "next", lambda auto=False: log.append(("next", None)) or True)
    monkeypatch.setattr(player, "previous", lambda: log.append(("prev", None)) or True)
    monkeypatch.setattr(player, "seek", lambda s, relative=True: log.append(("seek", (s, relative))) or True)
    monkeypatch.setattr(player, "stop", lambda: log.append(("stop", None)))
    monkeypatch.setattr(player, "set_volume", lambda v: log.append(("volume", v)))
    monkeypatch.setattr(player, "set_repeat", lambda m: log.append(("repeat", m)))
    monkeypatch.setattr(player, "set_shuffle", lambda on: log.append(("shuffle", on)))
    return log


async def settle_for(pilot, cond, timeout=5.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


@pytest.mark.asyncio
async def test_desktop_requests_drive_the_player(quiet_app, monkeypatch):
    log = quiet_app
    monkeypatch.setattr(player, "current_track", song(1), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)

        monkeypatch.setattr(player, "is_playing", True, raising=False)
        monkeypatch.setattr(player, "is_paused", False, raising=False)
        app._remote_action("play")                                  # already playing: nothing to do
        await pilot.pause(0.2)
        assert log == []
        app._remote_action("pause")
        assert await settle_for(pilot, lambda: log == [("toggle", None)])

        monkeypatch.setattr(player, "is_paused", True, raising=False)
        log.clear()
        app._remote_action("pause")                                 # already paused
        app._remote_action("play")
        assert await settle_for(pilot, lambda: log == [("toggle", None)])

        log.clear()
        monkeypatch.setattr(player, "current_track", None, raising=False)
        monkeypatch.setattr(player, "is_playing", False, raising=False)
        monkeypatch.setattr(player, "is_paused", False, raising=False)
        app._remote_action("play")                                  # nothing loaded: nothing to start
        await pilot.pause(0.2)
        assert log == []

        for action, arg in (("play_pause", None), ("next", None), ("prev", None), ("stop", None),
                            ("seek", -5.5), ("set_position", 42.0), ("volume", 0.255), ("loop", "all"),
                            ("shuffle", True)):
            app._remote_action(action, arg)
        expected = [("toggle", None), ("next", None), ("prev", None), ("stop", None), ("seek", (-5.5, True)),
                    ("seek", (42.0, False)), ("volume", 26), ("repeat", "all"), ("shuffle", True)]
        assert await settle_for(pilot, lambda: sorted(map(str, log)) == sorted(map(str, expected)))


@pytest.mark.asyncio
async def test_quit_request_from_the_desktop_closes_the_app(quiet_app):
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        app._remote_action("quit")
        assert await settle_for(pilot, lambda: ("stop", None) in quiet_app)


@pytest.mark.asyncio
async def test_the_app_publishes_player_state_and_wires_the_handler(quiet_app, monkeypatch):
    started, published = {}, []

    def fake_start(handler, position_fn, on_ready=None):
        started.update(handler=handler, position_fn=position_fn, on_ready=on_ready)
        return True

    monkeypatch.setattr(mpris, "start", fake_start)
    monkeypatch.setattr(mpris, "update", published.append)
    monkeypatch.setattr(player, "current_track", song(7), raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        assert await settle_for(pilot, lambda: bool(started) and any(s["track"] for s in published))
        assert started["position_fn"] == player.get_position
        assert published[-1]["track"]["videoId"] == "id-7_x" and published[-1]["status"] == "Stopped"

        threading.Thread(target=started["handler"], args=("next", None)).start()    # as the D-Bus thread does
        assert await settle_for(pilot, lambda: ("next", None) in quiet_app)

        threading.Thread(target=started["on_ready"], args=(True,)).start()
        assert await settle_for(pilot, lambda: any("MPRIS" in str(n.message) for n in app._notifications))
