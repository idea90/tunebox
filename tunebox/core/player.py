"""
Audio Player & Queue Manager for Tunebox
Built on pygame.mixer with background pre-fetching, gapless hand-over, seek, volume,
shuffle, repeat modes, and smart auto-radio.

Thread model: UI threads, worker threads and the monitor thread all call into this
class. `_lock` guards queue/state mutation; `_load_lock` serializes track loading so
two loads never touch the mixer at once.

Gapless: while a song plays, the next one is downloaded and handed to the mixer
(`pygame.mixer.music.queue`). The mixer has no "un-queue", so whenever an edit changes
what should play next, `_rearm()` reloads the current song at its exact position (which
clears the mixer's queue) and queues the right song instead. The app's idea of "what is
next" and the audio that will actually play therefore never drift apart.
"""
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

import time
import random
import threading
import pygame
from typing import List, Dict, Any, Optional, Callable
from ..config import config
from . import downloader
from .downloader import cache_track_audio, get_cached_track_path
from .database import add_history
from .ytmusic import yt_client

REPEAT_MODES = ("off", "all", "one")


class Player:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Player, cls).__new__(cls)
            cls._instance.init_player()
        return cls._instance

    def init_player(self):
        self.audio_ok = True
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
        except Exception:
            try:
                pygame.mixer.init()
            except Exception:
                self.audio_ok = False

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
        try:
            pygame.mixer.music.set_volume(self.volume / 100.0)
        except Exception:
            pass

    def _index_of(self, track: Optional[Dict[str, Any]]) -> Optional[int]:
        """Position of this exact queue entry (by identity: the same song can be queued twice)."""
        if track is None:
            return None
        return next((i for i, t in enumerate(self.queue) if t is track), None)

    def index_of(self, track: Optional[Dict[str, Any]]) -> Optional[int]:
        with self._lock:
            return self._index_of(track)

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

    def cycle_repeat(self) -> str:
        idx = REPEAT_MODES.index(self.repeat_mode) if self.repeat_mode in REPEAT_MODES else 0
        self.repeat_mode = REPEAT_MODES[(idx + 1) % len(REPEAT_MODES)]
        config.set("repeat_mode", self.repeat_mode)
        self._rearm()                          # "one" vs "off/all" changes what plays next
        self._notify()
        return self.repeat_mode

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
            pos_ms = pygame.mixer.music.get_pos()
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
                pygame.mixer.music.stop()      # don't let the previous song play on during the download
            except Exception:
                pass
            self._notify()
            try:
                if not self.audio_ok:
                    self._fail("No audio output device available.")
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
                    pygame.mixer.music.load(audio_file)
                    self._apply_volume()
                    if seek_start > 0:
                        pygame.mixer.music.play(start=seek_start)
                    else:
                        pygame.mixer.music.play()
                    if start_paused:
                        pygame.mixer.music.pause()
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
                # pygame's get_pos() already includes everything played so far.
                self._paused_pos = self.get_position()
                pygame.mixer.music.pause()
                self.is_paused = True
                self._notify()
            except Exception:
                pass

    def resume(self):
        if self.is_playing and self.is_paused:
            try:
                pygame.mixer.music.unpause()
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
            pygame.mixer.music.stop()
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

    # ------------------------------------------------------------ choosing the next song

    def _reset_bag(self, include_current: bool = False) -> None:
        """Start a shuffle cycle. The first cycle leaves out the song already playing; later cycles
        (repeat all) contain every song, just never starting with the one that just ended."""
        items = list(self.queue) if include_current else [t for i, t in enumerate(self.queue) if i != self.queue_index]
        random.shuffle(items)
        if include_current and len(items) > 1 and items[0] is self.current_track:
            items[0], items[1] = items[1], items[0]
        self._bag = items
        self._bag_valid = True

    def _consume(self, track: Optional[Dict[str, Any]]) -> None:
        """This entry is now playing: it is no longer 'to come' in the shuffle cycle."""
        if track is not None:
            self._bag = [t for t in self._bag if t is not track]

    def _pick_next_index(self) -> Optional[int]:
        """Index of the next queue entry, or None when the queue is exhausted. Deterministic until something changes."""
        n = len(self.queue)
        if n == 0:
            return None
        if self.shuffle and n > 1:
            if not self._bag_valid:
                self._reset_bag()
            for t in self._bag:
                i = self._index_of(t)
                if i is not None and i != self.queue_index:
                    return i
            if self.repeat_mode == "all":           # cycle finished: start a new one with every song
                self._reset_bag(include_current=True)
                return self._index_of(self._bag[0]) if self._bag else None
            return None
        nxt = self.queue_index + 1
        if nxt < n:
            return nxt
        return 0 if self.repeat_mode == "all" else None

    def upcoming(self, limit: int = 8) -> List[Dict[str, Any]]:
        """The songs that will actually play next, in order (shuffle-aware). For the Up Next list."""
        with self._lock:
            if not self.queue or self.queue_index < 0:
                return []
            if self.shuffle and len(self.queue) > 1:
                if not self._bag_valid:
                    self._reset_bag()
                return [t for t in self._bag if self._index_of(t) is not None and t is not self.current_track][:limit]
            return self.queue[self.queue_index + 1: self.queue_index + 1 + limit]

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

    # ------------------------------------------------------------ queue editing

    def _is_idle(self) -> bool:
        return not self.is_playing and not self.is_paused and not self.is_loading

    def set_queue_radio(self, seed: Dict[str, Any], tracks: List[Dict[str, Any]]):
        with self._lock:
            self.queue = [seed] + list(tracks)
            self.queue_index = 0
            self._bag_valid = False
        self._rearm()
        self._notify()

    def add_to_queue(self, track: Dict[str, Any]):
        with self._lock:
            self.queue.append(track)
            if self.shuffle and self._bag_valid:
                self._bag.insert(random.randint(0, len(self._bag)), track)
            idle = self._is_idle()
            if idle:                               # nothing playing: start it, and make the queue agree
                self.queue_index = len(self.queue) - 1
                self._consume(track)
        if idle:
            self.play_track(track)
        else:
            self._rearm()                          # appending at the end can change what's next
        self._notify()

    def play_next(self, track: Dict[str, Any]):
        with self._lock:
            pos = max(0, self.queue_index + 1)
            self.queue.insert(pos, track)
            if self.shuffle and self._bag_valid:
                self._bag.insert(0, track)         # "play next" wins over the shuffle order
            idle = self._is_idle()
            if idle:
                self.queue_index = pos
                self._consume(track)
        if idle:
            self.play_track(track)
        else:
            self._rearm()
        self._notify()

    def remove_from_queue(self, index: int):
        replay = None
        stop = False
        with self._lock:
            if not (0 <= index < len(self.queue)):
                return
            removed = self.queue.pop(index)
            self._consume(removed)
            if index < self.queue_index:
                self.queue_index -= 1
            elif index == self.queue_index:
                if self.queue_index < len(self.queue):
                    replay = self.queue[self.queue_index]
                    self._consume(replay)
                else:                              # removed the playing song and it was the last one
                    self.queue_index = len(self.queue) - 1
                    stop = True
        if replay is not None:
            self.play_track(replay)
        elif stop:
            self.stop()
        else:
            self._rearm()
        self._notify()

    def clear_queue(self):
        with self._lock:
            if self.current_track and self.is_playing:
                self.queue = [self.current_track]
                self.queue_index = 0
            else:
                self.queue = []
                self.queue_index = -1
            self._bag = []
            self._bag_valid = False
        self._rearm()
        self._notify()

    def move_in_queue(self, index: int, delta: int) -> int:
        """Move queue[index] by delta, keeping the playing track playing. Returns the new index."""
        with self._lock:
            new = index + delta
            if not (0 <= index < len(self.queue) and 0 <= new < len(self.queue)):
                return index
            self.queue[index], self.queue[new] = self.queue[new], self.queue[index]
            if self.queue_index == index:
                self.queue_index = new
            elif self.queue_index == new:
                self.queue_index = index
        self._rearm()
        self._notify()
        return new

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
                pygame.mixer.music.load(path)
                pygame.mixer.music.play(start=pos) if pos > 0 else pygame.mixer.music.play()
                if paused:
                    pygame.mixer.music.pause()
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
                pygame.mixer.music.queue(path)
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
                pos_ms = pygame.mixer.music.get_pos()
                if self._armed and pos_ms >= 0 and self._last_pos_ms - pos_ms > 1000:
                    self._on_gapless_advance()
                self._last_pos_ms = pos_ms
                if not pygame.mixer.music.get_busy() and not self.is_loading:
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
