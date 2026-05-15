from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    status: str
    size_bytes: int
    error: str | None = None
    report: dict | None = None
    created_at: datetime
    started_at: datetime | None = None
    done_at: datetime | None = None
