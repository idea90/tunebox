"""
Rich renderables used by the one-shot CLI subcommands (search, charts, lyrics...).
The interactive app lives in `app.py` and uses Textual widgets instead.
"""
from typing import List, Dict, Any, Optional
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.box import ROUNDED, SIMPLE
from .theme import get_theme
from ..core.database import get_favorite_ids

HEART_ON = "♥"
HEART_OFF = "♡"
PLAY = "▶"
PAUSE = "❚❚"


def format_seconds(seconds: float) -> str:
    """Convert float seconds to mm:ss (h:mm:ss for long tracks)."""
    if not seconds or seconds < 0:
        return "00:00"
    m = int(seconds) // 60
    s = int(seconds) % 60
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def render_track_table(
    tracks: List[Dict[str, Any]],
    current_video_id: Optional[str] = None,
    is_playing: bool = False,
    start_num: int = 1,
    title: str = "Tracks"
) -> Table:
    """Render a clean tracklist table."""
    t = get_theme()
    table = Table(
        title=f"[bold white]{title}[/bold white] [bright_black]({len(tracks)})[/bright_black]",
        box=SIMPLE,
        border_style=t["border"],
        expand=True,
        header_style=t["primary"],
        show_edge=False,
        pad_edge=False
    )

    table.add_column("#", justify="right", style="cyan", width=4)
    table.add_column("Title", style="white", ratio=5)
    table.add_column("Artist", style="bright_cyan", ratio=4)
    table.add_column("Time", justify="right", style="bright_yellow", width=7)
    table.add_column(HEART_ON, justify="center", width=3)

    favs = get_favorite_ids()
    for idx, item in enumerate(tracks):
        item_num = start_num + idx
        v_id = item.get("videoId") or item.get("id") or ""
        is_curr = bool(current_video_id and v_id == current_video_id)

        if is_curr:
            prefix = PLAY if is_playing else PAUSE
            num_str = f"[bold green]{prefix}{item_num}[/bold green]"
            row_style = t["playing_track"]
        else:
            num_str = str(item_num)
            row_style = None

        track_title = item.get("title", "Unknown")
        artist = item.get("artist") or "Unknown"
        secs = item.get("duration_seconds") or 0
        duration = item.get("duration") or (format_seconds(secs) if secs else "")   # unknown -> "--:--", not 00:00
        fav_icon = f"[bold bright_red]{HEART_ON}[/bold bright_red]" if v_id in favs else f"[grey35]{HEART_OFF}[/grey35]"

        table.add_row(
            num_str,
            Text(track_title, style=row_style) if row_style else Text(track_title),
            Text(artist),
            duration or "--:--",
            fav_icon
        )

    return table


def render_lyrics_panel(lyrics_data: Dict[str, Any], current_seconds: float, compact: bool = False,
                        full: bool = False) -> Panel:
    """Render lyrics; the active line is highlighted for synced lyrics."""
    t = get_theme()
    lines = lyrics_data.get("lines", [])
    is_synced = lyrics_data.get("synced", False)
    source = lyrics_data.get("source", "LRCLIB")

    if not lines:
        return Panel(Text("\n  (No lyrics available)\n", style=t["dim"]), title="[bold]Lyrics[/bold]",
                     border_style=t["border"], box=ROUNDED)

    content = Text()
    if is_synced and not full:
        active_idx = 0
        for i, line in enumerate(lines):
            lt = line.get("time")
            if lt is not None and lt <= current_seconds:
                active_idx = i
            elif lt is not None:
                break

        window = 6 if compact else 10
        start = max(0, active_idx - (2 if compact else 4))
        for i in range(start, min(len(lines), start + window)):
            text = lines[i].get("text", "")
            if i == active_idx:
                content.append(f" {PLAY} {text}\n", style=t["lyrics_active"])
            elif i < active_idx:
                content.append(f"   {text}\n", style=t["lyrics_past"])
            else:
                content.append(f"   {text}\n", style=t["lyrics_future"])
    else:
        for line in (lines if full else lines[:6 if compact else 14]):
            content.append(f"  {line.get('text', '')}\n", style=t["text"])

    return Panel(
        content,
        title=f"[bold magenta]Lyrics[/bold magenta] [bright_black]({source})[/bright_black]",
        border_style=t["border"],
        box=ROUNDED,
        padding=(0, 1)
    )
