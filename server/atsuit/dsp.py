"""Caption audio processing: mic gain, a graphic EQ and the analyser (RTA)
behind the Audio & EQ tab. Ported from AT-LiveCaption's app/core/dsp.py.

LiveCaption filtered with scipy's lfilter (one RBJ peaking biquad per band).
AT-SUIT has no scipy, and its audio arrives at 16 kHz over the network, so
the same ten-band curve is built from the same RBJ biquads but applied as one
linear-phase FIR (frequency sampling + Hann window), with overlap-add state
kept across chunks so the stream stays continuous. Bands at or above the
Nyquist frequency (8 kHz at 16 kHz) can't be heard by the recogniser and are
ignored, which is why AT-SUIT shows eight bands, not ten.

Pure numpy: unit tested with synthetic sines, no model or sound card needed.
"""
from __future__ import annotations

import math
import threading

import numpy as np

SAMPLE_RATE = 16000
EQ_BANDS_HZ = [31, 62, 125, 250, 500, 1000, 2000, 4000]  # LiveCaption's ISO bands below 8 kHz
BAND_Q = 1.4
MAX_BAND_GAIN_DB = 12.0
MAX_GAIN_DB = 20.0
FIR_TAPS = 1023  # ~32 ms latency at 16 kHz; fine enough for the 62 Hz band

RTA_NUM_BANDS = 31
RTA_FREQ_MIN = 20.0
RTA_FREQ_MAX = 20000.0
RTA_FLOOR_DB = -60.0
SILENCE_FLOOR_DB = -60.0


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def peaking_coeffs(freq_hz: float, gain_db: float, q: float, sample_rate: int):
    """RBJ Audio EQ Cookbook peaking filter, normalised so a[0] == 1."""
    a = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq_hz / sample_rate
    alpha = math.sin(w0) / (2 * q)
    cos_w0 = math.cos(w0)
    b = np.array([1 + alpha * a, -2 * cos_w0, 1 - alpha * a])
    den = np.array([1 + alpha / a, -2 * cos_w0, 1 - alpha / a])
    return b / den[0], den / den[0]


