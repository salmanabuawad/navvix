from __future__ import annotations

from pathlib import Path
from app.config import settings


def storage_root() -> Path:
    root = Path(settings.storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_dir(job_id: str) -> Path:
    path = storage_root() / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path
