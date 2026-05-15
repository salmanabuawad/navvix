from __future__ import annotations

import shutil
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.engine.process import process_dxf
from app.models import Job
from app.services.storage import job_dir

ALLOWED_EXTENSIONS = {".dxf"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_job(db: Session, file: UploadFile) -> Job:
    filename = file.filename or "input.dxf"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Only DXF files are accepted")

    job_id = uuid.uuid4().hex
    directory = job_dir(job_id)
    input_path = directory / "input.dxf"
    data = await file.read()
    input_path.write_bytes(data)

    job = Job(
        id=job_id,
        filename=filename,
        status="pending",
        size_bytes=len(data),
        input_path=str(input_path),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def process_job(db_factory, job_id: str) -> None:
    db: Session = db_factory()
    try:
        job = db.get(Job, job_id)
        if not job:
            return
        job.status = "processing"
        job.started_at = utcnow()
        db.commit()

        directory = job_dir(job_id)
        report = process_dxf(job.input_path, directory)

        job.status = "done"
        job.done_at = utcnow()
        job.output_dxf_path = report["dimensioned_dxf"]
        job.preview_pdf_path = report["preview_pdf"]
        job.preview_png_path = report["preview_png"]
        job.report = report
        db.commit()
    except Exception:
        job = db.get(Job, job_id)
        if job:
            job.status = "error"
            job.error = traceback.format_exc()
            db.commit()
    finally:
        db.close()


def delete_job(db: Session, job_id: str) -> None:
    job = db.get(Job, job_id)
    if not job:
        return
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    db.delete(job)
    db.commit()
