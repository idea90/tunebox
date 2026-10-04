"""
Queue state and editing for the Player: shuffle bag, choosing the next song, and add / remove / move.

`QueueMixin` is mixed into `Player` (core/player.py), which owns the state it touches: `queue`,
`queue_index`, `current_track`, `shuffle`, `repeat_mode`, `_bag`, `_bag_valid` and `_lock`, and the
playback hooks `play_track`, `stop`, `_rearm` and `_notify`.
"""
import random
from typing import Any, Dict, List, Optional


class QueueMixin:
    def _index_of(self, track: Optional[Dict[str, Any]]) -> Optional[int]:
        """Position of this exact queue entry (by identity: the same song can be queued twice)."""
        if track is None:
            return None
        return next((i for i, t in enumerate(self.queue) if t is track), None)

    def index_of(self, track: Optional[Dict[str, Any]]) -> Optional[int]:
        with self._lock:
            return self._index_of(track)

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

