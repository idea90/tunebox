"""Audio backends (pygame / mpv) and the Termux helpers."""
import os
import shutil
import signal
import time
import wave

import numpy as np
import pytest

from tunebox import config as cfg
from tunebox import termux
from tunebox.core import audio, share
from tunebox.core.audio import MpvBackend, PygameBackend, create_audio_backend
from tunebox.core.player import Player
from tunebox.ui import inline

HAS_MPV = bool(shutil.which("mpv"))
needs_mpv = pytest.mark.skipif(not HAS_MPV, reason="mpv is not installed")


def wait_until(cond, timeout=6.0, step=0.03):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    d = tmp_path_factory.mktemp("tones")

    def make(name, secs, freq):
        sr = 22050
        t = np.arange(int(sr * secs)) / sr
        path = str(d / name)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.sin(2 * np.pi * freq * t) * 20000).astype("<i2").tobytes())
        return path

    return make("a.wav", 3.0, 440), make("b.wav", 2.0, 660)


@pytest.fixture
def mpv():
    backend = MpvBackend(extra_args=["--ao=null"])          # real mpv, but silent
    assert backend.ok, backend.problem
    yield backend
    backend.close()


# ------------------------------------------------------------------ choosing a backend

class Stub:
    def __init__(self, name, ok, problem=""):
        self.name, self.ok, self.problem = name, ok, problem


@pytest.fixture
def pick(monkeypatch):
    """create_audio_backend() with both engines replaced by stubs; returns a function to set their availability."""
    def setup(pygame_ok=True, mpv_ok=True, termux_on=False, setting="auto", env=None):
        monkeypatch.setattr(audio, "PygameBackend", lambda: Stub("pygame", pygame_ok, "" if pygame_ok else "no sound card"))
        monkeypatch.setattr(audio, "MpvBackend", lambda path=None, args=None: Stub("mpv", mpv_ok, "" if mpv_ok else "mpv is not installed. In Termux: pkg install mpv"))
        monkeypatch.setattr(termux, "is_termux", lambda: termux_on)
        monkeypatch.setitem(cfg.config._data, "audio_backend", setting)
        monkeypatch.delenv("TUNEBOX_AUDIO", raising=False)
        if env:
            monkeypatch.setenv("TUNEBOX_AUDIO", env)
        return create_audio_backend()
    return setup


def test_desktop_uses_pygame_and_termux_uses_mpv(pick):
    assert pick().name == "pygame"
    assert pick(termux_on=True).name == "mpv"


def test_auto_falls_back_to_the_other_engine(pick):
    assert pick(pygame_ok=False).name == "mpv"                          # a desktop without a usable pygame
    assert pick(termux_on=True, mpv_ok=False).name == "pygame"


def test_an_explicit_choice_is_respected_and_reports_its_problem(pick):
    backend = pick(setting="mpv", mpv_ok=False)                         # asked for mpv, no silent switch to pygame
    assert backend.name == "mpv" and not backend.ok and "pkg install mpv" in backend.problem
    assert pick(setting="pygame", pygame_ok=False).name == "pygame"
    assert pick(setting="pygame", env="mpv").name == "mpv"              # the environment variable wins
    assert pick(setting="nonsense").name == "pygame"                    # garbage means auto


def test_when_nothing_works_the_preferred_backends_problem_is_reported(pick):
    backend = pick(pygame_ok=False, mpv_ok=False)
    assert not backend.ok and backend.problem == "no sound card"
    backend = pick(termux_on=True, pygame_ok=False, mpv_ok=False)
    assert not backend.ok and "pkg install mpv" in backend.problem


