"""Server-side speech recognition (sherpa-onnx streaming Zipformer), shared by
every room. Model handling and vocabulary boosting come from AT-LiveCaption;
the difference is that audio arrives from nodes over the network instead of
a local sound card."""
from __future__ import annotations

import json
import math
import re
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


# ------------------------------------------------------- caption text --
# From AT-LiveCaption's asr_engine.py. The model has no whole-word piece for
# most acronyms, so a spoken "UK" or "NHS" comes back as "U K" / "N H S":
# runs of 2+ single-letter words are joined into one acronym.
_ACRONYM_RE = re.compile(r"\b([A-Za-z])(?:\s+([A-Za-z]))+\b")


def join_spelled_acronyms(text: str) -> str:
    return _ACRONYM_RE.sub(lambda m: m.group(0).replace(" ", "").upper(), text)


# Background music has no words for the model to latch onto, so it guesses a
# filler sound, finalised again and again ("um", "um", ...). An utterance made
# of nothing but filler words is shown as [MUSIC] instead.
FILLER_WORDS = {"um", "uh", "umm", "uhh", "erm", "hmm", "mm", "mmm", "huh"}
MUSIC_LABEL = "[MUSIC]"


def is_filler_spam(text: str) -> bool:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return bool(words) and all(w in FILLER_WORDS for w in words)


def words_from_tokens(tokens: list[str], ys_probs: list[float]) -> list[dict]:
    """BPE pieces -> words with a confidence each (exp of the mean log-prob of
    its pieces). A piece starting with a space starts a new word. Only the
    tech's preview and the Corrections tab colour by it."""
    words: list[dict] = []
    cur, probs = "", []
    for tok, prob in zip(tokens, ys_probs):
        if tok.startswith(" ") or tok.startswith(WORD_BOUNDARY):
            if cur:
                words.append({"text": cur, "confidence": round(math.exp(sum(probs) / len(probs)), 3)})
            cur, probs = tok.lstrip(" " + WORD_BOUNDARY), [prob]
        else:
            cur += tok
            probs.append(prob)
    if cur:
        words.append({"text": cur, "confidence": round(math.exp(sum(probs) / len(probs)), 3)})
    return words


def merge_acronym_words(words: list[dict]) -> list[dict]:
    merged: list[dict] = []
    i, n = 0, len(words)
    while i < n:
        j = i
        while j < n and len(words[j]["text"]) == 1 and words[j]["text"].isalpha():
            j += 1
        if j - i >= 2:
            run = words[i:j]
            merged.append({"text": "".join(w["text"] for w in run).upper(),
                           "confidence": round(sum(w["confidence"] for w in run) / len(run), 3)})
            i = j
        else:
            merged.append(words[i])
            i += 1
    return merged


def tidy(text: str, words: list[dict], options: dict | None = None) -> tuple[str, list[dict]]:
    """What a room's screens see: acronyms joined, music labelled (per-room
    switches, both on by default as in LiveCaption)."""
    o = options or {}
    text = text.strip()
    if o.get("join_acronyms", True):
        text = join_spelled_acronyms(text)
        words = merge_acronym_words(words)
    return text, words


# ----------------------------------------------------------------- engine --
# ----------------------------------------------------------------- engine --
DEFAULT_HOTWORDS_SCORE = 2.5  # LiveCaption's default "boost strength"


