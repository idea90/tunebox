"""Search-box autocomplete: your own recent searches first, then YouTube Music suggestions."""
import asyncio
from typing import Optional

from textual.suggester import Suggester

from ..config import config
from ..core.ytmusic import yt_client


class SearchSuggester(Suggester):
    def __init__(self) -> None:
        # use_cache avoids re-asking the network for a prefix we already resolved.
        super().__init__(use_cache=True, case_sensitive=False)

    async def get_suggestion(self, value: str) -> Optional[str]:
        typed = value.lower()
        if len(typed.strip()) < 2:
            return None

        for past in config.get("search_history", []):
            if past.lower().startswith(typed) and past.lower() != typed:
                return past

        suggestions = await asyncio.to_thread(yt_client.get_suggestions, value)
        for s in suggestions:
            if s.lower().startswith(typed) and s.lower() != typed:
                return s
        return None
