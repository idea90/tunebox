"""
Desktop media controls on Linux through MPRIS (the D-Bus standard that `playerctl`, GNOME / KDE media
widgets, lock screens, KDE Connect and phone-style remotes all speak).

Tunebox shows up as the player `org.mpris.MediaPlayer2.tunebox`:

    desktop -> Tunebox   Play, Pause, PlayPause, Next, Previous, Stop, Seek, SetPosition, and writes to
                         Volume, LoopStatus and Shuffle. They reach the app through `handler(action, arg)`.
    Tunebox -> desktop   title, artists, album, length, square cover, play state, volume, loop and shuffle,
                         pushed with PropertiesChanged whenever they change.

Needs the pure-Python `dbus-next` package (installed on Linux by default) and a D-Bus session bus. Without
either, start() returns False and the app runs without it. Everything D-Bus runs in its own thread with its
own event loop, so a slow desktop client can never stall the UI or playback.

Not implemented on purpose: the TrackList interface (HasTrackList is false), OpenUri, Raise.
"""
import asyncio
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import MPRIS_ART_DIR

BUS_NAME = "org.mpris.MediaPlayer2.tunebox"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
ART_FILES_MAX = 10                      # cover files kept for the desktop; the newest ones

Handler = Callable[..., None]           # handler(action: str, arg=None), called from the D-Bus thread


# ------------------------------------------------------------------ plain-data helpers (no D-Bus needed)

LOOP_STATUS = {"off": "None", "one": "Track", "all": "Playlist"}
REPEAT_FROM_LOOP = {v: k for k, v in LOOP_STATUS.items()}


def track_id(track: Optional[Dict[str, Any]]) -> str:
    """D-Bus object path for a song. Paths allow only [A-Za-z0-9_], and video ids contain '-', so escape."""
    vid = (track or {}).get("videoId") or ""
    if not vid:
        return NO_TRACK
    return "/org/tunebox/track/" + re.sub(r"[^A-Za-z0-9_]", lambda m: "_%02x" % ord(m.group()), vid)


def playback_status(is_playing: bool, is_paused: bool) -> str:
    if is_paused:
        return "Paused"
    return "Playing" if is_playing else "Stopped"


def artist_names(track: Dict[str, Any]) -> List[str]:
    names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in (track.get("artists") or [])]
    names = [n for n in names if n]
    return names or ([track["artist"]] if track.get("artist") else [])


def snapshot_from_player(player) -> Dict[str, Any]:
    """What the desktop should see right now, as plain data. Cheap: called twice a second from the UI thread."""
    track = player.current_track
    has_queue = bool(player.queue)
    return {
        "status": playback_status(player.is_playing, player.is_paused),
        "loop": LOOP_STATUS.get(player.repeat_mode, "None"),
        "shuffle": bool(player.shuffle),
        "volume": max(0.0, min(1.0, player.volume / 100.0)),
        "track": dict(track) if track else None,
        "length_us": int(float(player.track_duration or 0) * 1_000_000),
        "can_go_next": has_queue,
        "can_go_previous": has_queue,
        "can_play": track is not None,
        "can_seek": bool(track and player.is_playing),
    }


def metadata_fields(track: Optional[Dict[str, Any]], length_us: int = 0,
                    art_url: Optional[str] = None) -> Dict[str, Tuple[str, Any]]:
    """MPRIS Metadata as {key: (D-Bus type signature, value)}. Empty when nothing is playing."""
    if not track:
        return {"mpris:trackid": ("o", NO_TRACK)}
    out: Dict[str, Tuple[str, Any]] = {
        "mpris:trackid": ("o", track_id(track)),
        "xesam:title": ("s", track.get("title") or "Unknown"),
    }
    if length_us > 0:
        out["mpris:length"] = ("x", int(length_us))
    if art_url:
        out["mpris:artUrl"] = ("s", art_url)
    artists = artist_names(track)
    if artists:
        out["xesam:artist"] = ("as", artists)
    album = track.get("album")
    album = album.get("name", "") if isinstance(album, dict) else (album or "")
    if album:
        out["xesam:album"] = ("s", album)
    if track.get("videoId"):
        out["xesam:url"] = ("s", f"https://music.youtube.com/watch?v={track['videoId']}")
    return out


