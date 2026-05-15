from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Job
from app.schemas import JobOut
from app.services.job_service import create_job, delete_job, process_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=201)
async def upload_job(file: UploadFile, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        job = await create_job(db, file)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    background_tasks.add_task(process_job, SessionLocal, job.id)
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db)):
    return db.scalars(select(Job).order_by(Job.created_at.desc())).all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.delete("/{job_id}", status_code=204)
def remove_job(job_id: str, db: Session = Depends(get_db)):
    delete_job(db, job_id)
    return None


def _file_or_404(path: str | None, filename: str) -> FileResponse:
    if not path or not Path(path).exists():
        raise HTTPException(404, "File not ready")
    return FileResponse(path, filename=filename)


@router.get("/{job_id}/pdf")
def get_pdf(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _file_or_404(job.preview_pdf_path, f"{job_id}.pdf")


@router.get("/{job_id}/png")
def get_png(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _file_or_404(job.preview_png_path, f"{job_id}.png")


@router.get("/{job_id}/dxf")
def get_dxf(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _file_or_404(job.output_dxf_path, f"{job_id}_dimensioned.dxf")
