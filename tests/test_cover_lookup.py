"""Official album-cover lookup (iTunes, then Deezer). The network is always faked."""
import pytest

from tunebox.core import albumart, downloader

pytestmark = pytest.mark.real_cover_lookup

ITUNES_ART = "https://is1-ssl.mzstatic.com/image/thumb/Music/x/100x100bb.jpg"
ITUNES_BIG = "https://is1-ssl.mzstatic.com/image/thumb/Music/x/1000x1000bb.jpg"


class Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def itunes(*rows):
    return {"results": [{"trackName": t, "artistName": a, "collectionName": c, "artworkUrl100": ITUNES_ART}
                        for t, a, c in rows]}


def deezer(*rows):
    return {"data": [{"title": t, "artist": {"name": a}, "album": {"title": c, "cover_xl": f"https://dz/{i}.jpg"}}
                     for i, (t, a, c) in enumerate(rows)]}


def track(title="Get Lucky", artist="Daft Punk", album="Random Access Memories", **extra):
    t = {"videoId": "v1", "title": title, "artist": artist, "artists": [{"name": artist, "id": None}],
         "album": {"name": album, "id": None} if album else None,
         "thumbnail": "https://i.ytimg.com/vi/v1/hqdefault.jpg"}
    t.update(extra)
    return t


def forget_everything():
    """Empty the lookup cache in memory and on disk."""
    albumart._LOOKUP.clear()
    albumart._disk = None
    albumart.COVER_LOOKUP_FILE.unlink(missing_ok=True)


def restart():
    """What a new launch looks like: memory is empty, the files on disk are kept."""
    albumart._LOOKUP.clear()
    albumart._CACHE.clear()
    albumart._disk = None


def fake_web(monkeypatch, itunes_payload=None, deezer_payload=None, calls=None):
    def get(url, params=None, timeout=0):
        if calls is not None:
            calls.append((url, params))
        if url == albumart.ITUNES_URL:
            if itunes_payload is None:
                raise ConnectionError("offline")
            return Resp(itunes_payload)
        if url == albumart.DEEZER_URL:
            if deezer_payload is None:
                raise ConnectionError("offline")
            return Resp(deezer_payload)
        raise AssertionError(f"unexpected url {url}")
    monkeypatch.setattr(albumart.requests, "get", get)


def test_itunes_cover_is_requested_at_1000px(monkeypatch):
    calls = []
    fake_web(monkeypatch, itunes(("Get Lucky", "Daft Punk", "Random Access Memories")), calls=calls)
    assert albumart.find_cover_url(track()) == ITUNES_BIG
    url, params = calls[0]
    assert url == albumart.ITUNES_URL and params["term"] == "daft punk get lucky" and params["entity"] == "song"


def test_falls_back_to_deezer_when_itunes_has_no_match(monkeypatch):
    fake_web(monkeypatch, itunes(("Other Song", "Other Artist", "X")),
             deezer(("Get Lucky", "Daft Punk", "Random Access Memories")))
    assert albumart.find_cover_url(track()) == "https://dz/0.jpg"


def test_falls_back_to_deezer_when_itunes_is_down(monkeypatch):
    fake_web(monkeypatch, None, deezer(("Get Lucky", "Daft Punk", "RAM")))
    assert albumart.find_cover_url(track()) == "https://dz/0.jpg"


def test_wrong_artist_or_title_is_never_accepted(monkeypatch):
    fake_web(monkeypatch, itunes(("Get Lucky", "Some Cover Band", "Tribute"), ("Get Lucky Remix Pack", "Nobody", "Z")),
             deezer())
    assert albumart.find_cover_url(track()) is None


def test_same_album_beats_a_compilation(monkeypatch):
    payload = {"results": [
        {"trackName": "Get Lucky", "artistName": "Daft Punk", "collectionName": "Now 85", "artworkUrl100": "https://x/100x100bb.jpg"},
        {"trackName": "Get Lucky", "artistName": "Daft Punk", "collectionName": "Random Access Memories", "artworkUrl100": "https://y/100x100bb.jpg"},
    ]}
    fake_web(monkeypatch, payload)
    assert albumart.find_cover_url(track()) == "https://y/1000x1000bb.jpg"