def test_mpv_missing_gives_the_install_hint(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    backend = MpvBackend()
    assert not backend.ok and "pkg install mpv" in backend.problem


def test_the_pygame_backend_delegates_at_call_time(monkeypatch):
    """The Player's tests (and anything else) patch pygame.mixer.music; the backend must see those patches."""
    import pygame
    seen = []
    monkeypatch.setattr(pygame.mixer.music, "get_pos", lambda: 1234)
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(pygame.mixer.music, "pause", lambda: seen.append("pause"))
    backend = PygameBackend()
    assert backend.ok and backend.pos_ms() == 1234 and backend.busy() is True
    backend.pause()
    assert seen == ["pause"]


# ------------------------------------------------------------------ the mpv backend against real mpv

@needs_mpv
def test_mpv_plays_pauses_resumes_and_stops(mpv, tones):
    a, _ = tones
    assert not mpv.busy() and mpv.pos_ms() == -1                        # nothing loaded yet
    mpv.load(a)
    assert mpv.busy()
    assert wait_until(lambda: mpv.pos_ms() > 400), "position never advanced"
    mpv.pause()
    frozen = mpv.pos_ms()
    time.sleep(0.4)
    assert abs(mpv.pos_ms() - frozen) < 80                              # paused: the position holds still
    mpv.unpause()
    assert wait_until(lambda: mpv.pos_ms() > frozen + 300)
    mpv.stop()
    assert wait_until(lambda: not mpv.busy()) and mpv.pos_ms() == -1


@needs_mpv
def test_mpv_start_offset_is_not_counted_in_the_position(mpv, tones):
    """pos_ms() means 'time since load() began', like pygame's get_pos(): the Player adds its own seek offset."""
    a, _ = tones
    mpv.load(a, start=2.0)
    assert wait_until(lambda: mpv.pos_ms() >= 0)
    time.sleep(0.4)
    assert 200 < mpv.pos_ms() < 1000                                    # about 0.4 s in, not 2.4 s
    mpv.load(a, start=1.0, paused=True)                                  # a paused load stays silent and still
    assert mpv.busy()
    time.sleep(0.4)
    assert mpv.pos_ms() < 200
    mpv.unpause()
    assert wait_until(lambda: mpv.pos_ms() > 300)


@needs_mpv
def test_mpv_gapless_handover_resets_the_position_and_the_end_is_noticed(mpv, tones):
    a, b = tones
    mpv.load(a)
    mpv.queue(b)
    assert wait_until(lambda: mpv.pos_ms() > 2000), "first song never got going"
    last = mpv.pos_ms()
    assert wait_until(lambda: mpv.pos_ms() < last - 1000, timeout=4.0), "no handover to the queued song"
    assert mpv.busy()                                                   # still playing: the second song
    assert wait_until(lambda: not mpv.busy(), timeout=5.0)              # and then the queue ends


@needs_mpv
def test_mpv_queue_is_ignored_while_idle_and_load_drops_anything_queued(mpv, tones):
    a, b = tones
    mpv.queue(b)                                                        # idle: must NOT start playing
    time.sleep(0.4)
    assert not mpv.busy()
    mpv.load(a)
    mpv.queue(b)
    mpv.load(a, start=0.0)                                              # reloading replaces the playlist, queue included
    assert wait_until(lambda: mpv.pos_ms() > 2200, timeout=5.0)
    assert wait_until(lambda: not mpv.busy(), timeout=2.5)              # ends after A alone: B was dropped


@needs_mpv
def test_mpv_reports_a_bad_file_and_keeps_working(mpv, tones, tmp_path):
    a, _ = tones
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"this is not audio")
    with pytest.raises(RuntimeError):
        mpv.load(str(junk))
    with pytest.raises(RuntimeError):
        mpv.load("/no/such/file.mp3")
    mpv.load(a)                                                         # still usable afterwards
    assert wait_until(lambda: mpv.pos_ms() > 200)


@needs_mpv
def test_mpv_volume_reaches_mpv_and_a_killed_mpv_is_restarted(mpv, tones):
    a, _ = tones
    mpv.set_volume(0.35)
    assert abs(float(mpv._command("get_property", "volume")) - 35.0) < 0.5
    mpv.load(a)
    os.kill(mpv._proc.pid, signal.SIGKILL)                              # Android may do this to a background app
    assert wait_until(lambda: not mpv.busy(), timeout=3.0)
    mpv.load(a)                                                         # the next load starts mpv again
    assert wait_until(lambda: mpv.pos_ms() > 200)
    assert abs(float(mpv._command("get_property", "volume")) - 35.0) < 0.5      # and keeps the volume


@needs_mpv
def test_mpv_close_stops_the_process_and_removes_its_socket_dir(tones):
    backend = MpvBackend(extra_args=["--ao=null"])
    proc, tmp = backend._proc, backend._tmp
    assert proc.poll() is None and os.path.isdir(tmp)
    backend.close()
    assert proc.poll() is not None and not os.path.exists(tmp)
    backend.close()                                                     # closing twice is harmless


# ------------------------------------------------------------------ the whole Player on mpv

