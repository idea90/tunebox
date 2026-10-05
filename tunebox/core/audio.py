"""
Audio output for the Player. Two interchangeable backends with the same small interface:

    PygameBackend   pygame.mixer.music: the default on desktops
    MpvBackend      drives an `mpv` process over its JSON IPC socket: the default on Termux (Android), where
                    pygame has no audio output. Works anywhere mpv is installed (Linux, macOS, WSL).

`create_audio_backend()` picks one: the `audio_backend` setting (auto | pygame | mpv), or the TUNEBOX_AUDIO
environment variable. "auto" means mpv on Termux and pygame everywhere else, with a fallback to the other one
when the first is unavailable.

Interface (all times in the units of pygame.mixer.music, so the Player's position maths stays unchanged):

    ok / problem          did it start; if not, a one-line reason to show the user
    set_volume(0.0-1.0)
    load(path, start, paused)   replace what is playing (this also drops anything queued) and play from `start`
    queue(path)                 play this next, gaplessly, when the current file ends
    pause() / unpause() / stop()
    pos_ms()                    milliseconds since load() began playing (NOT counting `start`), -1 when unknown
    busy()                      audio is playing (False once the last file has ended)
    close()
"""
import atexit
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from typing import Any, Dict, List, Optional

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame
except Exception:                                   # not installed (e.g. Termux): the mpv backend is used instead
    pygame = None

from .. import termux
from ..config import config


class PygameBackend:
    name = "pygame"

    def __init__(self) -> None:
        self.ok, self.problem = False, ""
        if pygame is None:
            self.problem = "pygame is not installed."
            return
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
            self.ok = True
        except Exception:
            try:
                pygame.mixer.init()
                self.ok = True
            except Exception:
                self.problem = "No audio output device found."

    def set_volume(self, volume: float) -> None:
        try:
            pygame.mixer.music.set_volume(volume)
        except Exception:
            pass

    def load(self, path: str, start: float = 0.0, paused: bool = False) -> None:
        pygame.mixer.music.load(path)
        if start > 0:
            pygame.mixer.music.play(start=start)
        else:
            pygame.mixer.music.play()
        if paused:
            pygame.mixer.music.pause()

    def queue(self, path: str) -> None:
        pygame.mixer.music.queue(path)

    def pause(self) -> None:
        pygame.mixer.music.pause()

    def unpause(self) -> None:
        pygame.mixer.music.unpause()

    def stop(self) -> None:
        pygame.mixer.music.stop()

    def pos_ms(self) -> int:
        return pygame.mixer.music.get_pos()

    def busy(self) -> bool:
        return pygame.mixer.music.get_busy()

    def close(self) -> None:
        pass


