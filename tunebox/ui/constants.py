"""Names shared by the app and its mixins."""

TABS = ["home", "search", "queue", "lyrics", "library", "downloads", "settings"]
LIB_TABS = ["favorites", "playlists", "history", "most_played", "yt_liked", "yt_playlists"]
SLEEP_STEPS = [0, 15, 30, 60, 90]   # minutes; 0 = off
COMPACT_WIDTH = 80                  # narrower terminals (a phone held upright) get the one-column layout
COMPACT_TAB_NAMES = {"home": "Home", "search": "Find", "queue": "Queue", "lyrics": "Lyrics", "library": "Lib",
                     "downloads": "Saved", "settings": "Setup", "detail": "Page"}
# Rows above the first lyric line: 1 padding + source label + blank line. Used to map clicks to lines.
LYRICS_TOP_ROWS = 3
SEARCH_FILTERS = [("All", "all"), ("Songs", "songs"), ("Albums", "albums"),
                  ("Artists", "artists"), ("Playlists", "playlists"), ("Videos", "videos")]
