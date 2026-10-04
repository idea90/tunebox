"""
Audio Stream Extractor & Downloader for Tunebox
"""
import os
import shutil
import threading
from functools import lru_cache
from glob import escape as glob_escape
import yt_dlp
import requests
from pathlib import Path
from typing import Dict, Any, Optional
from ..config import config, CACHE_DIR, DOWNLOADS_DIR
from .database import add_download

class _SilentLogger:
    """yt-dlp prints progress/warnings to stdout even with quiet=True, which would corrupt the TUI."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

# EBU R128 loudness target (-16 LUFS is the streaming-service norm) so songs play at similar volume.
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"

_QUIET = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": _SilentLogger()}

# One lock per video so a background prefetch and a foreground play of the same
# track share a single download instead of racing on the same files.
_download_locks: Dict[str, threading.Lock] = {}
_download_locks_guard = threading.Lock()

def _lock_for(video_id: str) -> threading.Lock:
    with _download_locks_guard:
        return _download_locks.setdefault(video_id, threading.Lock())

# Message from the most recent failed yt-dlp call, so the UI can show a real reason.
last_error: str = ""

def _set_error(msg: str) -> None:
    global last_error
    last_error = msg

def _clean_error(e: Exception) -> str:
    msg = str(e).replace("ERROR: ", "").strip().splitlines()
    return (msg[0] if msg else type(e).__name__)[:200]

def _safe_name(text: str) -> str:
    """Strip characters that are illegal in Windows/POSIX filenames."""
    illegal = '<>:"/\\|?*'
    cleaned = "".join("_" if c in illegal or ord(c) < 32 else c for c in text)
    return cleaned.strip(" .") or "Track"

def _fetch_cover(url: str) -> Optional[bytes]:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass
    return None


def write_tags(path: str, track: Dict[str, Any]) -> bool:
    """Embed title/artist/album and cover art. Tagging problems never fail a download."""
    title = track.get("title") or ""
    artist = track.get("artist") or ""
    album = track.get("album")
    album = album.get("name", "") if isinstance(album, dict) else (album or "")
    cover = _fetch_cover(track.get("thumbnail", ""))
    ext = Path(path).suffix.lower()

    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC
            tags = ID3()   # fresh tag: drops ffmpeg's container junk (major_brand, ...)
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artist))
            if album:
                tags.add(TALB(encoding=3, text=album))
            if cover:
                tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
            tags.save(path, v2_version=3)
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            f = MP4(path)
            f["\xa9nam"], f["\xa9ART"] = [title], [artist]
            if album:
                f["\xa9alb"] = [album]
            if cover:
                f["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
            f.save()
        elif ext == ".flac":
            from mutagen.flac import FLAC, Picture
            f = FLAC(path)
            f["title"], f["artist"] = [title], [artist]
            if album:
                f["album"] = [album]
            if cover:
                pic = Picture()
                pic.type, pic.mime, pic.data = 3, "image/jpeg", cover
                f.add_picture(pic)
            f.save()
        else:
            return False
        return True
    except Exception as e:
        _set_error(f"Saved, but could not write tags: {_clean_error(e)}")
        return False


@lru_cache(maxsize=1)
def get_ffmpeg_path() -> Optional[str]:
    """Find ffmpeg from imageio_ffmpeg or system PATH."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")

FORMATS = ("mp3", "m4a", "flac")


def download_format() -> str:
    fmt = str(config.get("download_format", "mp3")).lower()
    return fmt if fmt in FORMATS else "mp3"


def _cache_limit_bytes() -> int:
    try:
        return max(50, int(config.get("max_cache_size_mb", 2048))) * 1024 * 1024
    except (TypeError, ValueError):
        return 2048 * 1024 * 1024


