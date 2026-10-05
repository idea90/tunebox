"""
Termux (Android) helpers. Everything here is a no-op anywhere else.

    is_termux()          running inside Termux?
    music_dir()          ~/storage/shared/Music/Tunebox once `termux-setup-storage` has been run, else None
    wake_lock(on)        keep the phone awake while music plays (needs the Termux:API app + `pkg install termux-api`)
    clipboard_set(text)  copy via termux-clipboard-set (same requirement)

Kept free of imports from the rest of the package so config.py can use it.
"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

_wake_locked = False


def is_termux() -> bool:
    """True inside Termux. TERMUX_VERSION is set there; PREFIX points into com.termux as a fallback."""
    return bool(os.environ.get("TERMUX_VERSION")) or "com.termux" in os.environ.get("PREFIX", "")


def music_dir() -> Optional[Path]:
    """Where downloads should go so Android's music apps and file manager can see them, or None.

    `~/storage/shared` only exists after `termux-setup-storage` has been run once and allowed.
    """
    shared = Path.home() / "storage" / "shared"
    return shared / "Music" / "Tunebox" if is_termux() and shared.is_dir() else None


def _run(cmd, text: Optional[str] = None) -> bool:
    try:
        proc = subprocess.run(cmd, input=None if text is None else text.encode("utf-8"),
                              capture_output=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def wake_lock(on: bool) -> bool:
    """Hold / release Android's wake lock. Without it Android freezes Termux (and the music) when the screen turns
    off. Only acts when the state changes. Returns whether the lock is held now."""
    global _wake_locked
    if not is_termux() or on == _wake_locked:
        return _wake_locked
    tool = shutil.which("termux-wake-lock" if on else "termux-wake-unlock")
    if tool and _run([tool]):
        _wake_locked = on
    return _wake_locked


def clipboard_set(text: str) -> bool:
    tool = shutil.which("termux-clipboard-set") if is_termux() else None
    return bool(tool) and _run([tool], text)
