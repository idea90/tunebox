"""
Sharing helpers: song/album/artist links, clean lyrics text, and clipboard access.

Clipboard strategy (first that works wins):
  1. the operating system's own clipboard (Windows API, pbcopy, wl-copy, xclip, xsel)
  2. OSC 52, an escape sequence most modern terminals honour, which also works over SSH.
     It cannot be confirmed from here, so callers are told when it was the only option.
"""
import ctypes
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

from .. import termux

MUSIC_URL = "https://music.youtube.com"


def share_link(item: Optional[Dict[str, Any]]) -> Optional[str]:
    """Public link for a song, album, playlist or artist item. None if it has no usable id."""
    if not item:
        return None
    kind = item.get("type", "song")
    if item.get("videoId") and kind not in ("album", "playlist", "artist", "local_playlist"):
        return f"{MUSIC_URL}/watch?v={item['videoId']}"
    bid = item.get("browseId") or item.get("id")
    if not bid or kind == "local_playlist":
        return None
    if kind == "playlist":
        return f"{MUSIC_URL}/playlist?list={bid[2:] if bid.startswith('VL') else bid}"
    if kind == "artist":
        return f"{MUSIC_URL}/channel/{bid}"
    return f"{MUSIC_URL}/browse/{bid}"


def lyrics_to_text(lyrics: Dict[str, Any], title: str = "", artist: str = "") -> Optional[str]:
    """Plain lyrics (no timestamps), with a title line. None when there are no real lyrics to copy."""
    if not lyrics or lyrics.get("source") in (None, "", "None"):
        return None
    lines = [str(l.get("text", "")).strip() for l in lyrics.get("lines", [])]
    lines = [l for l in lines if l]
    if not lines:
        return None
    header = " - ".join(x for x in (title, artist) if x)
    return (header + "\n\n" if header else "") + "\n".join(lines)


# ---------------------------------------------------------------- clipboard

def _copy_windows(text: str) -> bool:
    """Win32 clipboard, Unicode-safe (the `clip` command mangles non-ASCII)."""
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return False
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
    finally:
        user32.CloseClipboard()


def _copy_with(cmd, text: str) -> bool:
    try:
        proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def copy_native(text: str) -> Optional[str]:
    """Copy via the OS. Returns the method used, or None if no native clipboard worked."""
    try:
        if sys.platform == "win32":
            return "windows" if _copy_windows(text) else None
        if sys.platform == "darwin":
            return "pbcopy" if _copy_with(["pbcopy"], text) else None
        if termux.clipboard_set(text):
            return "termux-clipboard"
        for name, cmd in (("wl-copy", ["wl-copy"]),
                          ("xclip", ["xclip", "-selection", "clipboard"]),
                          ("xsel", ["xsel", "--clipboard", "--input"])):
            if shutil.which(cmd[0]) and _copy_with(cmd, text):
                return name
    except Exception:
        pass
    return None
