"""Server-side speech recognition (sherpa-onnx streaming Zipformer), shared by
every room. Model handling and vocabulary boosting come from AT-LiveCaption;
the difference is that audio arrives from nodes over the network instead of
a local sound card."""
from __future__ import annotations

import tarfile
import threading
import urllib.request
from pathlib import Path

import numpy as np

from . import config

try:
    import sherpa_onnx  # type: ignore
except Exception:  # pragma: no cover - the slim image has no ASR
    sherpa_onnx = None

SAMPLE_RATE = 16000
WORD_BOUNDARY = "▁"


def model_url(name: str) -> str:
    return f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{name}.tar.bz2"


def _find(model_dir: Path, role: str) -> Path | None:
    for pattern in (f"{role}*.int8.onnx", f"{role}*.onnx"):
        matches = sorted(model_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def model_files(model_dir: Path) -> dict[str, Path] | None:
    tokens = model_dir / "tokens.txt"
    parts = {r: _find(model_dir, r) for r in ("encoder", "decoder", "joiner")}
    if not tokens.is_file() or not all(parts.values()):
        return None
    return {"tokens": tokens, **parts}  # type: ignore[dict-item]


def download_model(name: str, dest: Path, progress=None) -> Path:
    """Fetch and unpack a k2-fsa model, keeping only the int8 files we use."""
    model_dir = dest / name
    if model_files(model_dir):
        return model_dir
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"{name}.tar.bz2"

    def hook(blocks, size, total):
        if progress and total > 0:
            progress(min(100.0, blocks * size / total * 100))

    urllib.request.urlretrieve(model_url(name), archive, reporthook=hook)
    with tarfile.open(archive, "r:bz2") as tar:
        members = [m for m in tar.getmembers()
                   if m.name.endswith(("tokens.txt", ".int8.onnx")) and "test_wavs" not in m.name]
        tar.extractall(dest, members=members, filter="data")
    archive.unlink(missing_ok=True)
    if not model_files(model_dir):
        raise RuntimeError(f"Model files missing after extracting {name}")
    return model_dir


# --------------------------------------------------------------- hotwords --
def _vocab(tokens: Path) -> set[str]:
    out = set()
    for line in tokens.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ")
        if len(parts) >= 2:
            out.add(parts[0])
    return out


def _pieces(word: str, vocab: set[str]) -> list[str] | None:
    text = WORD_BOUNDARY + word.upper()
    out, i = [], 0
    while i < len(text):
        for j in range(len(text), i, -1):
            if text[i:j] in vocab:
                out.append(text[i:j])
                i = j
                break
        else:
            return None
    return out


def build_hotwords(phrases: list[str], tokens: Path, out: Path) -> list[str]:
    """Pre-tokenised hotwords file; returns phrases that couldn't be used."""
    vocab = _vocab(tokens)
    lines, skipped = [], []
    for phrase in {p.strip() for p in phrases if p.strip()}:
        seq: list[str] = []
        for word in phrase.split():
            pieces = _pieces(word, vocab)
            if pieces is None:
                seq = []
                break
            seq.extend(pieces)
        (lines.append(" ".join(seq)) if seq else skipped.append(phrase))
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return skipped


# ----------------------------------------------------------------- engine --
class Engine:
    def __init__(self) -> None:
        self.recognizer = None
        self.state = "off"  # off | unavailable | downloading | loading | ready | error
        self.detail = ""
        self.progress = 0.0
        self.skipped_vocab: list[str] = []
        self._lock = threading.Lock()

    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail, "progress": round(self.progress, 1),
                "model": config.cfg.asr_model, "skipped_vocabulary": self.skipped_vocab}

    def load(self, vocabulary: list[str] | None = None, download: bool = True) -> None:
        """Blocking; run in a thread."""
        with self._lock:
            if not config.cfg.asr_enabled:
                self.state, self.detail = "off", "Captions are turned off on this server (ATSUIT_ASR=0)"
                return
            if sherpa_onnx is None:
                self.state, self.detail = "unavailable", "This image was built without speech recognition"
                return
            model_dir = config.cfg.models / config.cfg.asr_model
            try:
                if not model_files(model_dir):
                    if not download:
                        self.state, self.detail = "unavailable", "Speech model not downloaded yet"
                        return
                    self.state, self.detail = "downloading", "Downloading the speech model (once)"

                    def prog(p):
                        self.progress = p

                    download_model(config.cfg.asr_model, config.cfg.models, prog)
                self.state, self.detail = "loading", "Loading the speech model"
                files = model_files(model_dir)
                hot = config.cfg.models / "hotwords.txt"
                self.skipped_vocab = build_hotwords(vocabulary or [], files["tokens"], hot)
                self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=str(files["tokens"]), encoder=str(files["encoder"]),
                    decoder=str(files["decoder"]), joiner=str(files["joiner"]),
                    num_threads=2, sample_rate=SAMPLE_RATE, feature_dim=80,
                    enable_endpoint_detection=True, rule1_min_trailing_silence=2.4,
                    rule2_min_trailing_silence=1.2, rule3_min_utterance_length=300,
                    decoding_method="modified_beam_search", max_active_paths=6,
                    hotwords_file=str(hot) if hot.stat().st_size else "", hotwords_score=1.5,
                    provider="cpu",
                )
                self.state, self.detail, self.progress = "ready", "", 100.0
            except Exception as exc:  # network, disk, bad model
                self.state, self.detail = "error", str(exc)[:300]

    def session(self) -> "Session":
        if self.state != "ready" or self.recognizer is None:
            raise RuntimeError(self.detail or "Speech recognition isn't ready")
        return Session(self.recognizer)


class Session:
    """One room's audio stream."""

    def __init__(self, recognizer) -> None:
        self.r = recognizer
        self.stream = recognizer.create_stream()
        self.last_partial = ""

    def feed(self, pcm16: bytes) -> list[tuple[str, str]]:
        """Feed 16 kHz mono int16 PCM. Returns [("partial"|"final", text)]."""
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        self.stream.accept_waveform(SAMPLE_RATE, samples)
        while self.r.is_ready(self.stream):
            self.r.decode_stream(self.stream)
        text = self.r.get_result(self.stream).strip()
        out: list[tuple[str, str]] = []
        if self.r.is_endpoint(self.stream):
            if text:
                out.append(("final", text))
            self.r.reset(self.stream)
            self.last_partial = ""
        elif text != self.last_partial:
            self.last_partial = text
            out.append(("partial", text))
        return out

    def flush(self) -> str:
        self.stream.input_finished()
        while self.r.is_ready(self.stream):
            self.r.decode_stream(self.stream)
        return self.r.get_result(self.stream).strip()


def level_db(pcm16: bytes) -> float:
    s = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    if not len(s):
        return -100.0
    rms = float(np.sqrt(np.mean(s * s))) / 32768.0
    return round(20 * np.log10(rms), 1) if rms > 1e-5 else -100.0


engine = Engine()
