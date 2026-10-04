"""
Song spectrum for the visualizer.

pygame plays music without exposing the samples, so instead each song is analysed once when it
starts: the cached file is decoded with ffmpeg and split into FPS frames per second, each holding
BANDS frequency bands (log-spaced, 40 Hz - 11 kHz) scaled to 0..1. The visualizer then shows the
frame that matches the playback position, which keeps it in sync through seeks and pauses.
"""
import subprocess
import threading
from collections import OrderedDict
from typing import Optional

import numpy as np

from .downloader import get_ffmpeg_path

FPS = 30                 # analysis frames per second of audio
BANDS = 64               # frequency bands per frame (the widget resamples to its width)
SAMPLE_RATE = 22050
WINDOW = 2048            # ~93 ms window: enough resolution for bass notes
LOW_HZ, HIGH_HZ = 40.0, 11000.0
DYNAMIC_RANGE_DB = 45.0  # quietest-to-loudest span shown by a bar

_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
_CACHE_MAX = 4
_lock = threading.Lock()


def _decode(path: str) -> Optional[np.ndarray]:
    """Mono float32 samples at SAMPLE_RATE, or None if ffmpeg is missing or the file can't be read."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=120)
    except Exception:
        return None
    if proc.returncode != 0 or len(proc.stdout) < 2:
        return None
    raw = proc.stdout[: len(proc.stdout) // 2 * 2]
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _band_edges() -> np.ndarray:
    """FFT bin index where each log-spaced band starts/ends (every band at least one bin wide)."""
    freqs = np.geomspace(LOW_HZ, HIGH_HZ, BANDS + 1)
    bins = np.round(freqs / (SAMPLE_RATE / WINDOW)).astype(int)
    for i in range(1, len(bins)):
        bins[i] = max(bins[i], bins[i - 1] + 1)
    return np.clip(bins, 1, WINDOW // 2)


def band_centres_hz() -> np.ndarray:
    """Real centre frequency of each band, from the FFT bins it actually covers. At the bass end there are
    fewer bins than bands, so bands get nudged up; their nominal log-spaced frequency would be wrong there."""
    edges = _band_edges()
    bin_hz = SAMPLE_RATE / WINDOW
    return (edges[:-1] + edges[1:] - 1) / 2.0 * bin_hz


def spectrum_from_samples(samples: np.ndarray) -> np.ndarray:
    """(frames, BANDS) array of loudness per band, scaled to 0..1 over the whole song."""
    hop = SAMPLE_RATE // FPS
    n_frames = max(1, int(np.ceil(len(samples) / hop)))
    padded = np.concatenate([np.zeros(WINDOW // 2, np.float32), samples, np.zeros(WINDOW, np.float32)])
    window = np.hanning(WINDOW).astype(np.float32)
    edges = _band_edges()
    out = np.empty((n_frames, BANDS), dtype=np.float32)

    chunk = 512                                        # frames per FFT batch: bounded memory for long songs
    for start in range(0, n_frames, chunk):
        stop = min(n_frames, start + chunk)
        idx = np.arange(start, stop)[:, None] * hop + np.arange(WINDOW)[None, :]
        mags = np.abs(np.fft.rfft(padded[idx] * window, axis=1))
        csum = np.concatenate([np.zeros((mags.shape[0], 1), np.float32), np.cumsum(mags, axis=1)], axis=1)
        out[start:stop] = (csum[:, edges[1:]] - csum[:, edges[:-1]]) / (edges[1:] - edges[:-1])

    db = 20.0 * np.log10(out + 1e-7)
    # Music loses roughly 3 dB of energy per octave going up, so raw bars would be all bass. Add that back
    # (+3 dB/octave around 1 kHz), then use ONE scale for every band so the loudest band really is the
    # loudest bar: per-band scaling would blow small leakage up into full-height bars.
    db = db + 3.0 * np.log2(band_centres_hz() / 1000.0)[None, :]
    top = float(np.percentile(db, 99.7))
    level = np.clip((db - (top - DYNAMIC_RANGE_DB)) / DYNAMIC_RANGE_DB, 0.0, 1.0)
    level[out < 1e-6] = 0.0                            # digital silence stays silent
    return level.astype(np.float32)


def analyze(path: str) -> Optional[np.ndarray]:
    """Spectrum for an audio file (cached for the last few songs). None if it can't be analysed."""
    with _lock:
        if path in _cache:
            _cache.move_to_end(path)
            return _cache[path]
    samples = _decode(path)
    if samples is None or len(samples) < SAMPLE_RATE // 10:
        return None
    spec = spectrum_from_samples(samples)
    with _lock:
        _cache[path] = spec
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return spec


def frame_at(spec: Optional[np.ndarray], seconds: float) -> Optional[np.ndarray]:
    """The band levels at a playback position (None past the end / without a spectrum)."""
    if spec is None or seconds < 0:
        return None
    i = int(seconds * FPS)
    return spec[i] if i < len(spec) else None
