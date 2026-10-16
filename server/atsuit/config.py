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
        # Laptops in the AT-SUIT app caption by themselves with a model they fetch from this server.
        # A hosted server that never listens itself (ATSUIT_ASR=0) still hands them the model.
        self.laptop_models = os.getenv("ATSUIT_LAPTOP_MODELS", "1" if self.asr_enabled else "0") != "0"
        self.asr_model = os.getenv(
            "ATSUIT_ASR_MODEL", "sherpa-onnx-streaming-zipformer-en-2023-06-21"
        )
        # "main" runs the whole suite; "helper" only lends its CPU to a main server.
        self.role = "helper" if os.getenv("ATSUIT_ROLE", "main").strip().lower() == "helper" else "main"
        self.main_url = os.getenv("ATSUIT_MAIN_URL", "").rstrip("/")
        self.join_code = os.getenv("ATSUIT_JOIN_CODE", "")
        self.helper_name = os.getenv("ATSUIT_HELPER_NAME", "")
        self.asr_max_rooms = int(os.getenv("ATSUIT_ASR_MAX_ROOMS", "0")) or (os.cpu_count() or 2)
        # Hosted: run for a venue by its AT-SUIT provider (cloud/), not on the venue's own server.
        self.hosted = os.getenv("ATSUIT_HOSTED", "0") == "1"
        # The one-time code in the setup link; without it nobody can set up a new server.
        self.setup_code = os.getenv("ATSUIT_SETUP_CODE", "").strip()
        # A provider's own tech guide articles, added to the built-in guide for every venue it hosts.
        self.kb_dirs = [Path(p) for p in os.getenv("ATSUIT_KB_DIR", "").split(os.pathsep) if p]
        # The provider's private AI model, used when the venue hasn't set one of its own.
        self.ai_url = os.getenv("ATSUIT_AI_URL", "").rstrip("/")
        self.ai_model = os.getenv("ATSUIT_AI_MODEL", "")

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
