"""Tabs, focus, row clicks and 'what is selected' - where the user is and what they act on."""
from typing import Any, Dict, Optional

from textual import events
from textual.css.query import NoMatches
from textual.widgets import Input, Select, TabbedContent

from ...config import config
from ...core.player import player
from ..constants import COMPACT_TAB_NAMES, COMPACT_WIDTH
from ..screens import HelpScreen
from ..widgets import TrackTable, is_track


class NavigationMixin:
    """Tabs, focus, row clicks and 'what is selected' - where the user is and what they act on."""

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self.active_tab = event.pane.id
        self.tab_events += 1       # lets tests/tools know startup activation has been processed
        if self.active_tab == "home":
            self.home_items = self._compose_home()      # pick up what you played since last visit
            self._bg(self._refresh_because)
        self._sig = None
        self.refresh_tables()
        try:
            self._refresh_lyrics()
        except NoMatches:
            pass
        # Single owner of focus on tab change, so two callbacks can't race.
        if self.active_tab == "search":
            self.call_after_refresh(self._focus_search_box)
        else:
            self.call_after_refresh(self._focus_active_table, self.active_tab)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search-input" or not event.value.strip():
            return
        config.add_search(event.value)
        self.say(f"Searching for \"{event.value.strip()}\"...")
        self._search_seq += 1
        self._bg(self._do_search, event.value.strip(), self.query_one("#search-filter", Select).value, self._search_seq)

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.action_play_pause()

    def on_track_table_activated(self, event: TrackTable.Activated) -> None:
        table, row = event.table, event.row
        if not (0 <= row < len(table.items)):
            return
        item = table.items[row]
        typ = item.get("type", "song")
        if typ == "header":
            return

        if table.kind == "queue":
            self._bg(player.jump_to, row)
            return
        if table.kind == "upnext":
            idx = player.index_of(item)            # Up Next isn't always queue order (shuffle)
            if idx is not None:
                self._bg(player.jump_to, idx)
            return

        if event.column is not None and event.column == table.heart_col and is_track(item):
            self._toggle_favorite(item)
            return

        if is_track(item):
            tracks = [t for t in table.items if is_track(t)]
            idx = tracks.index(item)
            self._bg(player.play, None, tracks, idx)
        elif typ in ("album", "playlist", "artist") and item.get("browseId"):
            self.open_detail(typ, item)
        elif typ == "local_playlist":
            self._bg(self._play_local_playlist, item["id"])

    def action_detail_back(self) -> None:
        self.query_one(TabbedContent).active = self.detail_prev or "home"

    def _target_track(self) -> Optional[Dict[str, Any]]:
        """Highlighted song in the focused list, else the playing song."""
        table = self.focused if isinstance(self.focused, TrackTable) else self.active_table()
        item = table.selected_item() if table else None
        return item if is_track(item) else player.current_track

    def _target_item(self) -> Optional[Dict[str, Any]]:
        """Highlighted row of any kind (song, album, artist...), else the playing song."""
        table = self.focused if isinstance(self.focused, TrackTable) else self.active_table()
        item = table.selected_item() if table else None
        if item and item.get("type") != "header":
            return item
        return player.current_track

    def action_go_artist(self) -> None:
        track = self._target_track()
        artists = (track or {}).get("artists") or []
        artist = artists[0] if artists and isinstance(artists[0], dict) else {}
        if artist.get("id"):
            self.open_detail("artist", {"browseId": artist["id"], "title": artist.get("name", "Artist")})
        else:
            self.say("No artist page available for this song.", True)

    def action_go_album(self) -> None:
        track = self._target_track()
        album = (track or {}).get("album")
        if isinstance(album, dict) and album.get("id"):
            self.open_detail("album", {"browseId": album["id"], "title": album.get("name", "Album")})
        else:
            self.say("No album page available for this song.", True)

    def active_table(self) -> Optional[TrackTable]:
        table_id = {"home": "t-home", "search": "t-search", "queue": "t-queue",
                    "library": "t-library", "downloads": "t-downloads", "detail": "t-detail"}.get(self.active_tab)
        return self.query_one(f"#{table_id}", TrackTable) if table_id else None

    TAB_NAMES = {"home": "Home", "search": "Search", "queue": "Queue", "lyrics": "Lyrics", "library": "Library",
                 "downloads": "Downloads", "settings": "Settings", "detail": "Details"}
    NUMBERED_TABS_MIN_WIDTH = 130      # narrower terminals drop the "1 " ... "8 " so the whole tab strip fits

    def _relabel_tabs(self) -> None:
        """Tab titles with their shortcut digit when there is room, plain names when there is not, short names
        on a phone."""
        numbered = self.size.width >= self.NUMBERED_TABS_MIN_WIDTH
        try:
            tabs = self.query_one(TabbedContent)
            for i, (tab_id, name) in enumerate(self.TAB_NAMES.items(), 1):
                if self.size.width < COMPACT_WIDTH:
                    name = COMPACT_TAB_NAMES[tab_id]
                label = f"{i} {name}" if numbered else name
                tab = tabs.get_tab(tab_id)
                if str(tab.label) != label:
                    tab.label = label
        except Exception:
            pass                       # not mounted yet, or already closing

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout()
        self._relabel_tabs()

    def _apply_layout(self) -> None:
        """Wide terminals: lists on the left, player on the right. Narrow ones (a phone held upright): the lists
        with a mini player under them, and the full player as its own screen you open with `o` or a tap."""
        compact = self.size.width < COMPACT_WIDTH
        self.compact = compact
        self.set_class(compact, "compact")
        if not compact:
            self.player_view = False
        try:
            self.query_one("#main").display = not (compact and self.player_view)
            self.query_one("#sidebar").display = (not compact) or self.player_view
            self.query_one("#mini").display = compact and not self.player_view
            self.query_one("#close-player").display = compact
        except NoMatches:
            pass                       # not mounted yet, or already closing

    def action_toggle_player(self) -> None:
        """Small screens: open the full player, or go back to the list."""
        if not self.compact:
            return
        self.player_view = not self.player_view
        self._apply_layout()
        self.tick()
        if not self.player_view:
            self._focus_active_table()

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())

    def action_tab(self, name: str) -> None:
        self.query_one(TabbedContent).active = name

    def action_focus_search(self) -> None:
        tabs = self.query_one(TabbedContent)
        if tabs.active == "search":
            self.query_one("#search-input", Input).focus()
        else:
            tabs.active = "search"   # the tab-activated handler focuses the box

    def action_focus_table(self) -> None:
        """Escape: close the full player (small screens), leave the search box, or go back from a detail page."""
        if self.compact and self.player_view:
            self.action_toggle_player()
            return
        if self.active_tab == "detail" and isinstance(self.focused, TrackTable):
            self.action_detail_back()
            return
        self._focus_active_table()

    def _focus_search_box(self) -> None:
        if self.query_one(TabbedContent).active == "search":   # same stale-focus guard as _focus_active_table
            self.query_one("#search-input", Input).focus()

    def _focus_active_table(self, expected_tab: Optional[str] = None) -> None:
        """Move focus to the current tab's list. Pure focus change: must never navigate.

        `expected_tab` guards deferred calls: focusing a widget makes Textual activate its tab, so a call
        scheduled for tab A that lands after the user already switched to B would drag them back to A.
        """
        # Compare with TabbedContent.active (changes instantly), not self.active_tab (updated later by an event).
        if expected_tab is not None and expected_tab != self.query_one(TabbedContent).active:
            return
        table = self.active_table()
        if table:
            table.focus()
        else:
            self.set_focus(None)

    def action_lib(self, name: str) -> None:
        self.library_sub = name
        self.refresh_tables()
        self._refresh_now_playing()