class MpvBackend:
    """One long-lived `mpv --idle` process, controlled over its IPC socket (https://mpv.io/manual/stable/#json-ipc).

    Position, pause and idle state are pushed by mpv (observed properties) and cached, so the Player's frequent
    pos_ms() / busy() polls never wait on the socket. Commands wait for mpv's reply, with a timeout.
    """
    name = "mpv"
    LOAD_TIMEOUT = 8.0
    COMMAND_TIMEOUT = 3.0

    def __init__(self, mpv_path: Optional[str] = None, extra_args: Optional[List[str]] = None) -> None:
        self.ok, self.problem = False, ""
        self.path = mpv_path or shutil.which("mpv") or ""
        self.extra_args = list(extra_args or [])
        self._proc: Optional[subprocess.Popen] = None
        self._sock: Optional[socket.socket] = None
        self._tmp: Optional[str] = None
        self._lock = threading.Lock()            # serialises writes to the socket
        self._state = threading.Lock()           # guards the cached state below
        self._waiters: Dict[int, List[Any]] = {}
        self._rid = 0
        self._alive = False
        self._volume = 100.0
        self._time_pos: Optional[float] = None
        self._origin = 0.0                       # where in the file load() started it
        self._idle = True
        self._expect_start = 0                   # start-file events caused by our own load() calls
        self._loaded = threading.Event()
        self._load_error = ""
        atexit.register(self.close)
        if not hasattr(socket, "AF_UNIX"):
            self.problem = "The mpv backend needs Unix sockets (not available on this system)."
        elif not self.path:
            self.problem = "mpv is not installed. In Termux: pkg install mpv"
        else:
            self._spawn()

    # ------------------------------------------------------------ process and connection

    def _spawn(self) -> None:
        self.close()
        self._tmp = tempfile.mkdtemp(prefix="tunebox-mpv-")
        sock_path = os.path.join(self._tmp, "ipc")
        log = open(os.path.join(self._tmp, "log"), "wb")
        cmd = [self.path, "--idle=yes", "--no-video", "--no-terminal", "--really-quiet", "--audio-display=no",
               "--gapless-audio=yes", "--keep-open=no", f"--volume={self._volume:g}",
               f"--input-ipc-server={sock_path}", *self.extra_args]
        try:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        except OSError as e:
            self.problem = f"Could not start mpv: {e}"
            return
        finally:
            log.close()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for _ in range(100):                                        # up to ~5 s for mpv to open its socket
            if self._proc.poll() is not None:
                break
            try:
                sock.connect(sock_path)
                break
            except OSError:
                import time
                time.sleep(0.05)
        else:
            self.problem = "mpv did not start in time."
            sock.close()
            return
        if self._proc.poll() is not None:
            sock.close()
            self.problem = "mpv exited at startup" + self._log_tail()
            return
        self._sock = sock
        self._alive = True
        threading.Thread(target=self._read_loop, name="mpv-ipc", daemon=True).start()
        for i, prop in enumerate(("time-pos", "idle-active"), 1):
            self._command("observe_property", i, prop)
        try:                                 # the first pushed idle value can predate mpv settling: ask once
            with self._state:
                self._idle = bool(self._command("get_property", "idle-active"))
        except Exception:
            pass
        self.ok, self.problem = True, ""

    def _log_tail(self) -> str:
        try:
            with open(os.path.join(self._tmp or "", "log"), "rb") as f:
                lines = f.read().decode("utf-8", "replace").strip().splitlines()
            return f": {lines[-1][:160]}" if lines else "."
        except OSError:
            return "."

    def _ensure(self) -> None:
        """Start mpv again if it died (killed by Android, crashed, ...)."""
        if self._alive and self._proc is not None and self._proc.poll() is None:
            return
        self._alive = False
        self._spawn()
        if not self.ok:
            raise RuntimeError(self.problem or "mpv is not running.")

    def _read_loop(self) -> None:
        sock, buf = self._sock, b""
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        self._dispatch(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        finally:
            self._alive = False
            with self._state:
                self._idle, self._time_pos = True, None
            self._loaded.set()                               # nothing is going to load now
            for waiter in list(self._waiters.values()):
                waiter[1] = {"error": "mpv connection closed"}
                waiter[0].set()

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        rid = msg.get("request_id")
        if rid is not None:
            waiter = self._waiters.pop(rid, None)
            if waiter:
                waiter[1] = msg
                waiter[0].set()
            return
        event = msg.get("event")
        if event == "property-change":
            name, data = msg.get("name"), msg.get("data")
            with self._state:
                if name == "time-pos":
                    self._time_pos = data
                elif name == "idle-active":
                    self._idle = bool(data)
        elif event == "start-file":
            with self._state:
                if self._expect_start > 0:
                    self._expect_start -= 1
                else:                        # mpv moved on to the queued file by itself (gapless): back to 0
                    self._origin, self._time_pos = 0.0, 0.0
        elif event == "file-loaded":
            self._loaded.set()
        elif event == "end-file" and msg.get("reason") == "error":
            self._load_error = msg.get("file_error") or "mpv could not play this file"
            self._loaded.set()

    def _command(self, *args: Any) -> Any:
        waiter: List[Any] = [threading.Event(), None]
        with self._lock:
            self._rid += 1
            rid = self._rid
            self._waiters[rid] = waiter
            try:
                self._sock.sendall((json.dumps({"command": list(args), "request_id": rid}) + "\n").encode("utf-8"))
            except (OSError, AttributeError) as e:
                self._waiters.pop(rid, None)
                raise RuntimeError(f"mpv is not reachable: {e}")
        if not waiter[0].wait(self.COMMAND_TIMEOUT):
            self._waiters.pop(rid, None)
            raise RuntimeError(f"mpv did not answer '{args[0]}'")
        reply = waiter[1] or {}
        if reply.get("error") != "success":
            raise RuntimeError(f"mpv: {reply.get('error')}")
        return reply.get("data")

    # ------------------------------------------------------------ the backend interface

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume)) * 100.0
        if self._alive:
            try:
                self._command("set_property", "volume", self._volume)
            except Exception:
                pass

    def load(self, path: str, start: float = 0.0, paused: bool = False) -> None:
        self._ensure()
        with self._state:
            self._time_pos, self._origin, self._load_error = None, float(start), ""
            self._expect_start += 1
        self._loaded.clear()
        try:
            self._command("set_property", "pause", bool(paused or start > 0))       # no blip of the song's first second
            self._command("loadfile", path, "replace")
            if not self._loaded.wait(self.LOAD_TIMEOUT):
                raise RuntimeError("mpv did not start the file in time")
            if self._load_error:
                raise RuntimeError(self._load_error)
            if start > 0:
                self._command("seek", float(start), "absolute")
                if not paused:
                    self._command("set_property", "pause", False)
        except Exception:
            with self._state:
                self._expect_start = 0
            raise

    def queue(self, path: str) -> None:
        if self._idle:                       # appending to an idle mpv would start it playing at once
            return
        self._command("loadfile", path, "append")

    def pause(self) -> None:
        self._command("set_property", "pause", True)

    def unpause(self) -> None:
        self._command("set_property", "pause", False)

    def stop(self) -> None:
        if self._alive:
            self._command("stop")
        with self._state:
            self._time_pos = None

    def pos_ms(self) -> int:
        with self._state:
            if self._idle or self._time_pos is None:
                return -1
            return max(0, int((self._time_pos - self._origin) * 1000))

    def busy(self) -> bool:
        return self._alive and not self._idle

    def close(self) -> None:
        proc, self._proc, tmp, self._tmp = self._proc, None, self._tmp, None
        self._alive = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        self.ok = False


# ------------------------------------------------------------------ choosing

def _mpv_args() -> List[str]:
    args = config.get("mpv_args", [])
    if isinstance(args, str):
        import shlex
        args = shlex.split(args)
    return [str(a) for a in args] if isinstance(args, list) else []


def create_audio_backend():
    """The backend to use, already started. If the preferred one is unavailable the other is tried; if neither
    works, the preferred one is returned with `ok` False and its `problem` text, for the app to show."""
    choice = str(os.environ.get("TUNEBOX_AUDIO") or config.get("audio_backend", "auto")).lower()
    if choice not in ("auto", "pygame", "mpv"):
        choice = "auto"
    prefer_mpv = choice == "mpv" or (choice == "auto" and termux.is_termux())
    order = ["mpv", "pygame"] if prefer_mpv else ["pygame", "mpv"]
    if choice in ("pygame", "mpv"):
        order = [choice]                     # an explicit choice is respected, no silent switch
    first = None
    for name in order:
        backend = MpvBackend(config.get("mpv_path") or None, _mpv_args()) if name == "mpv" else PygameBackend()
        if backend.ok:
            return backend
        first = first or backend
    return first
