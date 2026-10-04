"""
Global media-key support (play/pause, next, previous, stop) via the optional `pynput` package.

    pip install "tunebox[media]"

Works on Windows and macOS, and on Linux under X11. Not available on Wayland or without
`pynput`; in that case start() returns False and the app simply runs without media keys.
The Linux desktop's now-playing widgets (MPRIS) are in core/mpris.py; Windows SMTC and macOS Now Playing are not implemented.
"""
import threading
from typing import Callable, Dict, Optional

_listener = None


def key_map(keys_module) -> Dict[object, str]:
    """pynput Key -> action name. Separate from start() so it can be tested without a keyboard."""
    K = keys_module.Key
    out = {}
    for attr, action in (("media_play_pause", "play_pause"), ("media_next", "next"),
                         ("media_previous", "prev"), ("media_stop", "stop")):
        key = getattr(K, attr, None)
        if key is not None:
            out[key] = action
    return out


def start(handler: Callable[[str], None]) -> bool:
    """Start listening in a background thread. `handler(action)` is called from that thread."""
    global _listener
    if _listener is not None:
        return True
    try:
        from pynput import keyboard
    except Exception:
        return False

    mapping = key_map(keyboard)

    def on_press(key):
        action = mapping.get(key)
        if action:
            try:
                handler(action)
            except Exception:
                pass

    try:
        _listener = keyboard.Listener(on_press=on_press)
        _listener.daemon = True
        _listener.start()
        return True
    except Exception:
        _listener = None
        return False


def stop() -> None:
    global _listener
    if _listener is not None:
        try:
            _listener.stop()
        except Exception:
            pass
        _listener = None
