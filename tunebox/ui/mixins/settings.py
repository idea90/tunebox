"""Settings tab actions: theme, volume normalization, gapless, audio cache."""

from ...config import CACHE_DIR, config
from ..theme import THEMES


class SettingsMixin:
    """Settings tab actions: theme, volume normalization, gapless, audio cache."""

    def action_toggle_normalize(self) -> None:
        on = not config.get("normalize_volume", True)
        config.set("normalize_volume", on)
        self.say(f"Volume normalization {'on' if on else 'off'} (applies to songs cached from now on)")
        self.tick()

    def action_toggle_gapless(self) -> None:
        on = not config.get("gapless", True)
        config.set("gapless", on)
        self.say(f"Gapless playback {'on' if on else 'off'}")
        self.tick()

    def action_cycle_theme(self) -> None:
        names = list(THEMES.keys())
        nxt = names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]
        self.theme = nxt
        config.set("theme", nxt)
        self.say(f"Theme: {nxt}")
        self._sig = None             # repaint the lists: their cell colours come from the theme
        self.tick()

    def action_clear_cache(self) -> None:
        count = 0
        playing = None
        for f in CACHE_DIR.glob("*.*"):
            try:
                f.unlink()
                count += 1
            except OSError:
                playing = f  # file is open by the current track
        self.say(f"Cleared {count} cached files" + (" (skipped the one in use)" if playing else ""))
        self._refresh_usage()
