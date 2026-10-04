"""
Configuration manager for Tunebox
"""
import os
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

# Before the rename to Tunebox the app was called Metrolist and kept its data in ~/.metrolist.
LEGACY_APP_DIR = Path.home() / ".metrolist"
LEGACY_DB_NAME = "metrolist.db"


def _swap_prefix(value: Any, old: str, new: str) -> Any:
    """`value` with a leading `old` folder replaced by `new` (only whole folder names, either slash)."""
    if not isinstance(value, str):
        return value
    if value == old:
        return new
    for sep in ("\\", "/"):
        if value.startswith(old + sep):
            return new + value[len(old):]
    return value


def _rewrite_stored_paths(home: Path, old: str, new: str) -> None:
    """Absolute paths saved inside the data folder still point at the old folder: fix them."""
    config_file = home / "config.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                fixed = {k: _swap_prefix(v, old, new) for k, v in data.items()}
                if fixed != data:
                    tmp = config_file.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(fixed, indent=4), encoding="utf-8")
                    os.replace(tmp, config_file)
        except Exception:
            pass

    for db in (home / "tunebox.db", home / LEGACY_DB_NAME):
        if not db.exists():
            continue
        try:
            conn = sqlite3.connect(str(db))
            try:
                rows = conn.execute("SELECT rowid, file_path FROM downloads").fetchall()
                for rowid, path in rows:
                    moved = _swap_prefix(path, old, new)
                    if moved != path:
                        conn.execute("UPDATE downloads SET file_path = ? WHERE rowid = ?", (moved, rowid))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass                              # e.g. no downloads table yet

    session_file = home / "session.json"
    if session_file.exists():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            for track in data.get("queue", []):
                if isinstance(track, dict) and "filePath" in track:
                    track["filePath"] = _swap_prefix(track["filePath"], old, new)
            session_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass


def migrate_legacy_home(legacy: Path, new: Path) -> Path:
    """Move the old ~/.metrolist to ~/.tunebox once, keeping your library. Returns the folder to use.

    The move is a rename (instant, nothing copied). If it can't happen (for example an old copy is still
    running and holds files open), the old folder is used as-is this time so nothing looks lost, and the
    move is retried on the next launch.
    """
    if new.exists() or not legacy.is_dir():
        return new
    try:
        os.replace(legacy, new)
    except OSError:
        return legacy
    old_db, new_db = new / LEGACY_DB_NAME, new / "tunebox.db"
    if old_db.exists() and not new_db.exists():
        try:
            os.replace(old_db, new_db)
        except OSError:
            pass                              # _pick_db_file falls back to the old name
    _rewrite_stored_paths(new, str(legacy), str(new))
    return new


def _home_from_env() -> Optional[Path]:
    for var in ("TUNEBOX_HOME", "METROLIST_HOME"):     # METROLIST_HOME: the name before the rename
        if os.environ.get(var):
            return Path(os.environ[var])
    return None


def _pick_db_file(home: Path) -> Path:
    new = home / "tunebox.db"
    old = home / LEGACY_DB_NAME
    return old if old.exists() and not new.exists() else new


APP_DIR = _home_from_env() or migrate_legacy_home(LEGACY_APP_DIR, Path.home() / ".tunebox")
CACHE_DIR = APP_DIR / "cache"
DOWNLOADS_DIR = APP_DIR / "downloads"
CONFIG_FILE = APP_DIR / "config.json"
DB_FILE = _pick_db_file(APP_DIR)
SESSION_FILE = APP_DIR / "session.json"
AUTH_FILE = APP_DIR / "ytmusic_auth.json"   # written by `tunebox login`

APP_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG: Dict[str, Any] = {
    "volume": 80,
    "audio_quality": "high",
    "theme": "metro_dark",
    "cache_enabled": True,
    "max_cache_size_mb": 2048,
    "autoplay_radio": True,
    "repeat_mode": "off",
    "shuffle": False,
    "download_format": "mp3",
    "download_dir": str(DOWNLOADS_DIR),
    "cookie_file": "",
    "normalize_volume": True,   # loudness-normalize audio as it is cached/downloaded
    "gapless": True,
    "viz_style": "bars",        # bars | mirror | line | off
    "viz_colors": "theme",      # theme | rainbow | fire | mono
    "art_style": "auto",        # auto | ascii | blocks | off  (auto = real image if the terminal can, else ascii)
    "search_history": [],
}

class Config:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._data = DEFAULT_CONFIG.copy()
            cls._instance.load()
        return cls._instance

    load_error: str = ""

    def load(self) -> None:
        if not CONFIG_FILE.exists():
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("config.json must contain a JSON object")
            self._data.update(loaded)
        except Exception as e:
            # Keep the unreadable file for the user instead of silently overwriting it on the next save.
            backup = CONFIG_FILE.with_suffix(".json.corrupt")
            try:
                os.replace(CONFIG_FILE, backup)
            except OSError:
                pass
            self.load_error = f"config.json could not be read ({e}); defaults are in use. The old file was kept as {backup.name}."
        self._sanitize()

    def _sanitize(self) -> None:
        """Hand-edited values of the wrong type/range fall back to defaults instead of crashing the app."""
        d = self._data
        try:
            d["volume"] = max(0, min(100, int(d.get("volume", 80))))
        except (TypeError, ValueError):
            d["volume"] = DEFAULT_CONFIG["volume"]
        mode = str(d.get("repeat_mode", "off")).lower()
        d["repeat_mode"] = mode if mode in ("off", "all", "one") else "off"
        for key in ("shuffle", "autoplay_radio", "cache_enabled", "normalize_volume", "gapless"):
            if not isinstance(d.get(key), bool):
                d[key] = DEFAULT_CONFIG[key]
        if d.get("viz_style") not in ("bars", "mirror", "line", "off"):
            d["viz_style"] = DEFAULT_CONFIG["viz_style"]
        if d.get("viz_colors") not in ("theme", "rainbow", "fire", "mono"):
            d["viz_colors"] = DEFAULT_CONFIG["viz_colors"]
        if d.get("art_style") not in ("auto", "ascii", "blocks", "off"):
            d["art_style"] = DEFAULT_CONFIG["art_style"]
        if not isinstance(d.get("search_history"), list):
            d["search_history"] = []

    def save(self) -> None:
        """Write atomically: a crash mid-save leaves the previous config intact, never a truncated file."""
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4)
            os.replace(tmp, CONFIG_FILE)
        except Exception:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default if default is not None else DEFAULT_CONFIG.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def add_search(self, query: str, keep: int = 30) -> None:
        """Remember a search, most recent first, without duplicates."""
        q = query.strip()
        if not q:
            return
        hist = [h for h in self._data.get("search_history", []) if h.lower() != q.lower()]
        self.set("search_history", ([q] + hist)[:keep])

    def all(self) -> Dict[str, Any]:
        return self._data.copy()

config = Config()
