"""
TuneboxApp is assembled from these mixins, one per feature area, so each file stays small:

    helpers     thread plumbing (_ui, _bg), toasts, seek
    data        Home / search / Library / detail-page loading
    downloads   download a whole list (playlist, album, queue...)
    refresh     the periodic UI refresh (now playing, tables, settings chips)
    lyrics      live lyrics
    art         cover art + the sidebar row budget
    viz         spectrum visualizer
    navigation  tabs, focus, row clicks, current selection
    playback    transport and listening actions
    library     favorites, playlists, queue editing
    clipboard   copy link / lyrics
    settings    Settings-tab actions
    remote      desktop media controls (MPRIS)

They share state through `self` (set up in TuneboxApp.__init__) and call each other freely.
"""
from .art import ArtMixin
from .clipboard import ClipboardMixin
from .data import DataMixin
from .downloads import DownloadsMixin
from .helpers import HelpersMixin
from .library import LibraryMixin
from .lyrics import LyricsMixin
from .navigation import NavigationMixin
from .playback import PlaybackMixin
from .refresh import RefreshMixin
from .remote import RemoteMixin
from .settings import SettingsMixin
from .viz import VizMixin

__all__ = [
    "ArtMixin", "ClipboardMixin", "DataMixin", "DownloadsMixin", "HelpersMixin", "LibraryMixin", "LyricsMixin",
    "NavigationMixin", "PlaybackMixin", "RefreshMixin", "RemoteMixin", "SettingsMixin", "VizMixin",
]
