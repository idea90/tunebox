"""
Lyrics Service for Tunebox:
- Synchronized LRC lyrics via LRCLIB (lrclib.net)
- NetEase Cloud Music (synced) and YouTube Music / lyrics.ovh (plain) fallbacks
- LRC Parser & Real-time timestamp synchronization
"""
import re
import requests
from urllib.parse import quote
from typing import Dict, Any, List, Optional, Tuple
from .ytmusic import yt_client

_HEADERS = {"User-Agent": "Tunebox/1.4.1"}
_LRC_TAG = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")

def parse_lrc(lrc_text: str) -> List[Dict[str, Any]]:
    """Parse LRC text into a time-sorted list of { time: float, text: str }.

    Handles [mm:ss], [mm:ss.x], [mm:ss.xx], [mm:ss.xxx] and lines carrying
    several timestamps (repeated choruses): "[00:10.00][00:50.00] text".
    """
    if not lrc_text:
        return []

    parsed = []
    for line in lrc_text.splitlines():
        tags = list(_LRC_TAG.finditer(line.strip()))
        if not tags:
            continue
        text = _LRC_TAG.sub("", line.strip()).strip()
        if not text:
            continue
        for m in tags:
            frac = m.group(3)
            seconds = int(m.group(1)) * 60 + int(m.group(2)) + (int(frac) / 10 ** len(frac) if frac else 0.0)
            parsed.append({"time": round(seconds, 3), "text": text})

    parsed.sort(key=lambda x: x["time"])
    return parsed

def _plain(text: str, source: str) -> Dict[str, Any]:
    lines = [{"time": None, "text": l.strip()} for l in text.splitlines() if l.strip()]
    return {"synced": False, "lines": lines, "raw": text, "source": source}


def _synced(lrc: str, source: str) -> Optional[Dict[str, Any]]:
    lines = parse_lrc(lrc)
    return {"synced": True, "lines": lines, "raw": lrc, "source": source} if lines else None


def _from_lrclib_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if item.get("syncedLyrics"):
        res = _synced(item["syncedLyrics"], "LRCLIB")
        if res:
            return res
    if item.get("plainLyrics"):
        return _plain(item["plainLyrics"], "LRCLIB (Plain)")
    return None


def _src_lrclib_get(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    params = {"track_name": ctx["title"]}
    if ctx["artist"]:
        params["artist_name"] = ctx["artist"]
    if ctx["album"]:
        params["album_name"] = ctx["album"]
    if ctx["duration"]:
        params["duration"] = str(ctx["duration"])
    resp = requests.get("https://lrclib.net/api/get", params=params, headers=_HEADERS, timeout=5)
    return _from_lrclib_item(resp.json()) if resp.status_code == 200 else None


def _src_lrclib_search(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    resp = requests.get("https://lrclib.net/api/search", params={"q": ctx["query"]}, headers=_HEADERS, timeout=5)
    if resp.status_code != 200 or not isinstance(resp.json(), list):
        return None
    results = [r for r in resp.json() if isinstance(r, dict)]
    # Prefer a synced hit anywhere in the list over an earlier plain one.
    for item in results:
        if item.get("syncedLyrics"):
            res = _from_lrclib_item(item)
            if res:
                return res
    for item in results:
        res = _from_lrclib_item(item)
        if res:
            return res
    return None


def _src_netease(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """NetEase Cloud Music: large catalogue with synced LRC, no API key."""
    resp = requests.get("https://music.163.com/api/search/get",
                        params={"s": ctx["query"], "type": 1, "limit": 5},
                        headers={**_HEADERS, "Referer": "https://music.163.com/"}, timeout=5)
    songs = (resp.json().get("result") or {}).get("songs") or [] if resp.status_code == 200 else []
    want = ctx["duration"]
    # Closest duration first (ms) to avoid live/remix versions with other timing.
    if want:
        songs.sort(key=lambda s: abs((s.get("duration") or s.get("dt") or 0) / 1000 - want))
    for song in songs[:3]:
        if want and abs((song.get("duration") or song.get("dt") or 0) / 1000 - want) > 8:
            continue
        lr = requests.get("https://music.163.com/api/song/lyric",
                          params={"id": song["id"], "lv": 1},
                          headers={**_HEADERS, "Referer": "https://music.163.com/"}, timeout=5)
        if lr.status_code == 200:
            lrc = (lr.json().get("lrc") or {}).get("lyric") or ""
            res = _synced(lrc, "NetEase")
            if res:
                return res
    return None


def _src_ytmusic(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not ctx["video_id"]:
        return None
    watch_data = yt_client.yt.get_watch_playlist(videoId=ctx["video_id"])
    browse_id = watch_data.get("lyrics")
    if not browse_id:
        return None
    text = yt_client.yt.get_lyrics(browse_id).get("lyrics", "")
    return _plain(text, "YouTube Music") if text else None


def _src_lyrics_ovh(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not ctx["artist"]:
        return None
    resp = requests.get(f"https://api.lyrics.ovh/v1/{quote(ctx['artist'], safe='')}/{quote(ctx['title'], safe='')}",
                        headers=_HEADERS, timeout=6)
    text = (resp.json().get("lyrics") or "").strip() if resp.status_code == 200 else ""
    return _plain(text, "lyrics.ovh") if text else None


# Tried in order. The first synced result wins; otherwise the first plain one is used.
_SOURCES = (_src_lrclib_get, _src_lrclib_search, _src_netease, _src_ytmusic, _src_lyrics_ovh)


def get_lyrics(track_title: str, artist_name: str = "", album_name: str = "", duration: Optional[int] = None, video_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch lyrics from LRCLIB, NetEase, YouTube Music and lyrics.ovh, preferring synced lyrics."""
    # Clean track title (remove (Official Video), [Audio], etc. for better match)
    clean_title = re.sub(r"\(Official.*?\)|\[Official.*?\]|\(Lyric.*?\)|\[Lyric.*?\]|\(Audio\)|\[Audio\]", "", track_title, flags=re.IGNORECASE).strip()
    clean_artist = artist_name.split(",")[0].strip() if artist_name else ""
    ctx = {"title": clean_title, "artist": clean_artist, "album": album_name, "duration": duration,
           "video_id": video_id, "query": f"{clean_title} {clean_artist}".strip()}

    plain_fallback = None
    for source in _SOURCES:
        try:
            res = source(ctx)
        except Exception:
            continue
        if not res:
            continue
        if res["synced"]:
            return res
        plain_fallback = plain_fallback or res
    if plain_fallback:
        return plain_fallback

    return {
        "synced": False,
        "lines": [{"time": None, "text": "No lyrics found for this track."}],
        "raw": "",
        "source": "None"
    }

def get_synced_line_index(lines: List[Dict[str, Any]], current_pos_sec: float) -> int:
    """Given parsed LRC lines and current playback time in seconds, returns the active line index."""
    if not lines or current_pos_sec < 0:
        return 0
        
    last_idx = 0
    for idx, line in enumerate(lines):
        t = line.get("time")
        if t is not None:
            if t <= current_pos_sec:
                last_idx = idx
            else:
                break
    return last_idx
