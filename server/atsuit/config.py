"""Runtime configuration. Only infrastructure lives in env vars; everything a
venue changes on site lives in the settings table and the Admin UI."""
from __future__ import annotations

import os
from pathlib import Path


class Config:
    def __init__(self) -> None:
        self.data = Path(os.getenv("ATSUIT_DATA", "/data"))
        self.port = int(os.getenv("ATSUIT_PORT", "8080"))
        self.public_url = os.getenv("ATSUIT_PUBLIC_URL", "").rstrip("/")
        self.max_upload = int(os.getenv("ATSUIT_MAX_UPLOAD", str(25 * 1024 * 1024)))
        self.session_hours = int(os.getenv("ATSUIT_SESSION_HOURS", "24"))
        self.asr_enabled = os.getenv("ATSUIT_ASR", "1") != "0"
        self.asr_model = os.getenv(
            "ATSUIT_ASR_MODEL", "sherpa-onnx-streaming-zipformer-en-2023-06-21"
        )
        self.asr_max_rooms = int(os.getenv("ATSUIT_ASR_MAX_ROOMS", "0")) or (os.cpu_count() or 2)

    @property
    def db_path(self) -> Path:
        return self.data / "atsuit.db"

    @property
    def uploads(self) -> Path:
        return self.data / "uploads"

    @property
    def models(self) -> Path:
        return self.data / "models"

    @property
    def transcripts(self) -> Path:
        return self.data / "transcripts"

    def ensure_dirs(self) -> None:
        for p in (self.data, self.uploads, self.models, self.transcripts):
            p.mkdir(parents=True, exist_ok=True)


cfg = Config()


def reload() -> Config:
    global cfg
    cfg = Config()
    return cfg
