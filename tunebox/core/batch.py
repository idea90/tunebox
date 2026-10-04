"""
Download many songs in one go (a whole playlist, an album, the queue, your favorites...).

    plan(tracks, fmt)                      what would be downloaded: new songs, how many are already on disk
    download_tracks(tracks, fmt, ...)      download them one after another, with progress and a way to stop

Songs are fetched one at a time, politely, through `downloader.download_track_file` (so tags, cover art,
loudness normalization and the Downloads index all work exactly as for a single `d`). Songs already
downloaded in the same format are skipped, so re-running after a failure or a stop only does what is left.
"""
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import config, DOWNLOADS_DIR
from . import downloader
from .database import get_downloads

# on_progress(event, done, total, track, detail):  event is "start" | "done" | "skipped" | "failed".
# `done` counts songs finished so far (downloaded, skipped or failed), including this one for non-"start" events.
Progress = Callable[[str, int, int, Dict[str, Any], str], None]


@dataclass
class BatchResult:
    total: int = 0                                               # songs in the batch (unique, with an id)
    downloaded: List[str] = field(default_factory=list)          # files written
    skipped: int = 0                                             # already downloaded in this format
    unavailable: int = 0                                         # entries with no video id (removed / private)
    failed: List[Tuple[Dict[str, Any], str]] = field(default_factory=list)   # (track, reason)
    cancelled: bool = False

    @property
    def finished(self) -> int:
        return len(self.downloaded) + self.skipped + len(self.failed)


def _video_id(track: Dict[str, Any]) -> Optional[str]:
    return track.get("videoId") or track.get("id")


def plan(tracks: List[Dict[str, Any]], fmt: str) -> Tuple[List[Dict[str, Any]], int, int]:
    """(songs still to download, songs already on disk in `fmt`, entries without an id). Duplicates count once."""
    have = {d["videoId"] for d in get_downloads() if d.get("format") == fmt}
    todo: List[Dict[str, Any]] = []
    seen = set()
    skipped = unavailable = 0
    for track in tracks:
        vid = _video_id(track)
        if not vid:
            unavailable += 1
        elif vid not in seen:
            seen.add(vid)
            if vid in have:
                skipped += 1
            else:
                todo.append(track)
    return todo, skipped, unavailable


def folder_for(name: Optional[str]) -> Optional[str]:
    """Folder (inside the downloads directory) for a playlist / album, or None to download flat."""
    if not name or not config.get("download_playlist_folders", True):
        return None
    return downloader._safe_name(name)


def download_tracks(tracks: List[Dict[str, Any]], fmt: Optional[str] = None, subfolder: Optional[str] = None,
                    on_progress: Optional[Progress] = None, cancel: Optional[threading.Event] = None) -> BatchResult:
    """Download every song not already on disk. Blocks; run it in a worker. Set `cancel` to stop after the
    song in progress. A failed song never stops the rest: it is listed in the result."""
    fmt = fmt if fmt in downloader.FORMATS else downloader.download_format()
    todo, skipped, unavailable = plan(tracks, fmt)
    result = BatchResult(total=len(todo) + skipped, skipped=skipped, unavailable=unavailable)
    folder = folder_for(subfolder)
    out_dir = str(Path(config.get("download_dir", DOWNLOADS_DIR)) / folder) if folder else None

    def tell(event: str, track: Dict[str, Any], detail: str = "") -> None:
        if on_progress:
            try:
                on_progress(event, result.finished, result.total, track, detail)
            except Exception:
                pass                                 # a broken progress display must not stop the downloads

    for track in todo:
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break
        tell("start", track)
        try:
            path = downloader.download_track_file(track, output_dir=out_dir, fmt=fmt)
            reason = "" if path else (downloader.last_error or "unknown error")
        except Exception as e:                       # download_track_file handles its own errors; belt and braces
            path, reason = None, f"{type(e).__name__}: {e}"[:200]
        if path:
            result.downloaded.append(path)
            tell("done", track, path)
        else:
            result.failed.append((track, reason))
            tell("failed", track, reason)
    return result
