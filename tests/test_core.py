"""Unit tests for player queue logic, lyrics parsing and source hygiene."""
from pathlib import Path

import pygame
import pytest

from tunebox.core import downloader
from tunebox.core.lyrics import parse_lrc, get_synced_line_index
from tunebox.core.player import player
from tunebox.core.ytmusic import yt_client


def tracks(n):
    return [{"videoId": f"v{i}", "title": f"T{i}", "duration_seconds": 100} for i in range(n)]


@pytest.fixture
def fake_player(monkeypatch):
    """Player with loading stubbed out: records which track would start playing."""
    started = []

    def fake_load(track, seek_start, record_history):
        started.append((track["videoId"], seek_start, record_history))
        player.current_track = track
        player.is_playing = True
        player.is_paused = False
        player.seek_offset = seek_start
        return True

    monkeypatch.setattr(player, "_load_and_play", fake_load)
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)  # monitor thread stays idle
    player.queue, player.queue_index = [], -1
    player.shuffle, player.repeat_mode, player.autoplay = False, "off", False
    player.track_duration = 100.0
    return started


def test_next_walks_queue_and_stops_at_end(fake_player):
    player.play(queue=tracks(3), index=0)
    assert player.next() and player.queue_index == 1
    assert player.next() and player.queue_index == 2
    assert player.next() is False and player.is_playing is False


def test_repeat_all_wraps(fake_player):
    player.repeat_mode = "all"
    player.play(queue=tracks(2), index=1)
    assert player.next() and player.queue_index == 0


def test_repeat_one_replays_only_on_natural_end(fake_player):
    player.repeat_mode = "one"
    player.play(queue=tracks(3), index=0)
    player.next(auto=True)
    assert player.queue_index == 0 and fake_player[-1][0] == "v0"
    player.next(auto=False)               # manual skip still advances
    assert player.queue_index == 1


def test_shuffle_plays_every_song_once_then_ends(fake_player):
    """Regression: shuffle used to pick at random forever (never ended, repeated songs)."""
    player.shuffle = True
    player.play(queue=tracks(5), index=2)
    order = [player.current_track["videoId"]]
    while player.next():
        order.append(player.current_track["videoId"])
        assert len(order) <= 5, "shuffle with repeat off must end after one pass"
    assert sorted(order) == ["v0", "v1", "v2", "v3", "v4"]


def test_autoplay_extends_queue_with_radio(fake_player, monkeypatch):
    player.autoplay = True
    monkeypatch.setattr(yt_client, "get_watch_playlist", lambda vid, limit=10: [{"videoId": "r1", "title": "R1"}])
    player.play(queue=tracks(1), index=0)
    assert player.next() and player.current_track["videoId"] == "r1"
    assert len(player.queue) == 2


def test_previous_restarts_after_three_seconds(fake_player, monkeypatch):
    player.play(queue=tracks(3), index=1)
    monkeypatch.setattr(player, "get_position", lambda: 10.0)
    player.previous()
    assert fake_player[-1] == ("v1", 0.0, False)     # seek to 0, not a new history entry
    monkeypatch.setattr(player, "get_position", lambda: 1.0)
    player.previous()
    assert player.queue_index == 0


def test_seek_does_not_record_history(fake_player, monkeypatch):
    player.play(queue=tracks(1), index=0)
    monkeypatch.setattr(player, "get_position", lambda: 5.0)
    player.seek(20)
    assert fake_player[-1] == ("v0", 25.0, False)


def test_seek_is_clamped_inside_track(fake_player, monkeypatch):
    player.play(queue=tracks(1), index=0)
    monkeypatch.setattr(player, "get_position", lambda: 5.0)
    player.seek(10_000)
    assert fake_player[-1][1] == 99.0
    player.seek(-10_000)
    assert fake_player[-1][1] == 0.0


def test_remove_before_current_keeps_playing_track(fake_player):
    player.play(queue=tracks(4), index=2)
    player.remove_from_queue(0)
    assert player.queue_index == 1 and player.queue[player.queue_index]["videoId"] == "v2"


def test_pause_freezes_position_and_resume_does_not_double_count(monkeypatch):
    """Regression: pause used to fold the position into seek_offset, so resume jumped ahead."""
    state = {"ms": 2200}
    monkeypatch.setattr(pygame.mixer.music, "get_pos", lambda: state["ms"])
    monkeypatch.setattr(pygame.mixer.music, "pause", lambda: None)
    monkeypatch.setattr(pygame.mixer.music, "unpause", lambda: None)
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: True)
    player.is_playing, player.is_paused, player.seek_offset = True, False, 0.0
    player.pause()
    state["ms"] = 2200                      # pygame's clock does not advance while paused
    assert player.get_position() == pytest.approx(2.2)
    player.resume()
    state["ms"] = 3200                      # one more second played
    assert player.get_position() == pytest.approx(3.2)
    player.is_playing = False


def test_parse_lrc_variants_and_ordering():
    lines = parse_lrc("[00:12.50] second\n[00:01.5]first\n[01:02] third\nnot a lyric\n[00:30.123]\n")
    assert [l["text"] for l in lines] == ["first", "second", "third"]
    assert lines[1]["time"] == 12.5 and lines[2]["time"] == 62.0
    assert get_synced_line_index(lines, 20) == 1
    multi = parse_lrc("[00:10.00][00:50.00] chorus")
    assert [(l['time'], l['text']) for l in multi] == [(10.0, 'chorus'), (50.0, 'chorus')]


def test_filename_sanitizing_strips_windows_illegal_chars():
    assert downloader._safe_name('AC/DC: "Back" in <Black>?') == "AC_DC_ _Back_ in _Black__"
    assert downloader._safe_name("...") == "Track"


def test_ui_sources_have_no_lost_glyph_placeholders():
    """Regression: every emoji/box char had been replaced by a literal '?' in the UI source."""
    ui = Path(__file__).resolve().parent.parent / "tunebox"
    offenders = []
    for path in list(ui.glob("ui/*.py")) + [ui / "main.py"]:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if '"?"' in line or "'?'" in line or "? " in line.split("#")[0] and 'append("?' in line:
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_write_tags_embeds_metadata_into_mp3(tmp_path, monkeypatch):
    """Regression: README promised ID3 tags but mutagen was never used."""
    import subprocess
    from mutagen.id3 import ID3
    ffmpeg = downloader.get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    mp3 = tmp_path / "t.mp3"
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", str(mp3)],
                   check=True, capture_output=True)
    monkeypatch.setattr(downloader, "_fetch_cover", lambda url: b"\xff\xd8fakejpeg")
    track = {"title": "Yellow", "artist": "Coldplay", "album": {"name": "Parachutes"}, "thumbnail": "http://x"}
    assert downloader.write_tags(str(mp3), track) is True
    tags = ID3(str(mp3))
    assert str(tags["TIT2"]) == "Yellow" and str(tags["TPE1"]) == "Coldplay" and str(tags["TALB"]) == "Parachutes"
    assert tags["APIC:Cover"].data.startswith(b"\xff\xd8")
    assert not any(k.startswith("TXXX") for k in tags), "ffmpeg container junk should be gone"
