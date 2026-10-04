"""Stylesheet for the main app (Textual CSS).

Look: a quiet dark canvas where colour is reserved for what matters (the playing song, the active tab, the
selected row, hearts); the sidebar is a lighter "card" so the player always reads as its own area."""

APP_CSS = """
    Screen { background: $background; }

    /* ---------- chrome ---------- */
    #topbar { height: 1; background: $surface; padding: 0 2; }
    Footer { background: $surface; }
    Footer .footer-key--key { color: $primary; background: transparent; text-style: bold; }
    Footer .footer-key--description { color: $text-muted; }

    * {
        scrollbar-size-vertical: 1;
        scrollbar-background: $background;
        scrollbar-background-hover: $background;
        scrollbar-background-active: $background;
        scrollbar-color: $panel;
        scrollbar-color-hover: $primary 60%;
        scrollbar-color-active: $primary;
    }

    /* ---------- layout ---------- */
    #main { width: 3fr; padding: 0 1; }
    #sidebar { width: 2fr; min-width: 34; max-width: 58; background: $surface; padding: 1 2 0 2; }
    TabbedContent { height: 1fr; }
    TabbedContent ContentSwitcher { background: $background; }
    TabPane { padding: 0; }

    Tabs { background: $background; }
    Tab { color: $text-muted; padding: 0 1; background: $background; }
    Tab:hover { color: $text; background: $background; }
    Tab.-active { color: $primary; text-style: bold; background: $background; }
    Tabs .underline--bar { color: $primary; background: $panel; }

    /* ---------- lists ---------- */
    TrackTable { height: 1fr; background: $background; margin-top: 1; }
    TrackTable > .datatable--header { background: $background; color: $text-muted; text-style: bold; }
    TrackTable > .datatable--odd-row { background: $surface 55%; }
    TrackTable > .datatable--even-row { background: $background; }
    TrackTable > .datatable--cursor { background: $primary 20%; color: $text; text-style: bold; }
    TrackTable:focus > .datatable--cursor { background: $primary 45%; color: $text; text-style: bold; }
    TrackTable > .datatable--hover { background: $primary 12%; }
    #t-upnext { height: 1fr; min-height: 4; background: $surface; margin-top: 0; }
    #t-upnext > .datatable--odd-row { background: $surface; }
    #t-upnext > .datatable--even-row { background: $surface; }
    #t-upnext > .datatable--hover { background: $primary 15%; }

    #search-bar { height: 3; margin-top: 1; }
    #search-bar Input { width: 1fr; }
    #search-bar Select { width: 20; margin-left: 1; }
    #t-search { margin-top: 0; }
    #lib-bar { height: 1; margin: 1 0 0 0; }
    #t-library { margin-top: 1; }

    .empty { height: 1fr; content-align: center middle; text-align: center; color: $text-muted; }
    #lib-bar { margin-bottom: 0; }

    /* ---------- chips (buttons) ---------- */
    .chip { width: auto; height: 1; padding: 0 2; margin-right: 1; background: $panel; color: $text; }
    .chip:hover { background: $primary 55%; }
    .chip.on { background: $primary; color: $background; text-style: bold; }
    .chip.sel { background: $primary; color: $background; text-style: bold; }

    /* ---------- player card (sidebar) ---------- */
    NowPlaying { height: auto; padding: 0; background: $surface; }
    #art-wrap { height: 12; margin: 0 0 1 0; align: center middle; }
    #art-img { height: 12; width: auto; }
    ArtView { margin: 0 0 1 0; }
    Visualizer { margin: 0 0 1 0; }
    #np-title { text-style: bold; color: $text; width: 100%; content-align: center middle; }
    #np-artist { color: $text-muted; width: 100%; content-align: center middle; }
    #np-status { color: $primary; width: 100%; content-align: center middle; margin-bottom: 1; }
    SeekBar, VolumeBar { margin: 0; }
    .ctl { height: 1; margin: 1 0 0 0; align: center middle; }
    .ctl Chip { margin: 0 1; }
    #c-play { padding: 0 3; }
    VolumeBar { margin-top: 1; }

    .section { color: $text-muted; text-style: bold; margin-top: 1; }
    #mini-lyrics-title { color: $text-muted; text-style: bold; margin-top: 1; }
    #mini-lyrics { height: 7; padding: 0; color: $text-muted; }

    /* ---------- lyrics tab ---------- */
    #lyrics-view { height: 1fr; padding: 1 4; background: $background; }

    /* ---------- detail page ---------- */
    #d-title { text-style: bold; color: $text; padding: 1 1 0 1; }
    #d-sub { color: $text-muted; padding: 0 1; }
    #d-actions { height: 1; margin: 1 1 0 1; }
    #d-albums-label { color: $text-muted; text-style: bold; padding: 1 1 0 1; }
    #t-detail { height: 3fr; }
    #t-detail-albums { height: 2fr; }

    /* ---------- settings ---------- */
    #settings-box { padding: 0 1; }
    .settings-title { color: $text-muted; text-style: bold; margin: 1 0 0 0; }
    .settings-grid { grid-size: 2; grid-gutter: 1 2; grid-columns: 1fr 1fr; grid-rows: 3; height: auto; margin: 1 0 0 0; }
    Chip.setting { width: 100%; height: 3; margin: 0; padding: 0 2; background: $surface; color: $text;
               content-align: left middle; border-left: thick $panel; }
    Chip.setting:hover { background: $panel; border-left: thick $primary; }
    #s-usage, #s-account { color: $text-muted; margin-top: 1; }
    #settings-note { color: $text-muted; margin-top: 1; }
"""
