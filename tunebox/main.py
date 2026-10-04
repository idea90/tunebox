"""
Tunebox - Entry Point
Supports both the full interactive app and fast one-shot subcommands.
"""
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import argparse
import re
import signal
import threading
import time
from typing import Optional
from rich.console import Console
from rich.markup import escape

from . import __version__
from .core.ytmusic import yt_client
from .core.lyrics import get_lyrics
from .core.downloader import download_track_file
from .core import batch, downloader
from .core.database import get_favorites, get_history, get_playlist as get_local_playlist, get_playlists
from .ui.theme import get_theme
from .ui.components import render_track_table, render_lyrics_panel, format_seconds, PLAY, PAUSE

console = Console()


def _poll_key() -> Optional[str]:
    """Non-blocking single keypress (Windows and POSIX), or None."""
    if sys.platform == "win32":
        import msvcrt
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):  # arrow/function keys send a prefix + a code
                msvcrt.getwch()
                return None
            return ch
        return None
    import select
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.read(1)
    return None


def play_cli(query: str):
    """Search and play a song in a terminal mini-player."""
    from .core.player import player  # imported lazily: initializes the audio device

    t = get_theme()
    console.print(f"[{t['dim']}]Searching YouTube Music for:[/{t['dim']}] [bold white]{escape(query)}[/bold white]...")
    results = yt_client.search(query, filter_type="songs", limit=5)
    if not results:
        console.print(f"[{t['error']}]No songs found for '{escape(query)}'.[/{t['error']}]")
        return

    track = results[0]
    title = track.get("title", "Unknown")
    artist = track.get("artist", "Unknown")
    dur = track.get("duration", "--:--")

    console.print(f"[{t['success']}]{PLAY} Now Playing:[/{t['success']}] [bold white]{escape(title)}[/bold white] \u2014 "
                  f"[{t['secondary']}]{escape(artist)}[/{t['secondary']}] [{t['dim']}]({dur})[/{t['dim']}]")

    # One-shot mode: play this track once and exit, regardless of saved queue settings.
    player.autoplay = False
    player.repeat_mode = "off"
    player.shuffle = False

    if not player.play_track(track):
        console.print(f"[{t['error']}]{escape(player.last_error or 'Failed to stream track audio.')}[/{t['error']}]")
        return

    console.print(f"[{t['dim']}]Controls: \\[Space] pause/resume  \\[+/-] volume  \\[,/.] seek 10s  \\[q] quit[/{t['dim']}]\n")

    old_tty = None
    if sys.platform != "win32" and sys.stdin.isatty():
        import termios
        import tty
        old_tty = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
    try:
        total = float(track.get("duration_seconds") or 0)
        while player.is_playing:
            key = _poll_key()
            if key in ("q", "Q", "\x03"):
                break
            elif key == " ":
                player.toggle_pause()
            elif key in ("+", "="):
                player.volume_up()
            elif key in ("-", "_"):
                player.volume_down()
            elif key in (".", ">"):
                player.seek(10)
            elif key in (",", "<"):
                player.seek(-10)

            pos = player.get_position()
            width = 30
            filled = int(max(0.0, min(1.0, pos / total)) * (width - 1)) if total > 0 else 0
            bar = "\u2501" * filled + "\u25cf" + "\u2500" * (width - 1 - filled)
            state = PAUSE if player.is_paused else PLAY
            sys.stdout.write(f"\r  {state} {format_seconds(pos)} / {format_seconds(total)}  {bar}  Vol {player.volume}%   ")
            sys.stdout.flush()
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if old_tty is not None:
            import termios
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
        player.stop()
        console.print("\n[bold magenta]Playback stopped.[/bold magenta]")


