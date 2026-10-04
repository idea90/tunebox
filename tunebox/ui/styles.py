"""Stylesheet for the main app (Textual CSS)."""

APP_CSS = """
    Screen { background: $background; }
    Header { background: $primary; color: $background; }
    #main { width: 3fr; }
    #sidebar { width: 2fr; min-width: 36; max-width: 56; border-left: tall $panel; padding: 0 1; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0; }
    TrackTable { height: 1fr; background: $surface; }
    TrackTable > .datatable--header { background: $panel; color: $primary; text-style: bold; }
    TrackTable > .datatable--cursor { background: $primary 40%; color: $text; }
    #search-bar { height: 3; }
    #search-bar Input { width: 1fr; }
    #search-bar Select { width: 20; }
    #lib-bar { height: 1; margin: 0 0 1 0; }
    #lyrics-view { height: 1fr; padding: 1 2; background: $surface; }
    #mini-lyrics { height: 8; padding: 0 1; color: $text-muted; }
    .section { color: $primary; text-style: bold; margin-top: 1; }

    NowPlaying { height: auto; padding: 1 0; background: $surface; border: round $panel; }
    #np-title { text-style: bold; padding: 0 1; }
    #np-artist { color: $secondary; padding: 0 1; margin-bottom: 1; }
    SeekBar, VolumeBar { margin: 0 1; }
    .ctl { height: 1; margin: 1 1 0 1; }
    .chip { width: auto; height: 1; padding: 0 1; margin-right: 1; background: $panel; color: $text; }
    .chip:hover { background: $primary 60%; }
    .chip.on { background: $primary; color: $background; text-style: bold; }
    .chip.sel { background: $secondary; color: $background; text-style: bold; }
    #art-wrap { height: 12; margin: 0 1 1 1; align: center middle; }
    #art-img { height: 12; width: auto; }
    #d-title { text-style: bold; color: $primary; padding: 1 2 0 2; }
    #d-sub { color: $text-muted; padding: 0 2; }
    #d-actions { height: 1; margin: 1 2; }
    #d-albums-label { color: $primary; text-style: bold; padding: 1 2 0 2; }
    #t-detail { height: 3fr; }
    #t-detail-albums { height: 2fr; }
    #settings-box { padding: 1 2; }
    #s-usage, #s-account { color: $text-muted; margin-top: 1; }
    #settings-box .chip { margin: 0 0 1 0; }
    #settings-note { color: $text-muted; margin-top: 1; }
"""