def art_url_for(track: Dict[str, Any]) -> Optional[str]:
    """file:// URL of a square JPEG of the song's cover, written where the desktop can read it.

    Blocks while the cover downloads (usually it is already cached from the sidebar), so call it from a worker.
    """
    from . import albumart
    vid = track.get("videoId")
    if not vid:
        return None
    path = MPRIS_ART_DIR / (re.sub(r"[^A-Za-z0-9_-]", "_", vid) + ".jpg")
    if not path.exists():
        img = albumart.fetch_image(track)
        if img is None:
            return None
        try:
            albumart.square_for_display(img, 512).save(path, "JPEG", quality=90)
        except OSError:
            return None
    else:
        os.utime(path)
    try:                                                  # keep only the newest few
        files = sorted(MPRIS_ART_DIR.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        for old in files[:max(0, len(files) - ART_FILES_MAX)]:
            old.unlink()
    except OSError:
        pass
    return path.as_uri()


# ------------------------------------------------------------------ the D-Bus service

class MprisService:
    """Owns the D-Bus thread. All public methods are safe to call from any thread."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bus = None
        self._player_iface = None
        self._handler: Optional[Handler] = None
        self._position_fn: Callable[[], float] = lambda: 0.0
        self._state: Dict[str, Any] = {}
        self._art_url: Optional[str] = None
        self._art_for: Optional[str] = None           # video id the art above (or being fetched) belongs to
        self._last_pos: Optional[Tuple[float, float]] = None   # (position, monotonic time) at the last update
        self.bus_name: Optional[str] = None

    # ---- lifecycle

    def start(self, handler: Handler, position_fn: Callable[[], float],
              on_ready: Optional[Callable[[bool], None]] = None) -> bool:
        """Connect in the background. Returns False at once when it can't work here (not Linux, no
        dbus-next, no session bus); otherwise True, and `on_ready(ok)` reports whether the connection worked."""
        if self._thread is not None:
            return True
        if not _bus_available():
            return False
        try:
            import dbus_next  # noqa: F401
        except Exception:
            return False
        self._handler, self._position_fn = handler, position_fn
        self._thread = threading.Thread(target=self._run, args=(on_ready,), name="mpris", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        loop, bus = self._loop, self._bus
        if loop is not None and bus is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(bus.disconnect)
            except RuntimeError:
                pass
        self._thread = self._loop = self._bus = self._player_iface = None
        self.bus_name = None

    def update(self, snapshot: Dict[str, Any]) -> None:
        """Publish the player's state (a `snapshot_from_player` dict). No-op until connected."""
        loop = self._loop
        if loop is not None and not loop.is_closed() and self._player_iface is not None:
            try:
                loop.call_soon_threadsafe(self._apply, snapshot)
            except RuntimeError:
                pass

    # ---- the D-Bus thread

    def _run(self, on_ready) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve(on_ready))
        except Exception:
            if on_ready:
                try:
                    on_ready(False)
                except Exception:
                    pass
        finally:
            self._player_iface = None
            try:
                loop.close()
            except Exception:
                pass

    async def _serve(self, on_ready) -> None:
        from dbus_next import BusType, RequestNameReply
        from dbus_next.aio import MessageBus

        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        root, player_iface = _make_interfaces(self)
        bus.export(OBJECT_PATH, root)
        bus.export(OBJECT_PATH, player_iface)
        name = BUS_NAME
        if await bus.request_name(name) != RequestNameReply.PRIMARY_OWNER:   # a second Tunebox is running
            name = f"{BUS_NAME}.instance{os.getpid()}"
            await bus.request_name(name)
        self._bus, self._player_iface, self.bus_name = bus, player_iface, name
        if on_ready:
            on_ready(True)
        await bus.wait_for_disconnect()

    def _apply(self, snap: Dict[str, Any]) -> None:
        """(D-Bus thread) Adopt a new snapshot and tell the desktop what changed."""
        import time
        old, iface = self._state, self._player_iface
        if iface is None:
            return
        track = snap.get("track")
        vid = (track or {}).get("videoId")
        if vid != self._art_for:                          # new song: the old cover is wrong, fetch the new one
            self._art_for, self._art_url = vid, None
            if track and vid:
                asyncio.ensure_future(self._fetch_art(dict(track)))
        self._state = snap

        changed: Dict[str, Any] = {}
        for key, prop in (("status", "PlaybackStatus"), ("loop", "LoopStatus"), ("shuffle", "Shuffle"),
                          ("can_go_next", "CanGoNext"), ("can_go_previous", "CanGoPrevious"),
                          ("can_play", "CanPlay"), ("can_seek", "CanSeek")):
            if snap.get(key) != old.get(key):
                changed[prop] = snap[key]
        if abs(snap.get("volume", 0) - old.get("volume", -1)) > 0.001:
            changed["Volume"] = snap["volume"]
        meta_key = lambda s: (track_id(s.get("track")), (s.get("track") or {}).get("title"), s.get("length_us"))
        if meta_key(snap) != meta_key(old):
            changed["Metadata"] = self.metadata_variants()
        if changed:
            iface.emit_properties_changed(changed)

        # A jump the desktop didn't ask for (seek bar, restart, new song) needs a Seeked signal.
        pos, now = float(self._position_fn()), time.monotonic()
        last = self._last_pos
        expected = None if last is None else last[0] + ((now - last[1]) if old.get("status") == "Playing" else 0.0)
        if expected is not None and abs(pos - expected) > 1.5 and "Metadata" not in changed:
            iface.Seeked(int(pos * 1_000_000))
        self._last_pos = (pos, now)

    async def _fetch_art(self, track: Dict[str, Any]) -> None:
        vid = track.get("videoId")
        url = await asyncio.get_running_loop().run_in_executor(None, _safe_art_url, track)
        if vid != self._art_for or url is None or self._player_iface is None:
            return                                         # the song changed meanwhile, or no cover exists
        self._art_url = url
        self._player_iface.emit_properties_changed({"Metadata": self.metadata_variants()})

    # ---- values the interfaces serve

    def metadata_variants(self) -> Dict[str, Any]:
        from dbus_next import Variant
        s = self._state
        fields = metadata_fields(s.get("track"), s.get("length_us", 0), self._art_url)
        return {k: Variant(sig, val) for k, (sig, val) in fields.items()}

    def position_us(self) -> int:
        try:
            return int(max(0.0, float(self._position_fn())) * 1_000_000)
        except Exception:
            return 0

    def call(self, action: str, arg: Any = None) -> None:
        """Hand a desktop request to the app. Never raises into the D-Bus layer."""
        handler = self._handler
        if handler is not None:
            try:
                handler(action, arg)
            except Exception:
                pass

    def current_track_id(self) -> str:
        return track_id(self._state.get("track"))

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)