@needs_mpv
def test_player_plays_pauses_seeks_and_moves_on_through_mpv(monkeypatch, tones):
    """The real Player, the real queue and gapless logic, real mpv (silent): nothing faked but the history writes."""
    a, b = tones
    monkeypatch.setenv("TUNEBOX_AUDIO", "mpv")
    monkeypatch.setitem(cfg.config._data, "mpv_args", ["--ao=null"])
    monkeypatch.setattr("tunebox.core.player.add_history", lambda t: None)
    p = object.__new__(Player)
    p.init_player()
    try:
        assert p.audio.name == "mpv" and p.audio_ok
        p.autoplay, p.repeat_mode, p.shuffle = False, "off", False       # not whatever other tests left in the shared config
        changed = []
        p.on_track_change = changed.append
        tracks = [{"videoId": "mpvA", "title": "A", "filePath": a, "duration_seconds": 3},
                  {"videoId": "mpvB", "title": "B", "filePath": b, "duration_seconds": 2}]
        p.play(queue=tracks, index=0)
        assert p.is_playing and p.current_track["videoId"] == "mpvA"
        assert wait_until(lambda: p.get_position() > 0.4), "position never advanced"

        p.pause()
        held = p.get_position()
        time.sleep(0.4)
        assert p.is_paused and abs(p.get_position() - held) < 0.1
        p.resume()
        assert wait_until(lambda: p.get_position() > held + 0.3)

        assert p.seek(2.0, relative=False)                              # reloads at 2 s
        assert wait_until(lambda: 1.9 < p.get_position() < 3.0)

        assert wait_until(lambda: len(changed) == 2, timeout=8.0), "never moved on to B"
        assert [t["videoId"] for t in changed] == ["mpvA", "mpvB"] and p.current_track["videoId"] == "mpvB"
        assert wait_until(lambda: not p.is_playing, timeout=8.0), "never stopped at the end of the queue"
        assert p.queue_index == 1
    finally:
        p.audio.close()


# ------------------------------------------------------------------ Termux helpers

def test_is_termux_from_the_environment(monkeypatch):
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setenv("PREFIX", "/usr")
    assert termux.is_termux() is False
    monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
    assert termux.is_termux() is True
    monkeypatch.delenv("TERMUX_VERSION")
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    assert termux.is_termux() is True


def test_music_dir_exists_only_on_termux_after_storage_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(termux.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(termux, "is_termux", lambda: True)
    assert termux.music_dir() is None                                   # termux-setup-storage not run yet
    (tmp_path / "storage" / "shared").mkdir(parents=True)
    assert termux.music_dir() == tmp_path / "storage" / "shared" / "Music" / "Tunebox"
    monkeypatch.setattr(termux, "is_termux", lambda: False)
    assert termux.music_dir() is None                                   # never on a normal machine


def test_wake_lock_only_acts_on_termux_and_only_on_changes(monkeypatch):
    calls = []
    monkeypatch.setattr(termux, "_wake_locked", False)
    monkeypatch.setattr(termux.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(termux, "_run", lambda cmd, text=None: calls.append(cmd[0]) or True)

    monkeypatch.setattr(termux, "is_termux", lambda: False)
    assert termux.wake_lock(True) is False and calls == []              # not Termux: nothing happens

    monkeypatch.setattr(termux, "is_termux", lambda: True)
    assert termux.wake_lock(True) is True and termux.wake_lock(True) is True
    assert calls == ["/bin/termux-wake-lock"]                           # asked once, not on every tick
    assert termux.wake_lock(False) is False
    assert calls == ["/bin/termux-wake-lock", "/bin/termux-wake-unlock"]


def test_wake_lock_without_termux_api_installed_stays_off(monkeypatch):
    monkeypatch.setattr(termux, "_wake_locked", False)
    monkeypatch.setattr(termux, "is_termux", lambda: True)
    monkeypatch.setattr(termux.shutil, "which", lambda name: None)
    assert termux.wake_lock(True) is False


@pytest.mark.real_clipboard
def test_copy_uses_the_termux_clipboard(monkeypatch):
    seen = []
    monkeypatch.setattr(termux, "is_termux", lambda: True)
    monkeypatch.setattr(termux.shutil, "which", lambda name: "/bin/termux-clipboard-set" if name == "termux-clipboard-set" else None)
    monkeypatch.setattr(termux, "_run", lambda cmd, text=None: seen.append((cmd[0], text)) or True)
    assert share.copy_native("hello") == "termux-clipboard"
    assert seen == [("/bin/termux-clipboard-set", "hello")]


def test_no_graphics_probing_on_termux(monkeypatch):
    monkeypatch.setitem(inline._state, "checked", False)
    monkeypatch.setitem(inline._state, "protocol", None)
    monkeypatch.setattr(termux, "is_termux", lambda: True)
    assert inline.detect() is None
