"""Requests from outside the app: desktop media controls (MPRIS) and what the app tells them back."""
from typing import Any

from ...core import mpris
from ...core.player import player


class RemoteMixin:
    """Requests from outside the app: desktop media controls (MPRIS) and what the app tells them back."""

    def _start_mpris(self) -> None:
        def handler(action: str, arg: Any = None) -> None:      # runs on the D-Bus thread
            self._ui(self._remote_action, action, arg)

        def ready(ok: bool) -> None:
            if ok:
                self._ui(self.say, "Desktop media controls enabled (MPRIS).")

        mpris.start(handler, player.get_position, ready)

    def _mpris_sync(self) -> None:
        """Tell the desktop what is playing. Cheap: the service only sends what changed."""
        mpris.update(mpris.snapshot_from_player(player))

    def _remote_action(self, action: str, arg: Any = None) -> None:
        """Carry out a request from the desktop (UI thread). Slow work goes through _bg like a key press does."""
        if action == "play":
            if not (player.is_playing and not player.is_paused) and player.current_track:
                self.action_play_pause()
        elif action == "pause":
            if player.is_playing and not player.is_paused:
                self.action_play_pause()
        elif action == "play_pause":
            self.action_play_pause()
        elif action == "next":
            self.action_next()
        elif action == "prev":
            self.action_prev()
        elif action == "stop":
            self._bg(player.stop)
        elif action == "seek":                                   # seconds, relative
            self._bg(lambda: player.seek(float(arg), relative=True))
        elif action == "set_position":                           # seconds, absolute
            self.seek_to(float(arg))
        elif action == "volume":                                 # 0.0 - 1.0
            player.set_volume(round(float(arg) * 100))
        elif action == "loop":                                   # "off" / "all" / "one"
            self._bg(player.set_repeat, arg)
        elif action == "shuffle":
            self._bg(player.set_shuffle, bool(arg))
        elif action == "quit":
            self.action_quit_app()
