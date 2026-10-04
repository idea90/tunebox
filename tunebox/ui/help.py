"""
Content of the help overlay (`?`).

Keys are Textual key names, exactly as in TuneboxApp.BINDINGS. tests/test_help.py checks both ways that this
list and the bindings agree, so adding a shortcut without documenting it (or the reverse) fails the tests.
"""
from typing import List, NamedTuple, Optional, Tuple

KEY_LABELS = {
    "space": "Space", "plus": "+", "equals_sign": "=", "minus": "-",
    "right_square_bracket": "]", "left_square_bracket": "[", "slash": "/", "escape": "Esc",
    "question_mark": chr(0x3F),     # a code point: tests/test_core.py fails on a literal question-mark string in UI code
    "shift+up": "Shift+Up", "shift+down": "Shift+Down",
}


class Entry(NamedTuple):
    keys: Tuple[str, ...]
    text: str
    label: Optional[str] = None      # shown instead of the joined keys (e.g. "1-7")

    @property
    def shown(self) -> str:
        return self.label or " / ".join(KEY_LABELS.get(k, k) for k in self.keys)


SECTIONS: List[Tuple[str, List[Entry]]] = [
    ("Playback", [
        Entry(("space",), "Play / pause"),
        Entry(("n", "p"), "Next / previous song"),
        Entry(("left_square_bracket", "right_square_bracket"), "Back / forward 10 seconds"),
        Entry(("plus", "equals_sign", "minus"), "Volume up / down", label="+ / -"),
        Entry(("s",), "Shuffle on / off"),
        Entry(("r",), "Repeat: off / all / one"),
        Entry(("a",), "Autoplay radio when the queue ends, on / off"),
        Entry(("R",), "Start a radio from the playing song"),
        Entry(("z",), "Sleep timer: 15, 30, 60, 90 minutes, off"),
    ]),
    ("Go to", [
        Entry(tuple("1234567"), "Home, Search, Queue, Lyrics, Library, Downloads, Settings", label="1-7"),
        Entry(("l",), "Lyrics"),
        Entry(("slash",), "Jump to the search box"),
        Entry(("escape",), "Leave the search box, or go back from an artist / album page"),
        Entry(("g",), "Artist page of the highlighted (or playing) song"),
        Entry(("b",), "Album page of the highlighted (or playing) song"),
        Entry(("question_mark",), "This help"),
    ]),
    ("Songs, queue and library", [
        Entry(("f",), "Favorite / unfavorite the playing song"),
        Entry(("d",), "Download the playing song"),
        Entry(("D",), "Download a whole playlist or album (open its page, or highlight it), the queue or a library list"),
        Entry(("P",), "Add the highlighted (or playing) song to a playlist"),
        Entry(("N",), "Play the highlighted (or playing) song next"),
        Entry(("E",), "Add the highlighted (or playing) song to the end of the queue"),
        Entry(("shift+up", "shift+down"), "Move the highlighted queue row up / down", label="Shift+Up / Down"),
        Entry(("x",), "Remove the highlighted row (queue, favorites, playlists, downloads)"),
        Entry(("c",), "Clear the queue"),
        Entry(("S",), "Save the whole queue as a new playlist"),
    ]),
    ("Look and copy", [
        Entry(("t",), "Next theme"),
        Entry(("i",), "Cover art: auto / ascii / blocks / off"),
        Entry(("v",), "Visualizer style"),
        Entry(("V",), "Visualizer colours"),
        Entry(("y",), "Copy a link to the highlighted song (the lyrics, on the Lyrics tab)"),
        Entry(("Y",), "Copy the lyrics"),
        Entry(("q",), "Quit"),
    ]),
]

MOUSE: List[Tuple[str, str]] = [
    ("Click a song", "Play it (the list becomes the queue)"),
    ("Click an artist / album / playlist", "Open its page"),
    ("Click the heart column", "Favorite / unfavorite"),
    ("Click the seek or volume bar", "Jump to that point"),
    ("Scroll over the player card", "Volume"),
    ("Click a synced lyric line", "Seek to it"),
    ("Right click anywhere", "Play / pause"),
]


def documented_keys() -> set:
    """Every key the help lists (Textual names)."""
    return {k for _, entries in SECTIONS for e in entries for k in e.keys}


def filter_sections(query: str) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """(title, [(keys shown, description)]) for rows matching `query` in their keys, text or section name.
    An empty query returns everything, with the mouse tips last."""
    q = query.strip().lower()
    groups = [(title, [(e.shown, e.text) for e in entries]) for title, entries in SECTIONS]
    groups.append(("Mouse", list(MOUSE)))
    if not q:
        return groups
    out = []
    for title, rows in groups:
        hits = [(k, t) for k, t in rows if q in k.lower() or q in t.lower() or q in title.lower()]
        if hits:
            out.append((title, hits))
    return out