@pytest.mark.parametrize("title,artist", [
    ("Get Lucky (Official Video)", "Daft Punk - Topic"),
    ("Get Lucky - Remastered 2013", "Daft Punk"),
    ("GET LUCKY [feat. Pharrell Williams]", "Daft Punk"),
])
def test_titles_and_artists_are_cleaned_before_matching(monkeypatch, title, artist):
    fake_web(monkeypatch, itunes(("Get Lucky", "Daft Punk", "Random Access Memories")))
    assert albumart.find_cover_url(track(title=title, artist=artist, album=None)) == ITUNES_BIG


def test_accents_and_symbols_do_not_block_a_match(monkeypatch):
    fake_web(monkeypatch, itunes(("Beyoncé: Halo!", "Beyoncé", "I Am... Sasha Fierce")))
    assert albumart.find_cover_url(track(title="Beyonce Halo", artist="Beyonce", album=None)) == ITUNES_BIG


def test_results_and_misses_are_remembered_but_errors_are_not(monkeypatch):
    calls = []
    fake_web(monkeypatch, itunes(("Get Lucky", "Daft Punk", "RAM")), calls=calls)
    albumart.find_cover_url(track())
    albumart.find_cover_url(track())
    assert len(calls) == 1                                    # second call served from memory

    forget_everything()
    calls.clear()
    fake_web(monkeypatch, itunes(), deezer(), calls=calls)    # reachable but unknown song: a real miss
    assert albumart.find_cover_url(track()) is None
    albumart.find_cover_url(track())
    assert len(calls) == 2                                    # one iTunes + one Deezer, then cached

    forget_everything()
    fake_web(monkeypatch, None, None)                         # offline: must retry next time
    assert albumart.find_cover_url(track()) is None
    assert albumart._LOOKUP == {} and not albumart.COVER_LOOKUP_FILE.exists()


def test_setting_turns_the_lookup_off(monkeypatch):
    calls = []
    fake_web(monkeypatch, itunes(("Get Lucky", "Daft Punk", "RAM")), calls=calls)
    monkeypatch.setattr(albumart.config, "get", lambda key, default=None: False if key == "online_covers" else default)
    assert albumart.find_cover_url(track()) is None and calls == []


def test_a_video_frame_is_never_used_as_cover(monkeypatch):
    fake_web(monkeypatch, itunes(), deezer())
    assert albumart.best_cover_url(track()) is None           # ytimg thumbnail rejected
    assert albumart.fetch_image(track()) is None


def test_youtube_music_square_art_is_the_last_resort(monkeypatch):
    fake_web(monkeypatch, itunes(), deezer())
    t = track(thumbnail="https://lh3.googleusercontent.com/abc=w120-h120")
    assert albumart.best_cover_url(t) == "https://lh3.googleusercontent.com/abc=w400-h400"


def test_official_cover_wins_over_the_youtube_thumbnail(monkeypatch):
    fake_web(monkeypatch, itunes(("Get Lucky", "Daft Punk", "Random Access Memories")))
    t = track(thumbnail="https://lh3.googleusercontent.com/abc=w120-h120")
    assert albumart.best_cover_url(t) == ITUNES_BIG


def test_fetch_image_downloads_the_official_cover(monkeypatch):
    import io
    from PIL import Image
    albumart._CACHE.clear()
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, "PNG")
    downloaded = []

    class Img(Resp):
        content = buf.getvalue()

    def get(url, params=None, timeout=0):
        if url in (albumart.ITUNES_URL, albumart.DEEZER_URL):
            return Resp(itunes(("Get Lucky", "Daft Punk", "RAM")))
        downloaded.append(url)
        return Img({})
    monkeypatch.setattr(albumart.requests, "get", get)
    assert albumart.fetch_image(track()).size == (8, 8)
    assert downloaded == [ITUNES_BIG]


def test_downloads_embed_the_official_cover(monkeypatch):
    fake_web(monkeypatch, itunes(("Get Lucky", "Daft Punk", "Random Access Memories")))
    seen = []
    monkeypatch.setattr(downloader, "_fetch_cover", lambda url: seen.append(url) or None)
    downloader.write_tags("/nonexistent/song.mp3", track())
    assert seen == [ITUNES_BIG]


