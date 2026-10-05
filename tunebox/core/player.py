"""
Audio Player & Queue Manager for Tunebox
Plays through an audio backend (core/audio.py: pygame.mixer, or mpv on Termux) with background
pre-fetching, gapless hand-over, seek, volume, shuffle, repeat modes, and smart auto-radio.

Thread model: UI threads, worker threads and the monitor thread all call into this
class. `_lock` guards queue/state mutation; `_load_lock` serializes track loading so
two loads never touch the mixer at once.

Gapless: while a song plays, the next one is downloaded and handed to the mixer
(`audio.queue`). The audio engine has no "un-queue", so whenever an edit changes what
should play next, `_rearm()` reloads the current song at its exact position (which clears
the engine's queue) and queues the right song instead. The app's idea of "what is
next" and the audio that will actually play therefore never drift apart.

Queue editing and the shuffle bag live in `QueueMixin` (core/playqueue.py).
"""
import os
import time
import random
import threading
from typing import List, Dict, Any, Optional, Callable
from ..config import config
from .audio import create_audio_backend
from . import downloader
from .downloader import cache_track_audio, get_cached_track_path
from .database import add_history
from .playqueue import QueueMixin
from .ytmusic import yt_client

REPEAT_MODES = ("off", "all", "one")


class Player(QueueMixin):
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Player, cls).__new__(cls)
            cls._instance.init_player()
        return cls._instance

    def init_player(self):
        self.audio = create_audio_backend()           # pygame.mixer, or mpv on Termux (core/audio.py)
        self.audio_ok = bool(self.audio.ok)
        self.audio_problem = self.audio.problem

        self.queue: List[Dict[str, Any]] = []
        self.queue_index: int = -1

        self.current_track: Optional[Dict[str, Any]] = None
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.is_loading: bool = False
        self.last_error: str = ""
        self.volume: int = int(config.get("volume", 80))
        mode = str(config.get("repeat_mode", "off")).lower()
        self.repeat_mode: str = mode if mode in REPEAT_MODES else "off"
        self.shuffle: bool = bool(config.get("shuffle", False))
        self.autoplay: bool = bool(config.get("autoplay_radio", True))

        self.seek_offset: float = 0.0
        self._paused_pos: float = 0.0
        self.track_duration: float = 0.0

        self._lock = threading.RLock()
        self._load_lock = threading.Lock()
        self._req = 0
        self._gen = 0                         # bumped on every (re)load; invalidates a queued gapless track
        self._armed: Optional[tuple] = None   # (gen, queue_index, track) handed to the mixer for gapless
        self._armed_path: Optional[str] = None
        self._current_file: Optional[str] = None
        self._last_pos_ms = 0
        self._resume_pos: float = 0.0         # where a restored/stopped track will start from
        self.sleep_deadline: Optional[float] = None

        # Shuffle bag: the songs still to come this cycle, in their shuffled order. Every song plays
        # once per cycle; built lazily (after a new queue, a restore, or turning shuffle on).
        self._bag: List[Dict[str, Any]] = []
        self._bag_valid = False

        self.on_track_change: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_state_change: Optional[Callable[[], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_info: Optional[Callable[[str], None]] = None

        self._monitor_thread = threading.Thread(target=self._playback_monitor_loop, daemon=True)
        self._monitor_thread.start()

        self._apply_volume()

    # ------------------------------------------------------------ helpers

    def _notify(self):
        cb = self.on_state_change
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _fail(self, msg: str):
        self.last_error = msg
        cb = self.on_error
        if cb:
            try:
                cb(msg)
            except Exception:
                pass

    def _info(self, msg: str):
        cb = self.on_info
        if cb:
            try:
                cb(msg)
            except Exception:
                pass

    def _apply_volume(self):
        self.audio.set_volume(self.volume / 100.0)

    # ------------------------------------------------------------ settings

    def set_volume(self, val: int):
        self.volume = max(0, min(100, int(val)))
        config.set("volume", self.volume)
        self._apply_volume()
        self._notify()

    def volume_up(self, step: int = 5):
        self.set_volume(self.volume + step)

    def volume_down(self, step: int = 5):
        self.set_volume(self.volume - step)

    def toggle_shuffle(self) -> bool:
        with self._lock:
            self.shuffle = not self.shuffle
            self._bag_valid = False           # a fresh shuffle cycle starts from the current song
        config.set("shuffle", self.shuffle)
        self._rearm()
        self._notify()
        return self.shuffle

    def set_shuffle(self, on: bool) -> bool:
        return self.toggle_shuffle() if bool(on) != self.shuffle else self.shuffle

    def set_repeat(self, mode: str) -> str:
        if mode not in REPEAT_MODES:
            return self.repeat_mode
        self.repeat_mode = mode
        config.set("repeat_mode", self.repeat_mode)
        self._rearm()                          # "one" vs "off/all" changes what plays next
        self._notify()
        return self.repeat_mode

    def cycle_repeat(self) -> str:
        idx = REPEAT_MODES.index(self.repeat_mode) if self.repeat_mode in REPEAT_MODES else 0
        return self.set_repeat(REPEAT_MODES[(idx + 1) % len(REPEAT_MODES)])

    def toggle_autoplay(self) -> bool:
        self.autoplay = not self.autoplay
        config.set("autoplay_radio", self.autoplay)
        self._notify()
        return self.autoplay

    # ------------------------------------------------------------ position

    def get_position(self) -> float:
        """Current playback position in seconds."""
        if not self.is_playing:
            return self._resume_pos if self.current_track else 0.0
        if self.is_paused:
            return self._paused_pos
        if self.is_loading:
            return self.seek_offset
        try:
            pos_ms = self.audio.pos_ms()
            if pos_ms < 0:
                return self.seek_offset
            return self.seek_offset + (pos_ms / 1000.0)
        except Exception:
            return self.seek_offset

    # ------------------------------------------------------------ playback

    def _load_and_play(self, track: Dict[str, Any], seek_start: float, record_history: bool,
                       start_paused: bool = False) -> bool:
        """Resolve audio for `track` and start it. Blocks while downloading.

        The old song stops immediately (not after the download), and if a newer request arrives
        while this one waits or downloads, this one gives up so only the latest choice plays.
        """
        self._req += 1
        my_req = self._req
        superseded = False
        with self._load_lock:
            if my_req != self._req:
                return False
            self.is_loading = True
            with self._lock:
                self._gen += 1
                self._armed = None
                self._armed_path = None
                self._resume_pos = 0.0
                self.current_track = track
                self.track_duration = float(track.get("duration_seconds") or 0)
                self.is_paused = False
                self.seek_offset = seek_start
                self._paused_pos = seek_start
                self._last_pos_ms = 0
            try:
                self.audio.stop()              # don't let the previous song play on during the download
            except Exception:
                pass
            self._notify()
            try:
                if not self.audio_ok:
                    self._fail(self.audio_problem or "No audio output device available.")
                    with self._lock:
                        self.is_playing = False
                    return False

                audio_file = track.get("filePath")
                if not audio_file or not os.path.exists(audio_file):
                    audio_file = cache_track_audio(track)

                if my_req != self._req:
                    # The user picked something else while this was downloading. Keep is_loading set so the
                    # monitor doesn't mistake the silence for "song finished" before the newer load takes over.
                    superseded = True
                    return False

                if not audio_file or not os.path.exists(audio_file):
                    reason = downloader.last_error or "stream unavailable"
                    self._fail(f"Could not fetch \"{track.get('title', 'track')}\": {reason}")
                    with self._lock:
                        self.is_playing = False
                        self.is_paused = False
                    return False

                try:
                    self._apply_volume()
                    self.audio.load(audio_file, start=seek_start, paused=start_paused)
                except Exception as e:
                    self._fail(f"Playback error: {e}")
                    with self._lock:
                        self.is_playing = False
                        self.is_paused = False
                    return False

                with self._lock:
                    self.is_playing = True
                    self.is_paused = start_paused
                    self.seek_offset = seek_start
                    self._paused_pos = seek_start
                    # New baseline for gapless hand-over detection; a stale one makes a seek look like a track change.
                    self._last_pos_ms = 0
                    self._current_file = audio_file
                self.last_error = ""
            finally:
                if not superseded:
                    self.is_loading = False

        if record_history:
            threading.Thread(target=add_history, args=(track,), daemon=True).start()
        cb = self.on_track_change
        if cb and record_history:
            try:
                cb(track)
            except Exception:
                pass
        self._notify()
        self._trigger_prefetch()
        return True

    def play_track(self, track: Dict[str, Any]) -> bool:
        return self._load_and_play(track, 0.0, record_history=True)

    def play(self, track: Optional[Dict[str, Any]] = None, queue: Optional[List[Dict[str, Any]]] = None, index: int = 0):
        """Play a given track, or a queue starting at `index`; with no args, resume."""
        if queue is not None:
            with self._lock:
                self.queue = list(queue)
                self.queue_index = max(0, min(index, len(self.queue) - 1)) if self.queue else -1
                target = self.queue[self.queue_index] if self.queue else None
                self._bag_valid = False
            if target:
                self.play_track(target)
            return

        if track is not None:
            with self._lock:
                v_id = track.get("videoId")
                found = next((i for i, t in enumerate(self.queue) if v_id and t.get("videoId") == v_id), -1)
                if found != -1:
                    self.queue_index = found
                    track = self.queue[found]
                elif not self.queue:
                    self.queue = [track]
                    self.queue_index = 0
                else:
                    self.queue.insert(self.queue_index + 1, track)
                    self.queue_index += 1
                self._consume(track)
            self.play_track(track)
            return

        if self.is_paused:
            self.resume()

    def jump_to(self, index: int) -> bool:
        with self._lock:
            if not (0 <= index < len(self.queue)):
                return False
            self.queue_index = index
            track = self.queue[index]
            self._consume(track)
        return self.play_track(track)

    def pause(self):
        if self.is_playing and not self.is_paused and not self.is_loading:
            try:
                # Freeze the displayed position. Do NOT fold it into seek_offset:
                # the backend's pos_ms() already includes everything played so far.
                self._paused_pos = self.get_position()
                self.audio.pause()
                self.is_paused = True
                self._notify()
            except Exception:
                pass

    def resume(self):
        if self.is_playing and self.is_paused:
            try:
                self.audio.unpause()
                self.is_paused = False
                self._notify()
            except Exception:
                pass

    def toggle_pause(self):
        if self.is_paused:
            self.resume()
        elif self.is_playing:
            self.pause()
        elif self.current_track and not self.is_loading:
            start = self._resume_pos   # a restored session resumes mid-song
            self._load_and_play(self.current_track, start, record_history=start == 0)

    def stop(self):
        try:
            self.audio.stop()
        except Exception:
            pass
        with self._lock:
            self.is_playing = False
            self.is_paused = False
            self.seek_offset = 0.0
            self._resume_pos = 0.0
            self._armed = None
            self._armed_path = None
        self._notify()

    # ------------------------------------------------------------ skipping and seeking

    def next(self, auto: bool = False) -> bool:
        """Skip to the next track. `auto` is True when a track ended on its own."""
        target = None
        with self._lock:
            if auto and self.repeat_mode == "one" and self.current_track:
                target = self.current_track
            else:
                nxt = self._pick_next_index()
                if nxt is not None:
                    self.queue_index = nxt
                    target = self.queue[nxt]
                    self._consume(target)
        if target is not None:
            return self.play_track(target)

        seed = self.current_track
        if self.autoplay and seed and seed.get("videoId"):
            related = yt_client.get_watch_playlist(seed["videoId"], limit=15)
            if related:
                with self._lock:
                    start = len(self.queue)
                    self.queue.extend(related)
                    self.queue_index = start
                    target = self.queue[start]
                    if self.shuffle and self._bag_valid:   # the radio songs join the shuffle cycle
                        rest = list(self.queue[start + 1:])
                        random.shuffle(rest)
                        self._bag.extend(rest)
                return self.play_track(target)

        self.stop()
        return False

    def previous(self) -> bool:
        """Previous track, or restart the current one if >3s in."""
        if self.get_position() > 3.0:
            return self.seek(0, relative=False)

        with self._lock:
            if not self.queue:
                return False
            if self.queue_index > 0:
                self.queue_index -= 1
            self.queue_index = max(0, self.queue_index)
            track = self.queue[self.queue_index]
        return self.play_track(track)

    def seek(self, seconds: float, relative: bool = True) -> bool:
        """Seek within the current track without re-recording history. A paused song stays paused."""
        if not self.current_track or not self.is_playing:
            return False
        target = (self.get_position() + seconds) if relative else seconds
        upper = max(0.0, self.track_duration - 1.0) if self.track_duration else target
        target = max(0.0, min(upper, target))
        extra = {"start_paused": True} if self.is_paused else {}
        return self._load_and_play(self.current_track, target, record_history=False, **extra)

    # ------------------------------------------------------------ gapless

    def _peek_next(self):
        """(queue_index, track) that will play after the current one, or None (autoplay/radio is not peeked)."""
        with self._lock:
            if self.repeat_mode == "one" and self.current_track:
                return self.queue_index, self.current_track
            idx = self._pick_next_index()
            return (idx, self.queue[idx]) if idx is not None else None

    def _rearm(self) -> None:
        """After a queue/setting change, make sure the song handed to the mixer is still the right next one."""
        with self._lock:
            armed = self._armed
            if armed is None:
                stale = False
            else:
                nxt = self._peek_next()
                if nxt is not None and nxt[1] is armed[2]:
                    self._armed = (armed[0], nxt[0], armed[2])   # same song, maybe a new position: keep it
                    return
                stale = True
                self._armed = None
                self._armed_path = None
                self._gen += 1                                   # also cancels any in-flight prefetch
        if stale:
            self._unqueue_mixer()
        if self.is_playing and not self.is_loading:
            self._trigger_prefetch()

    def _unqueue_mixer(self) -> None:
        """Drop whatever the mixer has queued by reloading the current file at the same spot (load() clears the queue)."""
        if self.is_loading or not self._load_lock.acquire(blocking=False):
            return                                               # a load in progress resets the mixer anyway
        try:
            with self._lock:
                path, playing, paused = self._current_file, self.is_playing, self.is_paused
                pos = self.get_position()
            if not path or not playing or not os.path.exists(path):
                return
            try:
                self.audio.load(path, start=pos, paused=paused)
            except Exception:
                return
            with self._lock:
                self.seek_offset = pos
                self._paused_pos = pos
                self._last_pos_ms = 0
        finally:
            self._load_lock.release()

    def _trigger_prefetch(self):
        """Fetch the next song in the background and, if enabled, hand it to the mixer for gapless playback."""
        nxt = self._peek_next()
        if not nxt:
            return
        with self._lock:
            gen = self._gen
        threading.Thread(target=self._prefetch_and_arm, args=(gen, nxt[0], nxt[1]), daemon=True).start()

    def _prefetch_and_arm(self, gen: int, idx: int, track: Dict[str, Any]):
        path = track.get("filePath") if track.get("filePath") and os.path.exists(track["filePath"]) else None
        path = path or cache_track_audio(track)
        if not path or not config.get("gapless", True):
            return
        with self._lock:
            if gen != self._gen or not self.is_playing or self._armed is not None:
                return                                           # user moved on, or already armed this generation
            nxt = self._peek_next()
            if nxt is None or nxt[1] is not track:               # the queue changed while we were downloading
                return
            try:
                self.audio.queue(path)
            except Exception:
                return
            self._armed = (gen, nxt[0], track)
            self._armed_path = path

    def _on_gapless_advance(self):
        """The mixer started the queued track on its own: update our state to match."""
        with self._lock:
            if not self._armed or self._armed[0] != self._gen:
                return
            _, idx, track = self._armed
            real = self._index_of(track)
            if real is None:
                # Shouldn't happen (_rearm un-queues removed songs), but never let the queue and the audio disagree.
                real = min(self.queue_index + 1, len(self.queue))
                self.queue.insert(real, track)
            self._armed = None
            self._gen += 1
            self.queue_index = real
            self._consume(track)
            self.current_track = track
            self._current_file = self._armed_path or self._current_file
            self._armed_path = None
            self.track_duration = float(track.get("duration_seconds") or 0)
            self.seek_offset = 0.0
            self._resume_pos = 0.0
            self._last_pos_ms = 0
        threading.Thread(target=add_history, args=(track,), daemon=True).start()
        cb = self.on_track_change
        if cb:
            try:
                cb(track)
            except Exception:
                pass
        self._notify()
        self._trigger_prefetch()

    def _playback_monitor_loop(self):
        """Background watcher: sleep timer, gapless hand-over and natural end-of-track."""
        while True:
            time.sleep(0.25)

            deadline = self.sleep_deadline
            if deadline and time.time() >= deadline:
                self.sleep_deadline = None
                if self.is_playing and not self.is_paused:
                    self.pause()
                self._info("Sleep timer finished - playback paused.")

            if not self.is_playing or self.is_paused or self.is_loading:
                continue
            try:
                pos_ms = self.audio.pos_ms()
                if self._armed and pos_ms >= 0 and self._last_pos_ms - pos_ms > 1000:
                    self._on_gapless_advance()
                self._last_pos_ms = pos_ms
                if not self.audio.busy() and not self.is_loading:
                    self.next(auto=True)
            except Exception:
                pass

    # ------------------------------------------------------------ sleep timer / session

    def set_sleep_timer(self, minutes: int) -> None:
        self.sleep_deadline = time.time() + minutes * 60 if minutes > 0 else None
        self._notify()

    def sleep_remaining(self) -> int:
        """Whole minutes left on the sleep timer (rounded up), 0 when off."""
        if not self.sleep_deadline:
            return 0
        return max(1, int((self.sleep_deadline - time.time() + 59) // 60))

    def restore(self, queue: List[Dict[str, Any]], index: int, position: float) -> None:
        """Load a saved queue without playing; pressing play resumes from `position`."""
        with self._lock:
            self.queue = list(queue)
            self.queue_index = index if 0 <= index < len(self.queue) else (0 if self.queue else -1)
            self.current_track = self.queue[self.queue_index] if self.queue else None
            self.track_duration = float((self.current_track or {}).get("duration_seconds") or 0)
            self._resume_pos = max(0.0, float(position)) if self.current_track else 0.0
            self.is_playing = False
            self.is_paused = False
            self._bag_valid = False
        self._notify()


player = Player()
