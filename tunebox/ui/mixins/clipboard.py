"""Copy a link / lyrics to the clipboard (system tool first, terminal OSC 52 as fallback)."""
from typing import Optional

from ...core import share
from ...core.player import player


class ClipboardMixin:
    """Copy a link / lyrics to the clipboard (system tool first, terminal OSC 52 as fallback)."""

    def action_copy_smart(self) -> None:
        """y: the lyrics when you're on the Lyrics tab, otherwise a link to the highlighted/playing item."""
        if self.active_tab == "lyrics":
            self.action_copy_lyrics()
        else:
            self.action_copy_link()

    def action_copy_link(self) -> None:
        link = share.share_link(self._target_item())
        if link:
            self._copy(link, "link")
        else:
            self.say("Nothing shareable here (local playlists have no link).", True)

    def action_copy_lyrics(self) -> None:
        tr = player.current_track
        text = share.lyrics_to_text(self.lyrics, (tr or {}).get("title", ""), (tr or {}).get("artist", "")) if tr else None
        if text:
            self._copy(text, f"lyrics ({len(text.splitlines()) - 2} lines)")
        else:
            self.say("No lyrics to copy for this song.", True)

    def _copy(self, text: str, what: str) -> None:
        def work():
            method = share.copy_native(text)
            self._ui(self._copy_done, text, what, method)
        self._bg(work)

    def _copy_done(self, text: str, what: str, method: Optional[str]) -> None:
        if method:
            self.say(f"Copied {what} to the clipboard.")
            return
        try:        # no system clipboard tool: ask the terminal (OSC 52). It can't confirm, so say so.
            self.copy_to_clipboard(text)
            self.say(f"Copied {what} via the terminal. If pasting gives nothing, your terminal blocks clipboard access.")
        except Exception:
            self.say("Could not access the clipboard.", True)
