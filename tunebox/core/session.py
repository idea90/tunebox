"""
Save and restore the playback session (queue, current song, position) so closing
the app doesn't lose your place. Written atomically so a crash can't corrupt it.
"""
import json
import os
from typing import Any, Dict, List

from ..config import SESSION_FILE

MAX_QUEUE = 300


def snapshot(player) -> Dict[str, Any]:
    with player._lock:
        queue = list(player.queue)
        index = player.queue_index
    # Keep the playing song inside the saved window if the queue is huge.
    start = 0
    if len(queue) > MAX_QUEUE:
        start = max(0, min(index - MAX_QUEUE // 2, len(queue) - MAX_QUEUE))
        queue = queue[start:start + MAX_QUEUE]
    return {
        "version": 1,
        "queue": queue,
        "index": index - start if index >= 0 else -1,
        "position": round(player.get_position(), 1) if player.current_track else 0.0,
    }


def save(player) -> bool:
    """Persist the session. Returns False (and leaves the old file alone) on any error."""
    try:
        data = snapshot(player)
        tmp = SESSION_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, SESSION_FILE)
        return True
    except Exception:
        return False


def load() -> Dict[str, Any]:
    """Saved session, or an empty one if missing/corrupt."""
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        queue: List[Dict[str, Any]] = [t for t in data.get("queue", []) if isinstance(t, dict) and t.get("videoId")]
        return {"queue": queue, "index": int(data.get("index", 0)), "position": float(data.get("position", 0.0))}
    except Exception:
        return {"queue": [], "index": -1, "position": 0.0}


def restore(player) -> bool:
    """Load the saved session into the player (paused, ready to resume). True if anything was restored."""
    data = load()
    if not data["queue"]:
        return False
    player.restore(data["queue"], data["index"], data["position"])
    return True
