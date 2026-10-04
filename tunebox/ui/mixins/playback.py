"""Transport and listening actions: play/pause, skip, seek, volume, shuffle/repeat/autoplay, download, radio, sleep timer, detail-page Play all."""
from typing import Any, Dict
import random

from ...core import downloader
from ...core.downloader import download_track_file
from ...core.player import player
from ...core.ytmusic import yt_client
from ..constants import SLEEP_STEPS


class PlaybackMixin:
    """Transport and listening actions: play/pause, skip, seek, volume, shuffle/repeat/autoplay, download, radio, sleep timer, detail-page Play all."""

    def action_play_all(self) -> None:
        tracks = self.detail.get("tracks", [])
        if tracks:
            self._bg(player.play, None, list(tracks), 0)
        else:
            self.say("Nothing to play here yet.", True)

    def action_shuffle_play(self) -> None:
        tracks = list(self.detail.get("tracks", []))
        if tracks:
            random.shuffle(tracks)
            self._bg(player.play, None, tracks, 0)
        else:
            self.say("Nothing to play here yet.", True)

    def action_play_pause(self) -> None:
        self._bg(player.toggle_pause)

    def action_next(self) -> None:
        self._bg(player.next)

    def action_prev(self) -> None:
        self._bg(player.previous)

    def action_skip(self, seconds: int) -> None:
        self._bg(lambda: player.seek(seconds, relative=True))

    def action_vol(self, delta: int) -> None:
        player.set_volume(player.volume + delta)
        self.say(f"Volume: {player.volume}%")

    def action_shuffle(self) -> None:
        on = player.toggle_shuffle()
        self.say(f"Shuffle {'on' if on else 'off'}")

    def action_repeat(self) -> None:
        self.say(f"Repeat: {player.cycle_repeat()}")

    def action_autoplay(self) -> None:
        on = player.toggle_autoplay()
        self.say(f"Autoplay radio {'on' if on else 'off'}")

    def action_download(self) -> None:
        tr = player.current_track
        if not tr:
            self.say("Nothing is playing.", True)
            return
        self.say(f"Downloading \"{tr.get('title')}\"...")
        self._bg(self._download, dict(tr))

    def _download(self, track: Dict[str, Any]) -> None:
        path = download_track_file(track, fmt=downloader.download_format())
        if path:
            self._ui(self.say, f"Saved: {path}")
            self._ui(self.refresh_tables)
        else:
            self._ui(self.say, f"Download failed: {downloader.last_error or 'unknown error'}", True)

    def action_radio(self) -> None:
        tr = player.current_track
        if not tr:
            self.say("Play a song first to start its radio.", True)
            return
        self.say(f"Starting radio for \"{tr.get('title')}\"...")
        self._bg(self._radio, dict(tr))

    def _radio(self, seed: Dict[str, Any]) -> None:
        related = yt_client.get_watch_playlist(seed["videoId"], limit=25)
        if related:
            player.set_queue_radio(seed, related)
            self._ui(self.say, "Radio queued - see the Queue tab.")
        else:
            self._ui(self.say, "No radio suggestions available for this song.", True)

    def action_sleep_cycle(self) -> None:
        mins = player.sleep_remaining()
        nxt = next((m for m in SLEEP_STEPS if m > mins), 0) if mins else SLEEP_STEPS[1]
        player.set_sleep_timer(nxt)
        self.say(f"Sleep timer: {nxt} min" if nxt else "Sleep timer off")
        self.tick()
