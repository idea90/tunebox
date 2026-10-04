"""Thread plumbing every other mixin relies on: run on the UI thread, run blocking work in a worker, toast, seek."""
import threading

from ...core.player import player


class HelpersMixin:
    """Thread plumbing every other mixin relies on: run on the UI thread, run blocking work in a worker, toast, seek."""

    def _ui(self, fn, *args) -> None:
        """Run `fn` on the UI thread from any thread (non-blocking)."""
        if threading.get_ident() == self._ui_thread:
            fn(*args)
        elif self._loop and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:
                pass

    def _bg(self, fn, *args) -> None:
        """Run blocking work (network, downloads) off the UI thread."""
        def runner():
            try:
                fn(*args)
            except Exception as e:  # never let a worker die silently
                self._ui(self.say, f"{type(e).__name__}: {e}"[:160], True)
        self.run_worker(runner, thread=True, exit_on_error=False)

    def say(self, msg: str, error: bool = False) -> None:
        self.notify(msg, severity="error" if error else "information", timeout=5 if error else 3)

    def seek_to(self, seconds: float) -> None:
        self._bg(lambda: player.seek(seconds, relative=False))
