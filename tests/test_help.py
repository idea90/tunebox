"""Help overlay (`?`) and 'save queue as playlist' (`S`)."""
import pytest

from tunebox.core.database import get_playlist, get_playlists
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client
from tunebox.ui import help as helpmod
from tunebox.ui.app import TuneboxApp
from tunebox.ui.screens import HelpScreen, NamePrompt


def song(i):
    return {"videoId": f"q{i}", "title": f"Queued {i}", "artist": "A", "artists": [{"name": "A", "id": None}],
            "album": None, "thumbnail": "", "duration": "3:00", "duration_seconds": 180, "type": "song"}


async def wait_for(pilot, cond, timeout=5.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.05)
        waited += 0.05
    return cond()


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(yt_client, "get_home_feed", lambda: [])
    monkeypatch.setattr(player, "_trigger_prefetch", lambda: None)


# ------------------------------------------------------------------ help content

def test_help_documents_every_binding_and_nothing_else():
    """Adding a shortcut without a help line (or leaving a stale line behind) must fail here."""
    bound = {k for b in TuneboxApp.BINDINGS for k in b.key.split(",")}
    documented = helpmod.documented_keys()
    assert bound - documented == set(), "shortcuts missing from ui/help.py"
    assert documented - bound == set(), "ui/help.py documents keys that are not bound"


def test_filter_matches_keys_text_and_section_names():
    def rows(q):
        return [(k, t) for _, rs in helpmod.filter_sections(q) for k, t in rs]

    assert ("S", "Save the whole queue as a new playlist") in rows("playlist")
    assert any(k == "Space" for k, _ in rows("pause"))
    assert [k for k, _ in rows("shift+up")] == ["Shift+Up / Down"]
    assert {title for title, _ in helpmod.filter_sections("mouse")} == {"Mouse"}
    assert helpmod.filter_sections("zzzz-nothing") == []
    assert helpmod.filter_sections("") == helpmod.filter_sections("   ")
    assert helpmod.filter_sections("")[-1][0] == "Mouse"


# ------------------------------------------------------------------ help screen

@pytest.mark.asyncio
async def test_question_mark_opens_help_and_escape_closes_it():
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("question_mark")
        assert await wait_for(pilot, lambda: isinstance(app.screen, HelpScreen))
        assert app.focused.id == "help-filter"

        await pilot.press("question_mark")                      # typed into the box: no second overlay, no close
        assert isinstance(app.screen, HelpScreen) and len(app.screen_stack) == 2
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: not isinstance(app.screen, HelpScreen))


@pytest.mark.asyncio
async def test_typing_filters_the_help_list():
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("question_mark")
        assert await wait_for(pilot, lambda: isinstance(app.screen, HelpScreen))
        body = app.screen.query_one("#help-body")

        def shown():
            from rich.console import Console
            console = Console(width=100, record=True, file=open("/dev/null", "w"))
            console.print(getattr(body, "content", None) or body.renderable)
            return console.export_text()

        assert "Sleep timer" in shown() and "Save the whole queue" in shown()
        await pilot.press(*"sleep")
        assert await wait_for(pilot, lambda: "Save the whole queue" not in shown())
        assert "Sleep timer" in shown()
        await pilot.press(*"zzzz")
        assert await wait_for(pilot, lambda: "No shortcut matches" in shown())


# ------------------------------------------------------------------ save queue

@pytest.mark.asyncio
async def test_S_saves_the_queue_as_a_playlist_in_order(monkeypatch):
    monkeypatch.setattr(player, "queue", [song(1), song(2), song(3)], raising=False)
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("S")
        assert await wait_for(pilot, lambda: isinstance(app.screen, NamePrompt))
        assert app.screen.query_one("#name-input").value.startswith("Queue ")      # a ready-made name

        await pilot.press(*"Friday mix", "enter")               # typing replaces the selected suggestion
        assert await wait_for(pilot, lambda: any(p["title"] == "Friday mix" for p in get_playlists()))
        pl = next(p for p in get_playlists() if p["title"] == "Friday mix")
        assert [t["videoId"] for t in get_playlist(pl["id"])["tracks"]] == ["q1", "q2", "q3"]
        assert not isinstance(app.screen, NamePrompt)


@pytest.mark.asyncio
async def test_S_with_an_empty_queue_says_so_and_asks_nothing(monkeypatch):
    monkeypatch.setattr(player, "queue", [], raising=False)
    before = len(get_playlists())
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("S")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, NamePrompt)
    assert len(get_playlists()) == before


@pytest.mark.asyncio
async def test_escape_cancels_saving_the_queue(monkeypatch):
    monkeypatch.setattr(player, "queue", [song(1)], raising=False)
    before = len(get_playlists())
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("S")
        assert await wait_for(pilot, lambda: isinstance(app.screen, NamePrompt))
        await pilot.press(*"nope", "escape")
        assert await wait_for(pilot, lambda: not isinstance(app.screen, NamePrompt))
        await pilot.pause(0.2)
    assert len(get_playlists()) == before


@pytest.mark.asyncio
async def test_a_blank_name_is_not_accepted(monkeypatch):
    monkeypatch.setattr(player, "queue", [song(1)], raising=False)
    before = len(get_playlists())
    app = TuneboxApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("S")
        assert await wait_for(pilot, lambda: isinstance(app.screen, NamePrompt))
        app.screen.query_one("#name-input").value = "   "
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert isinstance(app.screen, NamePrompt)               # still asking
    assert len(get_playlists()) == before