def search_cli(query: str, filter_type: str = "songs"):
    """Search and display formatted results."""
    t = get_theme()
    console.print(f"[{t['dim']}]Searching for:[/{t['dim']}] [bold white]{escape(query)}[/bold white]...")
    results = yt_client.search(query, filter_type=filter_type, limit=15)
    if not results:
        reason = f" ({escape(yt_client.last_error)})" if yt_client.last_error else ""
        console.print(f"[{t['error']}]No results found{reason}.[/{t['error']}]")
        return

    song_items = [r for r in results if r.get("videoId") or r.get("type") in ["song", "video"]]
    if song_items:
        console.print(render_track_table(song_items, title=f"Results for '{query}'"))
    else:
        for idx, item in enumerate(results):
            console.print(f"{idx + 1}. {escape(str(item.get('title')))} ({item.get('type')}) {escape(str(item.get('artist') or ''))}")


def download_cli(query: str, fmt: str = "mp3"):
    """Download song or audio stream."""
    t = get_theme()
    console.print(f"[{t['dim']}]Searching for track to download:[/{t['dim']}] [bold white]{escape(query)}[/bold white]...")
    results = yt_client.search(query, filter_type="songs", limit=1)
    if not results:
        console.print(f"[{t['error']}]Track not found.[/{t['error']}]")
        return

    track = results[0]
    console.print(f"[{t['primary']}]Downloading:[/{t['primary']}] {escape(track.get('title', ''))} \u2014 {escape(track.get('artist', ''))}...")
    out_file = download_track_file(track, fmt=fmt)
    if out_file:
        console.print(f"[{t['success']}]\u2714 Successfully downloaded:[/{t['success']}] [bold white]{escape(str(out_file))}[/bold white]")
    else:
        console.print(f"[{t['error']}]Download failed: {escape(downloader.last_error or 'unknown error')}[/{t['error']}]")


def resolve_playlist_source(source: str):
    """What a `download-playlist` argument points at: ("local", name) | ("album", browse id) | ("playlist", id).

    Accepts the name of one of your own playlists (checked first), a music.youtube.com / youtube.com playlist
    link, or a bare playlist or album id.
    """
    source = source.strip()
    for pl in get_playlists():
        if pl["title"].lower() == source.lower():
            return "local", pl["id"]
    found = re.search(r"[?&]list=([A-Za-z0-9_-]+)", source)
    ident = found.group(1) if found else source
    if ident.startswith("VL"):
        ident = ident[2:]
    return ("album", ident) if ident.startswith("MPRE") else ("playlist", ident)


def download_playlist_cli(source: str, fmt: str = "mp3", assume_yes: bool = False):
    """Download every song of a playlist or album, or of one of your own playlists."""
    t = get_theme()
    kind, ident = resolve_playlist_source(source)
    console.print(f"[{t['dim']}]Fetching the playlist...[/{t['dim']}]")
    if kind == "local":
        data = get_local_playlist(ident) or {}
    elif kind == "album":
        data = yt_client.get_album(ident)
    else:
        data = yt_client.get_playlist(ident, limit=None)
    tracks, title = data.get("tracks", []), data.get("title") or "Playlist"
    if not tracks:
        console.print(f"[{t['error']}]Nothing found{': ' + escape(yt_client.last_error) if yt_client.last_error else ''}. "
                      f"Check the link or id (private playlists need `tunebox login`).[/{t['error']}]")
        return

    todo, skipped, unavailable = batch.plan(tracks, fmt)
    console.print(f"[bold white]{escape(title)}[/bold white]: {len(todo)} to download as {fmt.upper()}"
                  + (f", {skipped} already downloaded" if skipped else "")
                  + (f", {unavailable} unavailable" if unavailable else ""))
    if not todo:
        console.print(f"[{t['success']}]Nothing to do.[/{t['success']}]")
        return
    if not assume_yes:
        if not sys.stdin.isatty():
            console.print(f"[{t['error']}]Not a terminal: add --yes to start without asking.[/{t['error']}]")
            return
        if input("Start? [y/N] ").strip().lower() not in ("y", "yes"):
            console.print("Cancelled.")
            return

    cancel = threading.Event()

    def stop_after_this_song(signum, frame):
        cancel.set()
        signal.signal(signal.SIGINT, signal.SIG_DFL)          # a second Ctrl+C quits at once
        console.print(f"\n[{t['dim']}]Stopping after this song (Ctrl+C again to quit now)...[/{t['dim']}]")

    def show(event: str, done: int, total: int, track, detail: str) -> None:
        name = escape(f"{track.get('artist', '')} - {track.get('title', '')}")
        if event == "start":
            console.print(f"[{t['dim']}]({done + 1}/{total})[/{t['dim']}] {name}...")
        elif event == "failed":
            console.print(f"  [{t['error']}]\u2718 failed:[/{t['error']}] {escape(detail)}")

    previous = signal.signal(signal.SIGINT, stop_after_this_song)
    try:
        result = batch.download_tracks(tracks, fmt, subfolder=title, on_progress=show, cancel=cancel)
    finally:
        signal.signal(signal.SIGINT, previous)

    got = len(result.downloaded)
    if result.cancelled:
        console.print(f"[{t['error']}]Stopped:[/{t['error']}] {got} downloaded. Run the same command to continue.")
    elif result.failed:
        console.print(f"[{t['error']}]Done with problems:[/{t['error']}] {got} downloaded, {len(result.failed)} failed. "
                      f"Run the same command to retry those.")
    else:
        console.print(f"[{t['success']}]\u2714 Downloaded {got} song{'s' if got != 1 else ''}[/{t['success']}] "
                      f"[bold white]{escape(title)}[/bold white]")


