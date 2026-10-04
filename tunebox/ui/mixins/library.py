"""Favorites, playlists and queue editing: the actions that change what is saved or queued."""
import time
from typing import Any, Dict, Optional

from ...core.database import (
    add_favorite,
    add_track_to_playlist,
    add_tracks_to_playlist,
    create_playlist,
    delete_playlist,
    get_favorite_ids,
    remove_download,
    remove_favorite,
)
from ...core.player import player
from ..screens import NamePrompt, PlaylistPicker
from ..widgets import TrackTable


class LibraryMixin:
    """Favorites, playlists and queue editing: the actions that change what is saved or queued."""

    def _toggle_favorite(self, track: Dict[str, Any]) -> None:
        vid = track.get("videoId")
        if not vid:
            return
        if vid in get_favorite_ids():
            remove_favorite(vid)
            self.say(f"Removed \"{track.get('title')}\" from favorites")
        else:
            add_favorite(track)
            self.say(f"Added \"{track.get('title')}\" to favorites")
        self._sig = None
        self.tick()

    def action_favorite(self) -> None:
        if player.current_track:
            self._toggle_favorite(player.current_track)
        else:
            self.say("Nothing is playing.", True)

    def action_add_to_playlist(self) -> None:
        track = self._target_track()
        if not track:
            self.say("Highlight a song (or play one) first.", True)
            return

        def done(pid: Optional[str]) -> None:
            if pid and add_track_to_playlist(pid, track):
                self.say(f"Added \"{track.get('title')}\" to playlist")
                self._sig = None
                self.refresh_tables()

        self.push_screen(PlaylistPicker(track.get("title", "track")), done)

    def action_remove_selected(self) -> None:
        table = self.focused if isinstance(self.focused, TrackTable) else None
        if table is None or table.selected_item() is None:
            return
        if table.kind == "queue":
            self._bg(player.remove_from_queue, table.cursor_row)
        elif table.kind == "library" and self.library_sub == "playlists":
            item = table.selected_item()
            if item.get("type") == "local_playlist":
                delete_playlist(item["id"])
                self.say(f"Deleted playlist \"{item['title']}\"")
                self.refresh_tables()
        elif table.kind == "downloads":
            item = table.selected_item()
            remove_download(item["videoId"])
            self.say(f"Deleted \"{item.get('title')}\" from disk")
            self._sig = None
            self.refresh_tables()
        elif table.kind == "library" and self.library_sub == "favorites":
            item = table.selected_item()
            remove_favorite(item["videoId"])
            self._sig = None
            self.tick()

    def action_queue_add(self, play_next: bool) -> None:
        track = self._target_track()
        if not track:
            self.say("Highlight a song (or play one) first.", True)
            return
        if play_next:
            self._bg(player.play_next, track)
            self.say(f"Playing next: {track.get('title')}")
        else:
            self._bg(player.add_to_queue, track)
            self.say(f"Added to queue: {track.get('title')}")

    def action_queue_move(self, delta: int) -> None:
        table = self.focused
        if not (isinstance(table, TrackTable) and table.kind == "queue") or table.selected_item() is None:
            return
        new = player.move_in_queue(table.cursor_row, delta)
        self._sig = None
        self.refresh_tables()
        table.move_cursor(row=new, animate=False)

    def action_save_queue(self) -> None:
        """S: ask for a name, then save the whole queue (as it is now) as a new playlist."""
        songs = [t for t in list(player.queue) if t.get("videoId")]
        if not songs:
            self.say("The queue is empty. Nothing to save.", True)
            return

        def done(name: Optional[str]) -> None:
            if not name:
                return
            saved = add_tracks_to_playlist(create_playlist(name)["id"], songs)
            self.say(f"Saved {saved} song{'s' if saved != 1 else ''} to playlist \"{name}\"")
            self._sig = None
            self.refresh_tables()

        self.push_screen(NamePrompt(f"Save {len(songs)} queued song{'s' if len(songs) != 1 else ''} as a playlist",
                                    time.strftime("Queue %b %d")), done)

    def action_clear_queue(self) -> None:
        player.clear_queue()
        self.say("Queue cleared")
