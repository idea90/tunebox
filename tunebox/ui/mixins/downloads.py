"""Download a whole list in one go: a playlist or album page, a highlighted playlist, the queue, favorites..."""
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...core import batch, downloader
from ...core.database import get_playlist
from ...core.player import player
from ...core.ytmusic import yt_client
from ..screens import ConfirmPrompt
from ..widgets import TrackTable, is_track

LIBRARY_LISTS = {"favorites": ("Favorites", True), "history": ("History", False),
                 "most_played": ("Most played", False), "yt_liked": ("Liked songs", True)}   # name, own folder?

# (label for messages, tracks already known, fetch the complete list or None, folder name or None)
Target = Tuple[str, List[Dict[str, Any]], Optional[Callable[[], List[Dict[str, Any]]]], Optional[str]]


def _plural(n: int, word: str = "song") -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class DownloadsMixin:
    """Download a whole list in one go: a playlist or album page, a highlighted playlist, the queue, favorites..."""

    # ------------------------------------------------------------ choosing what to download

    def _download_all_target(self) -> Optional[Target]:
        """What `D` means right now, or None (after saying why). Never the Home or Search list itself: pressing
        D by accident must not start dozens of downloads, so those need a highlighted album / playlist."""
        table = self.focused if isinstance(self.focused, TrackTable) else self.active_table()
        item = table.selected_item() if table else None
        if item and item.get("browseId") and item.get("type") in ("album", "playlist"):
            return self._remote_target(item.get("type"), item["browseId"], item.get("title") or item.get("name") or "Playlist")

        tab = self.active_tab
        if tab == "detail":
            tracks = list(self.detail.get("tracks", []))
            title = self.detail.get("title") or "this page"
            if not tracks:
                self.say("Nothing to download here yet.", True)
                return None
            if self.detail.get("kind") == "playlist":
                return self._remote_target("playlist", self.detail["id"], title, known=tracks)
            return title, tracks, None, title
        if tab == "queue":
            return "the queue", list(player.queue), None, None
        if tab == "library":
            sub = self.library_sub
            if sub in LIBRARY_LISTS:
                label, own_folder = LIBRARY_LISTS[sub]
                return label, [t for t in table.items if is_track(t)] if table else [], None, label if own_folder else None
            if item and item.get("type") == "local_playlist":
                pl = get_playlist(item["id"])
                return item["title"], list((pl or {}).get("tracks", [])), None, item["title"]
            self.say("Highlight a playlist first, then press D.", True)
            return None
        self.say("Open a playlist or album (or the Queue or Library tab), or highlight one in a list, then press D.", True)
        return None

    @staticmethod
    def _remote_target(kind: str, browse_id: str, title: str, known: Optional[List[Dict[str, Any]]] = None) -> Target:
        """A YouTube playlist or album: fetch the complete track list (the page may only hold the first 100)."""
        def fetch() -> List[Dict[str, Any]]:
            data = yt_client.get_album(browse_id) if kind == "album" else yt_client.get_playlist(browse_id, limit=None)
            return list(data.get("tracks", []))
        return title, list(known or []), fetch, title

    # ------------------------------------------------------------ asking, then running

    def action_download_all(self) -> None:
        if self.batch is not None:
            self._confirm_stop()
            return
        target = self._download_all_target()
        if target is not None:
            label, tracks, fetch, folder = target
            if fetch is not None:
                self.say(f"Fetching the whole of \"{label}\"...")
            self._bg(self._prepare_batch, label, tracks, fetch, folder)

    def _prepare_batch(self, label: str, tracks: List[Dict[str, Any]], fetch, folder: Optional[str]) -> None:
        """(worker) Get the full list if needed and work out what is new, then ask."""
        if fetch is not None:
            fetched = fetch()
            if fetched:
                tracks = fetched
            elif not tracks:
                self._ui(self.say, yt_client.last_error or f"Could not load \"{label}\".", True)
                return
        fmt = downloader.download_format()
        todo, skipped, unavailable = batch.plan(tracks, fmt)
        # call_later queues it on the app's own message loop: a dialog's widgets can only be built there, not in a
        # callback that arrives from a worker thread
        self._ui(self.call_later, self._confirm_batch, label, tracks, folder, fmt, len(todo), skipped, unavailable)

    def _confirm_batch(self, label: str, tracks, folder, fmt: str, new: int, skipped: int, unavailable: int) -> None:
        if not new:
            self.say(f"Everything in \"{label}\" is already downloaded." if skipped
                     else f"Nothing to download in \"{label}\".", not skipped)
            return
        lines = [f"Download {_plural(new)} from \"{label}\" as {fmt.upper()}?"]
        if skipped:
            lines.append(f"{_plural(skipped)} already downloaded will be skipped.")
        if unavailable:
            lines.append(f"{_plural(unavailable, 'entry')} without a video will be left out.")
        folder_name = batch.folder_for(folder)
        lines.append(f"Saved in: {Path(self._download_root()) / folder_name if folder_name else self._download_root()}")

        def answered(yes: Optional[bool]) -> None:
            if yes:
                self._start_batch(label, tracks, folder, fmt, new + skipped)

        self.push_screen(ConfirmPrompt("\n".join(lines), yes_label="Download"), answered)

    @staticmethod
    def _download_root() -> str:
        from ...config import config, DOWNLOADS_DIR
        return str(config.get("download_dir", DOWNLOADS_DIR))

    def _start_batch(self, label: str, tracks, folder: Optional[str], fmt: str, total: int) -> None:
        skipped = total - len(batch.plan(tracks, fmt)[0])
        self.batch = {"label": label, "done": skipped, "total": total, "fmt": fmt, "cancel": threading.Event()}
        self.say(f"Downloading {_plural(total - skipped)} from \"{label}\"... press D to stop.")
        self._bg(self._run_batch, label, tracks, folder, fmt)

    def _run_batch(self, label: str, tracks, folder: Optional[str], fmt: str) -> None:
        """(worker) The downloads themselves. Always ends by clearing the running state."""
        state = self.batch
        try:
            result = batch.download_tracks(
                tracks, fmt, subfolder=folder, cancel=state["cancel"] if state else None,
                on_progress=lambda event, done, total, track, detail: self._ui(self._batch_progress, done, total))
        except Exception as e:
            result = batch.BatchResult(failed=[({"title": label}, f"{type(e).__name__}: {e}"[:160])])
        self._ui(self._batch_done, label, result)

    def _batch_progress(self, done: int, total: int) -> None:
        if self.batch is not None:
            self.batch.update(done=done, total=total)
            self.tick()

    def _batch_done(self, label: str, result: "batch.BatchResult") -> None:
        self.batch = None
        got = len(result.downloaded)
        if result.cancelled:
            msg = f"Stopped: {got} of {result.total - result.skipped} downloaded from \"{label}\"."
        elif not result.failed:
            msg = f"Downloaded {_plural(got)} from \"{label}\"."
        else:
            names = "; ".join(f"{t.get('title', '?')} ({why})" for t, why in result.failed[:2])
            more = f" and {len(result.failed) - 2} more" if len(result.failed) > 2 else ""
            msg = f"Downloaded {got} of {got + len(result.failed)} from \"{label}\". Failed: {names}{more}. Press D to retry those."
        if result.skipped and not result.cancelled:
            msg += f" ({result.skipped} already downloaded)"
        self.say(msg, bool(result.failed))
        self.refresh_tables()
        self.tick()

    def _confirm_stop(self) -> None:
        state = self.batch
        if state is None:
            return

        def answered(yes: Optional[bool]) -> None:
            if yes and self.batch is not None:
                self.batch["cancel"].set()
                self.say("Stopping after the song in progress...")

        self.push_screen(ConfirmPrompt(f"Stop downloading \"{state['label']}\"?\n{state['done']} of {state['total']} done.",
                                       yes_label="Stop"), answered)

    def _batch_summary(self) -> str:
        """For the top bar: '3/12' while a batch runs, else ''."""
        state = self.batch
        return f"{state['done']}/{state['total']}" if state else ""