def lyrics_cli(query: str):
    """Fetch and print lyrics."""
    t = get_theme()
    console.print(f"[{t['dim']}]Fetching lyrics for:[/{t['dim']}] [bold white]{escape(query)}[/bold white]...")
    results = yt_client.search(query, filter_type="songs", limit=1)
    if not results:
        console.print(f"[{t['error']}]Track not found.[/{t['error']}]")
        return

    track = results[0]
    lyrics = get_lyrics(track.get("title", ""), artist_name=track.get("artist", ""),
                        duration=track.get("duration_seconds"), video_id=track.get("videoId"))
    console.print(render_lyrics_panel(lyrics, 0.0, full=True))


def charts_cli():
    """Display Top Charts."""
    charts = yt_client.get_charts()
    songs = charts.get("songs", [])
    if songs:
        console.print(render_track_table(songs[:20], title="Top Global Music Charts"))
    else:
        console.print("[dim]Unable to fetch charts right now.[/dim]")


def favorites_cli():
    """Display user favorites."""
    favs = get_favorites()
    if not favs:
        console.print("[dim]No favorites saved yet. Press f in the app to favorite the playing song.[/dim]")
    else:
        console.print(render_track_table(favs, title="My Favorite Songs"))


def login_cli(headers_file: str = ""):
    """Sign in to YouTube Music using request headers copied from your browser (ytmusicapi 'browser' auth)."""
    import ytmusicapi
    from .config import AUTH_FILE
    t = get_theme()

    if headers_file:
        with open(headers_file, "r", encoding="utf-8") as f:
            raw = f.read()
    else:
        console.print(
            "[bold]Sign in to YouTube Music[/bold]\n"
            "1. Open https://music.youtube.com in your browser while signed in.\n"
            "2. Open DevTools > Network, filter for 'browse', click a POST request to /youtubei/v1/browse.\n"
            "3. Copy its [bold]request headers[/bold] (Chrome: right-click > Copy > Copy as cURL also works if you paste only the headers).\n"
            "4. Paste them below, then finish with a blank line (Enter twice):\n")
        lines = []
        try:
            while True:
                line = input()
                if not line.strip() and lines:
                    break
                lines.append(line)
        except EOFError:
            pass
        raw = "\n".join(lines)

    if not raw.strip():
        console.print(f"[{t['error']}]No headers received.[/{t['error']}]")
        return
    try:
        ytmusicapi.setup(filepath=str(AUTH_FILE), headers_raw=raw)
        from ytmusicapi import YTMusic
        YTMusic(auth=str(AUTH_FILE)).get_library_playlists(limit=1)   # proves the credentials work
    except Exception as e:
        AUTH_FILE.unlink(missing_ok=True)
        console.print(f"[{t['error']}]Login failed: {escape(str(e)[:200])}[/{t['error']}]")
        return
    console.print(f"[{t['success']}]\u2714 Signed in.[/{t['success']}] Your liked songs and playlists are under Library in the app.\n"
                  f"Credentials are stored in [bold]{escape(str(AUTH_FILE))}[/bold]; `tunebox logout` removes them.")