def eq_response(band_gains_db: list[float], freqs: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Magnitude response of the cascaded peaking filters at `freqs` (Hz)."""
    z = np.exp(-1j * 2 * np.pi * freqs / sample_rate)
    h = np.ones_like(freqs, dtype=complex)
    for f0, g in zip(EQ_BANDS_HZ, band_gains_db):
        if not g or f0 >= sample_rate / 2:
            continue
        b, a = peaking_coeffs(f0, g, BAND_Q, sample_rate)
        h *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return np.abs(h)


def design_fir(band_gains_db: list[float], sample_rate: int = SAMPLE_RATE, taps: int = FIR_TAPS) -> np.ndarray | None:
    """A linear-phase FIR with the EQ's magnitude response; None when flat."""
    if not any(abs(g) > 1e-6 for g in band_gains_db):
        return None
    n_fft = 1 << int(math.ceil(math.log2(taps * 4)))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
    mag = eq_response(band_gains_db, freqs, sample_rate)
    impulse = np.fft.irfft(mag, n_fft)
    impulse = np.roll(impulse, taps // 2)[:taps]  # centre the (zero-phase) response
    return (impulse * np.hanning(taps)).astype(np.float64)


class GraphicEQ:
    """Gain + N-band EQ for one room's stream. The API thread calls set_*, the
    audio loop calls process(); each set swaps the filter in one step."""

    def __init__(self, band_gains_db: list[float] | None = None, gain_db: float = 0.0,
                 sample_rate: int = SAMPLE_RATE) -> None:
        self._lock = threading.Lock()
        self.sample_rate = sample_rate
        self._gain_db = 0.0
        self.gain_linear = 1.0
        self._bands = [0.0] * len(EQ_BANDS_HZ)
        self._fir: np.ndarray | None = None
        self._tail = np.zeros(0)
        self.set(gain_db=gain_db, band_gains_db=band_gains_db)

    def set(self, gain_db: float | None = None, band_gains_db: list[float] | None = None) -> None:
        with self._lock:
            if gain_db is not None:
                self._gain_db = clamp(gain_db, -MAX_GAIN_DB, MAX_GAIN_DB)
                self.gain_linear = 10 ** (self._gain_db / 20.0)
            if band_gains_db is not None:
                bands = [clamp(g, -MAX_BAND_GAIN_DB, MAX_BAND_GAIN_DB) for g in list(band_gains_db)[:len(EQ_BANDS_HZ)]]
                bands += [0.0] * (len(EQ_BANDS_HZ) - len(bands))
                if bands != self._bands:
                    self._bands = bands
                    self._fir = design_fir(bands, self.sample_rate)
                    self._tail = np.zeros(0)

    def set_gain_db(self, gain_db: float) -> None:
        self.set(gain_db=gain_db)

    def set_band_gain_db(self, index: int, gain_db: float) -> None:
        if 0 <= index < len(EQ_BANDS_HZ):
            bands = self.band_gains_db()
            bands[index] = gain_db
            self.set(band_gains_db=bands)

    def band_gains_db(self) -> list[float]:
        with self._lock:
            return list(self._bands)

    @property
    def gain_db(self) -> float:
        return self._gain_db

    def process(self, samples: np.ndarray) -> np.ndarray:
        """float32 samples in [-1, 1] -> processed float32, same length."""
        with self._lock:
            gain, fir = self.gain_linear, self._fir
        out = np.asarray(samples, dtype=np.float64) * gain
        if fir is not None and out.size:
            full = np.convolve(out, fir)  # len n + taps - 1
            tail = self._tail
            if tail.size:
                k = min(tail.size, full.size)
                full[:k] += tail[:k]
                if tail.size > full.size:  # very short chunk: carry the rest
                    full = np.concatenate([full, tail[full.size:]])
            out, self._tail = full[:samples.size], full[samples.size:]
        return np.clip(out, -1.0, 1.0).astype(np.float32)


def pcm16_to_float(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0


def dbfs(samples: np.ndarray) -> float:
    """Level meter reading, floored at -60 dBFS like LiveCaption's."""
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0
    if rms <= 1e-8:
        return SILENCE_FLOOR_DB
    return round(max(SILENCE_FLOOR_DB, 20.0 * math.log10(rms)), 1)


_EDGES: dict[int, np.ndarray] = {}


def rta_band_edges(sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    edges = _EDGES.get(sample_rate)
    if edges is None:
        f_max = min(RTA_FREQ_MAX, sample_rate / 2.0)
        edges = _EDGES[sample_rate] = np.logspace(math.log10(RTA_FREQ_MIN), math.log10(f_max), RTA_NUM_BANDS + 1)
    return edges


def compute_rta_bands(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> list[float]:
    """Log-spaced spectrum in dBFS (a full-scale sine reads ~0 dB)."""
    n = samples.size
    if n < 8:
        return [RTA_FLOOR_DB] * RTA_NUM_BANDS
    window = np.hanning(n)
    amplitude = np.abs(np.fft.rfft(samples * window)) * (2.0 / (window.sum() or 1.0))
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    edges = rta_band_edges(sample_rate)
    out = []
    for i in range(RTA_NUM_BANDS):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        peak = amplitude[mask].max() if mask.any() else 0.0
        rms = peak / math.sqrt(2)
        db = 20 * math.log10(rms) if rms > 1e-8 else RTA_FLOOR_DB
        out.append(round(max(RTA_FLOOR_DB, min(0.0, db)), 1))
    return out


def settings_out(eq: GraphicEQ | None = None) -> dict:
    return {"gain_db": eq.gain_db if eq else 0.0, "eq_bands_hz": EQ_BANDS_HZ,
            "eq_band_gains_db": eq.band_gains_db() if eq else [0.0] * len(EQ_BANDS_HZ),
            "max_gain_db": MAX_GAIN_DB, "max_band_gain_db": MAX_BAND_GAIN_DB,
            "rta_num_bands": RTA_NUM_BANDS, "rta_freq_min": RTA_FREQ_MIN,
            "rta_freq_max": min(RTA_FREQ_MAX, SAMPLE_RATE / 2), "rta_floor_db": RTA_FLOOR_DB}
