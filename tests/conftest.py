import os
import sys
import tempfile
from pathlib import Path

# Isolate config/db/cache in a temp dir BEFORE the package is imported.
os.environ["TUNEBOX_HOME"] = tempfile.mkdtemp(prefix="tunebox-test-")
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def isolate_side_effects(request, monkeypatch):
    """Keep UI tests hermetic: no session carry-over between tests, no global keyboard hooks.
    Tests that exercise these features opt back in with @pytest.mark.real_session / real_mediakeys."""
    from tunebox.core import session, mediakeys
    if "real_session" not in request.keywords:
        monkeypatch.setattr(session, "save", lambda player: True)
        monkeypatch.setattr(session, "restore", lambda player: False)
    if "real_mediakeys" not in request.keywords:
        monkeypatch.setattr(mediakeys, "start", lambda handler: False)


@pytest.fixture(autouse=True)
def isolate_personal_and_system(request, monkeypatch):
    """Never touch the real clipboard, never probe the terminal for graphics, and keep the shared
    test database's history out of the Home tests. Opt in with real_recs / real_clipboard / real_inline."""
    from tunebox.core import share, recommend
    from tunebox.ui import inline

    copied = []
    if "real_clipboard" not in request.keywords:
        monkeypatch.setattr(share, "copy_native", lambda text: copied.append(text) or "test-clipboard")
    request.node.copied = copied

    if "real_recs" not in request.keywords:
        monkeypatch.setattr(recommend, "local_shelves", lambda: [])
        monkeypatch.setattr(recommend, "seed_track", lambda: None)

    if "real_inline" not in request.keywords:
        monkeypatch.setitem(inline._state, "checked", True)
        monkeypatch.setitem(inline._state, "protocol", None)


@pytest.fixture(autouse=True)
def no_network_suggestions(monkeypatch):
    """The search box autocompletes as you type; never let that hit the network during tests."""
    from tunebox.core.ytmusic import yt_client
    monkeypatch.setattr(yt_client, "get_suggestions", lambda q: [])


@pytest.fixture(autouse=True)
def reset_player_flags():
    """Each test starts from an idle player (a previous test's background load must not leak in)."""
    from tunebox.core.player import player
    player.is_loading = False
    player.is_paused = False
    player.sleep_deadline = None
    yield
    player.is_loading = False