def _safe_art_url(track: Dict[str, Any]) -> Optional[str]:
    try:
        return art_url_for(track)
    except Exception:
        return None


def _bus_available() -> bool:
    """Is there a session bus to talk to? (Linux only.)"""
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    return Path(f"/run/user/{os.getuid()}/bus").exists()


def _make_interfaces(svc: "MprisService"):
    """Build the two exported interfaces. Defined here, not at import time, so this module loads without dbus-next."""
    from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method, signal

    # dbus-next reads each D-Bus signature from the Python annotations, written as strings: 's', 'x', 'a{sv}'...
    # ruff: noqa: F821, F722
    class Root(ServiceInterface):
        def __init__(self):
            super().__init__("org.mpris.MediaPlayer2")

        @method()
        def Raise(self):
            pass

        @method()
        def Quit(self):
            svc.call("quit")

        @dbus_property(access=PropertyAccess.READ)
        def CanQuit(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanRaise(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def HasTrackList(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def Identity(self) -> "s":
            return "Tunebox"

        @dbus_property(access=PropertyAccess.READ)
        def SupportedUriSchemes(self) -> "as":
            return []

        @dbus_property(access=PropertyAccess.READ)
        def SupportedMimeTypes(self) -> "as":
            return []

    class Player(ServiceInterface):
        def __init__(self):
            super().__init__("org.mpris.MediaPlayer2.Player")

        @method()
        def Next(self):
            svc.call("next")

        @method()
        def Previous(self):
            svc.call("prev")

        @method()
        def Pause(self):
            svc.call("pause")

        @method()
        def PlayPause(self):
            svc.call("play_pause")

        @method()
        def Stop(self):
            svc.call("stop")

        @method()
        def Play(self):
            svc.call("play")

        @method()
        def Seek(self, Offset: "x"):
            svc.call("seek", Offset / 1_000_000)

        @method()
        def SetPosition(self, TrackId: "o", Position: "x"):
            if TrackId == svc.current_track_id() and Position >= 0:      # a stale id means another song: ignore
                svc.call("set_position", Position / 1_000_000)

        @method()
        def OpenUri(self, Uri: "s"):
            pass

        @signal()
        def Seeked(self, Position: "x") -> "x":
            return Position

        @dbus_property(access=PropertyAccess.READ)
        def PlaybackStatus(self) -> "s":
            return svc.get("status", "Stopped")

        @dbus_property()
        def LoopStatus(self) -> "s":
            return svc.get("loop", "None")

        @LoopStatus.setter
        def LoopStatus(self, value: "s"):
            if value in REPEAT_FROM_LOOP:
                svc.call("loop", REPEAT_FROM_LOOP[value])

        @dbus_property()
        def Rate(self) -> "d":
            return 1.0

        @Rate.setter
        def Rate(self, value: "d"):
            pass                                             # playback speed is fixed at 1.0

        @dbus_property()
        def Shuffle(self) -> "b":
            return bool(svc.get("shuffle", False))

        @Shuffle.setter
        def Shuffle(self, value: "b"):
            svc.call("shuffle", bool(value))

        @dbus_property(access=PropertyAccess.READ)
        def Metadata(self) -> "a{sv}":
            return svc.metadata_variants()

        @dbus_property()
        def Volume(self) -> "d":
            return float(svc.get("volume", 1.0))

        @Volume.setter
        def Volume(self, value: "d"):
            svc.call("volume", max(0.0, min(1.0, float(value))))

        @dbus_property(access=PropertyAccess.READ)
        def Position(self) -> "x":
            return svc.position_us()

        @dbus_property(access=PropertyAccess.READ)
        def MinimumRate(self) -> "d":
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MaximumRate(self) -> "d":
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def CanGoNext(self) -> "b":
            return bool(svc.get("can_go_next", False))

        @dbus_property(access=PropertyAccess.READ)
        def CanGoPrevious(self) -> "b":
            return bool(svc.get("can_go_previous", False))

        @dbus_property(access=PropertyAccess.READ)
        def CanPlay(self) -> "b":
            return bool(svc.get("can_play", False))

        @dbus_property(access=PropertyAccess.READ)
        def CanPause(self) -> "b":
            return bool(svc.get("can_play", False))

        @dbus_property(access=PropertyAccess.READ)
        def CanSeek(self) -> "b":
            return bool(svc.get("can_seek", False))

        @dbus_property(access=PropertyAccess.READ)
        def CanControl(self) -> "b":
            return True

    return Root(), Player()


_service = MprisService()


def start(handler: Handler, position_fn: Callable[[], float],
          on_ready: Optional[Callable[[bool], None]] = None) -> bool:
    return _service.start(handler, position_fn, on_ready)


def update(snapshot: Dict[str, Any]) -> None:
    _service.update(snapshot)


def stop() -> None:
    _service.stop()
