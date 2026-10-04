"""Modal screens."""
from typing import Optional

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ..core.database import create_playlist, get_playlists
from .help import filter_sections


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


class NamePrompt(ModalScreen[Optional[str]]):
    """Ask for a name; dismisses with the trimmed text (Enter) or None (Esc)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    NamePrompt { align: center middle; }
    #name-prompt { width: 56; height: auto; background: $panel; border: round $primary; padding: 1 2; }
    #name-prompt Input { margin: 1 0 0 0; }
    """

    def __init__(self, prompt: str, default: str = ""):
        super().__init__()
        self.prompt, self.default = prompt, default

    def compose(self) -> ComposeResult:
        with Vertical(id="name-prompt"):
            yield Label(Text(self.prompt, style="bold"))
            yield Input(value=self.default, placeholder="Name, then Enter  (Esc cancels)", id="name-input")

    def on_mount(self) -> None:
        box = self.query_one("#name-input", Input)
        box.focus()
        select_all = getattr(box, "select_all", None)         # replace the suggestion by just typing
        if select_all:
            select_all()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if name:
            self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Every shortcut, with a filter box. Esc closes."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("down", "scroll(3)", "Scroll", show=False),
        Binding("up", "scroll(-3)", "Scroll", show=False),
        Binding("pagedown", "scroll(15)", "Scroll", show=False),
        Binding("pageup", "scroll(-15)", "Scroll", show=False),
    ]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help { width: 86; max-width: 95%; height: 85%; background: $panel; border: round $primary; padding: 1 2; }
    #help-title { text-style: bold; }
    #help-filter { margin: 1 0; }
    #help-scroll { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Label("Keyboard shortcuts   (type to filter, Up/Down to scroll, Esc to close)", id="help-title")
            yield Input(placeholder="Filter, e.g. queue, volume, S", id="help-filter")
            with VerticalScroll(id="help-scroll"):
                yield Static(self.render_help(""), id="help-body")

    @staticmethod
    def render_help(query: str):
        groups = filter_sections(query)
        if not groups:
            return Text(f"No shortcut matches \"{query.strip()}\".", style="grey50")
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold", no_wrap=True)
        table.add_column()
        for i, (title, rows) in enumerate(groups):
            if i:
                table.add_row("", "")
            table.add_row(Text(title, style="bold underline"), "")
            for keys, text in rows:
                table.add_row(Text(keys, style="bold"), Text(text))
        return table

    def on_mount(self) -> None:
        self.query_one("#help-filter", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#help-body", Static).update(self.render_help(event.value))
        self.query_one("#help-scroll", VerticalScroll).scroll_home(animate=False)

    def action_scroll(self, rows: int) -> None:
        self.query_one("#help-scroll", VerticalScroll).scroll_relative(y=rows, animate=False)

    def action_close(self) -> None:
        self.dismiss(None)
