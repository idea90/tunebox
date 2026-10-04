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


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    albumart._LOOKUP.clear()
    yield
    albumart._LOOKUP.clear()


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

    albumart._LOOKUP.clear()
    calls.clear()
    fake_web(monkeypatch, itunes(), deezer(), calls=calls)    # reachable but unknown song: a real miss
    assert albumart.find_cover_url(track()) is None
    albumart.find_cover_url(track())
    assert len(calls) == 2                                    # one iTunes + one Deezer, then cached

    albumart._LOOKUP.clear()
    fake_web(monkeypatch, None, None)                         # offline: must retry next time
    assert albumart.find_cover_url(track()) is None
    assert albumart._LOOKUP == {}


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