class Engine:
    def __init__(self) -> None:
        self.recognizer = None
        self.state = "off"  # off | unavailable | downloading | loading | ready | error
        self.detail = ""
        self.progress = 0.0
        self.skipped_vocab: list[str] = []
        self.hotwords_score = DEFAULT_HOTWORDS_SCORE
        self.generation = 0  # bumped on every (re)load so running rooms pick up new vocabulary
        self._lock = threading.Lock()

    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail, "progress": round(self.progress, 1),
                "model": config.cfg.asr_model, "skipped_vocabulary": self.skipped_vocab,
                "hotwords_score": self.hotwords_score}

    def load(self, vocabulary: list[str] | None = None, download: bool = True,
             hotwords_score: float | None = None) -> None:
        """Blocking; run in a thread. Rooms already captioning keep going and
        switch to the new recogniser at their next pause."""
        with self._lock:
            if hotwords_score is not None:
                self.hotwords_score = max(0.5, min(6.0, float(hotwords_score)))
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
                if self.recognizer is None:
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
                    hotwords_file=str(hot) if hot.stat().st_size else "", hotwords_score=self.hotwords_score,
                    provider="cpu",
                )
                self.generation += 1
                self.state, self.detail, self.progress = "ready", "", 100.0
            except Exception as exc:  # network, disk, bad model
                self.state, self.detail = "error", str(exc)[:300]

    def session(self, options: dict | None = None) -> "Session":
        if self.state != "ready" or self.recognizer is None:
            raise RuntimeError(self.detail or "Speech recognition isn't ready")
        return Session(self.recognizer, engine=self, options=options)


def _result(recognizer, stream) -> tuple[str, list[dict]]:
    """Text and per-word confidence. Older/fake recognisers without the JSON
    result just give text."""
    getter = getattr(recognizer, "get_result_as_json_string", None)
    if getter is not None:
        try:
            r = json.loads(getter(stream))
            text = str(r.get("text", "")).strip()
            words = words_from_tokens(r.get("tokens") or [], r.get("ys_probs") or []) if text else []
            return text, words
        except (ValueError, TypeError):
            pass
    return str(recognizer.get_result(stream)).strip(), []


class Session:
    """One room's audio stream. feed() returns caption events:
    {"type": "partial"|"final", "text": str, "words": [{"text", "confidence"}]}.

    Commit logic (as LiveCaption): a final is sent when sherpa's endpoint
    detector hears a pause (rule 1: 2.4 s of silence with nothing said,
    rule 2: 1.2 s after speech, rule 3: a 300 s cap); in between, the growing
    hypothesis goes out as a partial only when it changes. Music/filler-only
    utterances become [MUSIC]; spelled-out acronyms are joined."""

    def __init__(self, recognizer, engine: "Engine | None" = None, options: dict | None = None) -> None:
        self.r = recognizer
        self.engine = engine
        self.generation = engine.generation if engine else 0
        self.options = options if options is not None else {}
        self.stream = recognizer.create_stream()
        self.last_partial = ""

    def feed(self, pcm16: bytes) -> list[dict]:
        """16 kHz mono int16 PCM."""
        return self.feed_samples(np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0)

    def feed_samples(self, samples: np.ndarray) -> list[dict]:
        self.stream.accept_waveform(SAMPLE_RATE, samples)
        while self.r.is_ready(self.stream):
            self.r.decode_stream(self.stream)
        endpoint = self.r.is_endpoint(self.stream)
        raw, words = _result(self.r, self.stream)
        text, words = tidy(raw, words, self.options) if raw else ("", [])
        out: list[dict] = []
        if endpoint:
            if text:
                if self.options.get("music_label", True) and is_filler_spam(text):
                    text, words = MUSIC_LABEL, []
                out.append({"type": "final", "text": text, "words": words})
            self.r.reset(self.stream)
            self.last_partial = ""
            self._maybe_swap()
        elif text and text != self.last_partial:
            self.last_partial = text
            out.append({"type": "partial", "text": text, "words": words})
        return out

    def _maybe_swap(self) -> None:
        """New vocabulary loaded since this room started: move to the new
        recogniser now, between utterances, so nobody has to restart the mic."""
        e = self.engine
        if e is not None and e.generation != self.generation and e.state == "ready" and e.recognizer is not None:
            self.r, self.generation = e.recognizer, e.generation
            self.stream = self.r.create_stream()

    def flush(self) -> str:
        self.stream.input_finished()
        while self.r.is_ready(self.stream):
            self.r.decode_stream(self.stream)
        return _result(self.r, self.stream)[0]


def level_db(pcm16: bytes) -> float:
    s = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    if not len(s):
        return -100.0
    rms = float(np.sqrt(np.mean(s * s))) / 32768.0
    return round(20 * np.log10(rms), 1) if rms > 1e-5 else -100.0


engine = Engine()