def enforce_cache_limit(keep: int = 3) -> int:
    """Delete least-recently-used cached songs until the cache fits `max_cache_size_mb`.

    With `cache_enabled` false only the `keep` most recent songs (what's playing and up next) stay.
    Files in use can't be deleted on Windows; those are skipped. Returns how many files were removed.
    """
    try:
        files = sorted((p for p in CACHE_DIR.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    limit = _cache_limit_bytes()
    total, removed = 0, 0
    for i, f in enumerate(files):
        try:
            size = f.stat().st_size
        except OSError:
            continue
        over = (not config.get("cache_enabled", True) and i >= keep) or (i >= keep and total + size > limit)
        if over:
            try:
                f.unlink()
                removed += 1
                continue
            except OSError:
                pass                       # playing right now (locked) - keep it
        total += size
    return removed


def get_cached_track_path(video_id: str) -> Optional[str]:
    """Check if track exists in cache."""
    # pygame can only decode mp3/ogg/wav. With ffmpeg available every download is converted
    # to mp3, so a lingering .webm/.m4a is a half-finished download, not a usable cache hit.
    exts = ["mp3", "ogg", "wav"] if get_ffmpeg_path() else ["mp3", "ogg", "wav", "m4a", "opus", "webm"]
    for ext in exts:
        candidate = CACHE_DIR / f"{video_id}.{ext}"
        if candidate.exists() and candidate.stat().st_size > 10000:
            try:
                os.utime(candidate, None)
            except OSError:
                pass
            return str(candidate)
    return None

def cache_track_audio(track: Dict[str, Any], progress_hook=None) -> Optional[str]:
    """Download and cache track audio for seamless playback."""
    video_id = track.get("videoId") or track.get("id")
    if not video_id:
        return None

    with _lock_for(video_id):
        return _cache_track_audio_locked(track, video_id, progress_hook)


def _cache_track_audio_locked(track: Dict[str, Any], video_id: str, progress_hook=None) -> Optional[str]:
    cached = get_cached_track_path(video_id)
    if cached:
        return cached

    ffmpeg_exe = get_ffmpeg_path()
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = str(CACHE_DIR / f"{video_id}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        **_QUIET,
        "socket_timeout": 15,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "tv_embedded", "web"]
            }
        }
    }

    if ffmpeg_exe:
        ydl_opts["ffmpeg_location"] = ffmpeg_exe
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
        if config.get("normalize_volume", True):
            ydl_opts["postprocessor_args"] = {"extractaudio": ["-af", LOUDNORM]}

    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    cookie_file = config.get("cookie_file")
    if cookie_file and os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        path = get_cached_track_path(video_id)
        if not path:
            _set_error("Download finished but no audio file was produced.")
        else:
            enforce_cache_limit()
        return path
    except Exception as e:
        _set_error(_clean_error(e))
        return None

def _unique_stem(target_dir: Path, base: str, video_id: str) -> str:
    """'Artist - Title', unless a file with that name already belongs to a different song (e.g. the live
    version of a track you downloaded the studio version of). Then the video id is added to keep both."""
    from .database import get_download_owner
    existing = [p for p in target_dir.glob(f"{glob_escape(base)}.*") if p.suffix != ".part"]
    if not existing:
        return base
    owners = {get_download_owner(str(p)) for p in existing}
    if owners == {video_id}:
        return base                         # re-downloading the same song: reuse its name
    return f"{base} [{video_id}]"


def download_track_file(track: Dict[str, Any], output_dir: Optional[str] = None, fmt: str = "mp3", progress_hook=None) -> Optional[str]:
    """Download track to user library/downloads directory with metadata."""
    video_id = track.get("videoId") or track.get("id")
    if not video_id:
        return None

    target_dir = Path(output_dir) if output_dir else Path(config.get("download_dir", DOWNLOADS_DIR))
    target_dir.mkdir(parents=True, exist_ok=True)

    if fmt not in FORMATS:
        fmt = "mp3"
    title = _safe_name(track.get("title", "Track"))
    artist = _safe_name(track.get("artist", "Artist"))
    stem = _unique_stem(target_dir, f"{artist} - {title}", video_id)
    out_path = target_dir / f"{stem}.{fmt}"

    ffmpeg_exe = get_ffmpeg_path()
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = str(target_dir / f"{stem}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        **_QUIET,
        "socket_timeout": 20,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "web"]
            }
        }
    }

    if ffmpeg_exe:
        ydl_opts["ffmpeg_location"] = ffmpeg_exe
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": fmt,
            "preferredquality": "320" if config.get("audio_quality") == "high" else "192",
        }]
        if config.get("normalize_volume", True):
            ydl_opts["postprocessor_args"] = {"extractaudio": ["-af", LOUDNORM]}

    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    cookie_file = config.get("cookie_file")
    if cookie_file and os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        final_file = None
        if out_path.exists():
            final_file = str(out_path)
        else:  # no ffmpeg: yt-dlp keeps the source container (webm/m4a/...)
            leftovers = [p for p in target_dir.glob(f"{glob_escape(stem)}.*") if p.suffix != ".part"]
            if leftovers:
                final_file = str(leftovers[0])

        if final_file and os.path.exists(final_file):
            write_tags(final_file, track)
            size = os.path.getsize(final_file)
            add_download(track, final_file, size, Path(final_file).suffix.lstrip(".") or fmt)
            return final_file
        _set_error("Download finished but the output file was not found.")
        return None
    except Exception as e:
        _set_error(_clean_error(e))
        return None
