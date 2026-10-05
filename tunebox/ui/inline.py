"""
Real-pixel cover art through the terminal's graphics protocol, when it has one.

    kitty  - kitty, Ghostty (Terminal Graphics Protocol)
    sixel  - WezTerm, Windows Terminal 1.22+, iTerm2, foot, Konsole, mlterm, ...

Anything else (or the `textual-image` package missing) reports None, and the app uses its own
ASCII / half-block art instead. Detection asks the terminal a question, which is only possible
before the Textual app starts, so `detect()` is called from `run()` and the answer is cached.

Set TUNEBOX_NO_GRAPHICS=1 to skip detection entirely.
"""
import os
from typing import Any, Optional

from .. import termux

_state = {"checked": False, "protocol": None}


def detect() -> Optional[str]:
    """'kitty', 'sixel' or None. Cached; call once before the app starts."""
    if _state["checked"]:
        return _state["protocol"]
    _state["checked"] = True
    if os.environ.get("TUNEBOX_NO_GRAPHICS") or os.environ.get("METROLIST_NO_GRAPHICS"):   # old name still honoured
        return None
    if termux.is_termux():             # no graphics protocol there; asking would only slow startup
        return None
    try:
        # textual-image declares pillow>=10.3 but calls Image.get_flattened_data (Pillow 12.1+). With an older
        # Pillow every paint would crash, so treat that as "no graphics" and use the text art instead.
        from PIL import Image as PILImage
        if not hasattr(PILImage.Image, "get_flattened_data"):
            return None
        from textual_image.renderable import Image, SixelImage, TGPImage
        if Image is TGPImage:
            _state["protocol"] = "kitty"
        elif Image is SixelImage:
            _state["protocol"] = "sixel"
    except Exception:
        _state["protocol"] = None
    return _state["protocol"]


def graphics_protocol() -> Optional[str]:
    return detect()


def resolve_style(style: str) -> str:
    """Turn the saved setting into what is actually drawn: image | ascii | blocks | off."""
    if style == "auto":
        return "image" if graphics_protocol() else "ascii"
    if style == "image" and not graphics_protocol():
        return "ascii"
    return style if style in ("ascii", "blocks", "off", "image") else "ascii"


def make_image_widget(**kwargs: Any):
    """The terminal-graphics Image widget, or None when the terminal can't show images."""
    if not graphics_protocol():
        return None
    try:
        from textual_image.widget import Image
        return Image(**kwargs)
    except Exception:
        return None
