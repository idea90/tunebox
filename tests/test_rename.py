"""The Metrolist -> Tunebox rename must not lose anyone's library."""
import json
import sqlite3
from pathlib import Path

from tunebox import config as cfg


def make_legacy_home(root: Path) -> Path:
    """A ~/.metrolist as the old version left it, with absolute paths inside."""
    home = root / ".metrolist"
    (home / "downloads").mkdir(parents=True)
    (home / "cache").mkdir()
    song = home / "downloads" / "AJR - The Big Goodbye.mp3"
    song.write_bytes(b"mp3")
    (home / "cache" / "abc.mp3").write_bytes(b"cached")
    conn = sqlite3.connect(str(home / "metrolist.db"))
    conn.execute("CREATE TABLE downloads (video_id TEXT PRIMARY KEY, file_path TEXT)")
    conn.execute("CREATE TABLE favorites (video_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO downloads VALUES ('v1', ?)", (str(song),))
    conn.execute("INSERT INTO downloads VALUES ('v2', ?)", (r"D:\Music\elsewhere.mp3",))   # not inside the old folder
    conn.execute("INSERT INTO favorites VALUES ('fav1')")
    conn.commit()
    conn.close()
    (home / "config.json").write_text(json.dumps({
        "volume": 61, "download_dir": str(home / "downloads"), "cookie_file": str(home / "cookies.txt"),
        "theme": "emerald"}), encoding="utf-8")
    (home / "session.json").write_text(json.dumps({
        "queue": [{"videoId": "v1", "filePath": str(song)}, {"videoId": "v3"}], "index": 0, "position": 12.5}),
        encoding="utf-8")
    return home


def test_library_moves_to_the_new_folder_with_paths_fixed(tmp_path):
    legacy = make_legacy_home(tmp_path)
    new = tmp_path / ".tunebox"
    assert cfg.migrate_legacy_home(legacy, new) == new
    assert not legacy.exists(), "moved, not copied (no doubled disk use)"
    assert (new / "tunebox.db").exists() and not (new / "metrolist.db").exists()
    assert (new / "downloads" / "AJR - The Big Goodbye.mp3").read_bytes() == b"mp3"
    assert (new / "cache" / "abc.mp3").exists()

    conn = sqlite3.connect(str(new / "tunebox.db"))
    paths = dict(conn.execute("SELECT video_id, file_path FROM downloads"))
    favs = conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]
    conn.close()
    assert paths["v1"] == str(new / "downloads" / "AJR - The Big Goodbye.mp3")
    assert Path(paths["v1"]).exists(), "downloads must still be found after the move"
    assert paths["v2"] == r"D:\Music\elsewhere.mp3", "paths outside the old folder are left alone"
    assert favs == 1

    conf = json.loads((new / "config.json").read_text(encoding="utf-8"))
    assert conf["download_dir"] == str(new / "downloads") and conf["cookie_file"] == str(new / "cookies.txt")
    assert conf["volume"] == 61 and conf["theme"] == "emerald"

    sess = json.loads((new / "session.json").read_text(encoding="utf-8"))
    assert sess["queue"][0]["filePath"] == str(new / "downloads" / "AJR - The Big Goodbye.mp3")
    assert sess["position"] == 12.5 and sess["queue"][1] == {"videoId": "v3"}


def test_migration_runs_only_once_and_never_overwrites_a_new_folder(tmp_path):
    legacy = make_legacy_home(tmp_path)
    new = tmp_path / ".tunebox"
    new.mkdir()
    (new / "tunebox.db").write_bytes(b"newer data")
    assert cfg.migrate_legacy_home(legacy, new) == new
    assert legacy.exists() and (new / "tunebox.db").read_bytes() == b"newer data"


def test_fresh_install_without_old_data_just_uses_the_new_folder(tmp_path):
    assert cfg.migrate_legacy_home(tmp_path / ".metrolist", tmp_path / ".tunebox") == tmp_path / ".tunebox"


def test_if_the_move_fails_the_old_folder_is_used_so_nothing_looks_lost(tmp_path, monkeypatch):
    legacy = make_legacy_home(tmp_path)
    def locked(src, dst):
        raise PermissionError("in use by another process")
    monkeypatch.setattr(cfg.os, "replace", locked)
    assert cfg.migrate_legacy_home(legacy, tmp_path / ".tunebox") == legacy
    assert cfg._pick_db_file(legacy) == legacy / "metrolist.db", "the old database name is still found"


def test_old_environment_variable_still_works(monkeypatch, tmp_path):
    monkeypatch.delenv("TUNEBOX_HOME", raising=False)
    monkeypatch.setenv("METROLIST_HOME", str(tmp_path / "old-style"))
    assert cfg._home_from_env() == tmp_path / "old-style"
    monkeypatch.setenv("TUNEBOX_HOME", str(tmp_path / "new-style"))
    assert cfg._home_from_env() == tmp_path / "new-style", "the new name wins when both are set"


def test_swap_prefix_only_matches_whole_folder_names():
    assert cfg._swap_prefix(r"C:\u\.metrolist\downloads\a.mp3", r"C:\u\.metrolist", r"C:\u\.tunebox") == r"C:\u\.tunebox\downloads\a.mp3"
    assert cfg._swap_prefix(r"C:\u\.metrolist-backup\a.mp3", r"C:\u\.metrolist", r"C:\u\.tunebox") == r"C:\u\.metrolist-backup\a.mp3"
    assert cfg._swap_prefix(42, "a", "b") == 42


def test_cli_tables_show_dashes_for_unknown_durations():
    from tunebox.ui.components import render_track_table
    from rich.console import Console
    console = Console(width=100, record=True)
    console.print(render_track_table([{"videoId": "a", "title": "Maybe Man", "artist": "AJR", "duration": "", "duration_seconds": 0}]))
    out = console.export_text()
    assert "--:--" in out and "00:00" not in out
