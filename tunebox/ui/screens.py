"""Modal screens."""
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from ..core.database import create_playlist, get_playlists


class PlaylistPicker(ModalScreen[Optional[str]]):
    """Pick an existing playlist or type a new name; dismisses with a playlist id."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    PlaylistPicker { align: center middle; }
    #picker { width: 56; height: auto; max-height: 24; background: $panel; border: round $primary; padding: 1 2; }
    #picker OptionList { height: auto; max-height: 12; margin: 1 0; }
    """

    def __init__(self, track_title: str):
        super().__init__()
        self.track_title = track_title

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label(Text(f"Add \"{self.track_title}\" to a playlist", style="bold"))
            plist = get_playlists()
            if plist:
                yield OptionList(*[Option(f"{p['title']}  ({p.get('trackCount', 0)})", id=p["id"]) for p in plist])
            yield Input(placeholder="...or type a new playlist name and press Enter", id="new-name")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if name:
            self.dismiss(create_playlist(name)["id"])

    def action_cancel(self) -> None:
        self.dismiss(None)