# ------------------------------------------------------------------ disk cache

def png(color=(200, 10, 10), size=(8, 8)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def web_with_image(monkeypatch, downloaded):
    class Img(Resp):
        content = png()

    def get(url, params=None, timeout=0):
        if url in (albumart.ITUNES_URL, albumart.DEEZER_URL):
            downloaded.append("lookup")
            return Resp(itunes(("Get Lucky", "Daft Punk", "RAM")))
        downloaded.append(url)
        return Img({})
    monkeypatch.setattr(albumart.requests, "get", get)


def test_lookup_survives_a_restart_without_any_network(monkeypatch):
    calls = []
    fake_web(monkeypatch, itunes(("Get Lucky", "Daft Punk", "RAM")), calls=calls)
    assert albumart.find_cover_url(track()) == ITUNES_BIG
    restart()
    fake_web(monkeypatch, None, None, calls=calls)            # now offline
    calls.clear()
    assert albumart.find_cover_url(track()) == ITUNES_BIG and calls == []


def test_a_miss_is_retried_after_a_week_but_not_before(monkeypatch):
    calls = []
    fake_web(monkeypatch, itunes(), deezer(), calls=calls)
    assert albumart.find_cover_url(track()) is None
    restart()
    calls.clear()
    assert albumart.find_cover_url(track()) is None and calls == []      # remembered across the restart

    restart()
    later = albumart.time.time() + albumart.MISS_TTL + 60
    monkeypatch.setattr(albumart.time, "time", lambda: later)
    fake_web(monkeypatch, itunes(("Get Lucky", "Daft Punk", "RAM")), calls=calls)
    assert albumart.find_cover_url(track()) == ITUNES_BIG and calls     # a week later it looks again


def test_the_lookup_file_is_bounded_and_survives_corruption(monkeypatch):
    monkeypatch.setattr(albumart, "LOOKUP_DISK_MAX", 3)
    for i in range(6):
        albumart._disk_put(f"key{i}", f"https://x/{i}")
    assert len(albumart._disk_lookup()) == 3

    restart()
    albumart.COVER_LOOKUP_FILE.write_text("{not json", encoding="utf-8")
    assert albumart._disk_get("anything") is albumart._UNKNOWN          # unreadable file: treated as empty


def test_cover_image_is_saved_and_reused_without_downloading(monkeypatch):
    downloaded = []
    web_with_image(monkeypatch, downloaded)
    assert albumart.fetch_image(track()).size == (8, 8)
    assert downloaded.count(ITUNES_BIG) == 1 and len(list(albumart.COVERS_DIR.glob("*.img"))) == 1

    restart()
    downloaded.clear()
    fake_web(monkeypatch, None, None)                          # offline after the restart
    img = albumart.fetch_image(track())
    assert img is not None and img.size == (8, 8) and downloaded == []


def test_a_corrupt_cover_file_is_replaced_by_a_fresh_download(monkeypatch):
    downloaded = []
    web_with_image(monkeypatch, downloaded)
    albumart.fetch_image(track())
    (path,) = albumart.COVERS_DIR.glob("*.img")
    path.write_bytes(b"not an image")
    restart()
    downloaded.clear()
    assert albumart.fetch_image(track()) is not None
    assert downloaded.count(ITUNES_BIG) == 1 and path.read_bytes() == png()


def test_least_recently_used_cover_files_are_evicted(monkeypatch):
    import os
    monkeypatch.setattr(albumart, "COVER_FILES_MAX", 3)
    urls = [f"https://x/{i}.jpg" for i in range(3)]
    for i, u in enumerate(urls):
        albumart._write_cover(u, png())
        os.utime(albumart._cover_path(u), (1000 + i, 1000 + i))
    assert albumart._read_cover(urls[0]) is not None           # touching it makes it the freshest
    albumart._write_cover("https://x/new.jpg", png())
    left = {p.name for p in albumart.COVERS_DIR.glob("*.img")}
    assert albumart._cover_path(urls[1]).name not in left       # the stalest one went
    assert {albumart._cover_path(urls[0]).name, albumart._cover_path(urls[2]).name,
            albumart._cover_path("https://x/new.jpg").name} == left