def logout_cli():
    from .config import AUTH_FILE
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()
        console.print("Signed out. Saved credentials were deleted.")
    else:
        console.print("You are not signed in.")


def main():
    parser = argparse.ArgumentParser(
        prog="tunebox",
        description="Tunebox - YouTube Music in your terminal. Run with no arguments for the interactive app."
    )
    parser.add_argument("-v", "--version", action="version", version=f"Tunebox v{__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    play_p = subparsers.add_parser("play", help="Search and play a song")
    play_p.add_argument("query", type=str, help="Song title, artist, or query")

    search_p = subparsers.add_parser("search", help="Search YouTube Music")
    search_p.add_argument("query", type=str, help="Search query")
    search_p.add_argument("-t", "--type", default="songs", choices=["songs", "videos", "albums", "artists", "playlists"], help="Filter category")

    dl_p = subparsers.add_parser("download", help="Download audio file")
    dl_p.add_argument("query", type=str, help="Song title or query")
    dl_p.add_argument("-f", "--format", default=None, choices=["mp3", "m4a", "flac"],
                      help="Audio format (default: download_format in config.json, else mp3)")

    pl_p = subparsers.add_parser("download-playlist", help="Download a whole playlist or album")
    pl_p.add_argument("source", type=str, help="playlist or album link or id, or the name of one of your own playlists")
    pl_p.add_argument("-f", "--format", default=None, choices=["mp3", "m4a", "flac"],
                      help="Audio format (default: download_format in config.json, else mp3)")
    pl_p.add_argument("-y", "--yes", action="store_true", help="start without asking")

    lyr_p = subparsers.add_parser("lyrics", help="Get song lyrics")
    lyr_p.add_argument("query", type=str, help="Song title or artist")

    subparsers.add_parser("charts", help="View Top Music Charts")
    subparsers.add_parser("favorites", help="View favorite songs")
    subparsers.add_parser("history", help="View listening history")
    subparsers.add_parser("version", help="Show version info")

    login_p = subparsers.add_parser("login", help="Sign in to YouTube Music (liked songs, your playlists)")
    login_p.add_argument("--headers", default="", metavar="FILE", help="read copied request headers from FILE instead of pasting")
    subparsers.add_parser("logout", help="Delete saved YouTube Music credentials")

    args = parser.parse_args()

    if args.command == "play":
        play_cli(args.query)
    elif args.command == "search":
        search_cli(args.query, filter_type=args.type)
    elif args.command == "download":
        download_cli(args.query, fmt=args.format or downloader.download_format())
    elif args.command == "download-playlist":
        download_playlist_cli(args.source, fmt=args.format or downloader.download_format(), assume_yes=args.yes)
    elif args.command == "lyrics":
        lyrics_cli(args.query)
    elif args.command == "charts":
        charts_cli()
    elif args.command == "favorites":
        favorites_cli()
    elif args.command == "login":
        login_cli(args.headers)
    elif args.command == "logout":
        logout_cli()
    elif args.command == "version":
        console.print(f"[bold magenta]Tunebox[/bold magenta] v{__version__}")
    elif args.command == "history":
        console.print(render_track_table(get_history(), title="Recent Listening History"))
    else:
        from .ui.app import run
        run()


if __name__ == "__main__":
    main()
