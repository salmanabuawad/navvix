"""
navvix backend — FastAPI

Job routes (DXF processing):
  POST   /api/jobs              upload DXF → enqueue processing
  GET    /api/jobs              list jobs (newest first)
  GET    /api/jobs/{id}         job detail
  GET    /api/jobs/{id}/pdf     stream PDF preview
  GET    /api/jobs/{id}/dxf     download dimensioned DXF
  DELETE /api/jobs/{id}         delete job + files

Training routes:
  GET    /api/training/files           list uploaded training DXFs
  POST   /api/training/files           upload a training DXF
  DELETE /api/training/files/{name}    delete a training file
  POST   /api/training/train           start learning (background)
  GET    /api/training/status          training status + last report
  GET    /api/training/model           current style model (if exists)
  DELETE /api/training/model           reset model (fall back to v12 rules)
"""

from __future__ import annotations

import json, shutil, sys, threading, traceback, uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# ── Path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from navvix_v12.__main__ import isolate, generate, preview as gen_preview

STORAGE      = Path(__file__).parent / "storage";       STORAGE.mkdir(exist_ok=True)
TRAINING_DIR = Path(__file__).parent / "training_files"; TRAINING_DIR.mkdir(exist_ok=True)
MODEL_PATH   = Path(__file__).parent / "style_model.json"

ALLOWED_EXT = {".dxf", ".dwfx"}

app = FastAPI(title="navvix", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Training state (thread-safe) ─────────────────────────────────────────────
_training_lock   = threading.Lock()
_training_status: dict = {"running": False, "last_report": None, "error": None,
                          "started_at": None, "finished_at": None}


# ═══════════════════════════════════════════════════════════════════════════
# Job helpers
# ═══════════════════════════════════════════════════════════════════════════

def job_dir(job_id: str) -> Path:
    return STORAGE / job_id


def read_meta(job_id: str) -> dict:
    p = job_dir(job_id) / "meta.json"
    if not p.exists():
        raise HTTPException(404, "Job not found")
    return json.loads(p.read_text(encoding="utf-8"))


def write_meta(job_id: str, meta: dict):
    (job_dir(job_id) / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Job processing
# ═══════════════════════════════════════════════════════════════════════════

def _process_single(job_id: str, meta: dict):
    """Run v18 isolation + dimensioning on a single-drawing job dir. Mutates meta.

    v18 produces output files with v18_* prefixes; rename to canonical names
    so /api/jobs/{id}/{dxf,pdf} endpoints keep working unchanged.
    """
    from navvix_v18.__main__ import process as v18_process

    d = job_dir(job_id)
    report = v18_process(d / "input.dxf", d)

    rename_map = {
        d / "v18_dimensioned.dxf": d / "dimensioned.dxf",
        d / "v18_isolated.dxf":    d / "isolated_main.dxf",
        d / "v18_preview.png":     d / "preview.png",
        d / "v18_preview.pdf":     d / "preview.pdf",
    }
    for src, dst in rename_map.items():
        if src.exists():
            if dst.exists():
                dst.unlink()
            src.rename(dst)

    meta["model_used"] = "v18_architectural"
    meta.update({
        "status":        "done",
        "done_at":       datetime.now(timezone.utc).isoformat(),
        "x_axes":        report.get("x_axes", 0),
        "y_axes":        report.get("y_axes", 0),
        "dims_total":    report.get("dimensions_created", 0),
        "dims_internal": report.get("dimensions_created", 0),
        "v18_levels":    report.get("level_counts", {}),
        "v18_valid":     report.get("validation", {}).get("valid", False),
    })


def process_job(job_id: str):
    d = job_dir(job_id)
    meta = read_meta(job_id)
    meta["status"] = "processing"
    meta["started_at"] = datetime.now(timezone.utc).isoformat()
    write_meta(job_id, meta)
    try:
        # Multi-drawing detection: if the upload contains N>=2 apartment sheets,
        # split into N child jobs and process each independently. The parent
        # job becomes a no-op record (kind="batch") so the upload is still
        # discoverable in the job list.
        from navvix_v12.split import split_drawing
        splits_dir = d / "splits"
        try:
            children = split_drawing(d / "input.dxf", splits_dir)
        except Exception:
            children = []

        if children:
            child_ids: list[str] = []
            for child_input, label in children:
                cid = uuid.uuid4().hex
                cd = job_dir(cid); cd.mkdir()
                shutil.copy(child_input, cd / "input.dxf")
                cmeta = {
                    "id":         cid,
                    "parent_id":  job_id,
                    "filename":   f"{meta.get('filename', 'input.dxf')} [{label}]",
                    "size_bytes": (cd / "input.dxf").stat().st_size,
                    "status":     "pending",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "model_used": meta.get("model_used"),
                }
                write_meta(cid, cmeta)
                child_ids.append(cid)

            meta.update({
                "kind":     "batch",
                "children": child_ids,
                "status":   "done",
                "done_at":  datetime.now(timezone.utc).isoformat(),
            })
            write_meta(job_id, meta)

            # Process children sequentially in the same background task
            for cid in child_ids:
                try:
                    cmeta_now = read_meta(cid)
                    cmeta_now["status"] = "processing"
                    cmeta_now["started_at"] = datetime.now(timezone.utc).isoformat()
                    write_meta(cid, cmeta_now)
                    _process_single(cid, cmeta_now)
                    write_meta(cid, cmeta_now)
                except Exception:
                    err_meta = read_meta(cid)
                    err_meta.update({"status": "error", "error": traceback.format_exc()})
                    write_meta(cid, err_meta)
            return

        _process_single(job_id, meta)
    except Exception:
        meta.update({"status": "error", "error": traceback.format_exc()})
    write_meta(job_id, meta)


# ═══════════════════════════════════════════════════════════════════════════
# Training
# ═══════════════════════════════════════════════════════════════════════════

def _run_training():
    global _training_status
    with _training_lock:
        _training_status["running"]    = True
        _training_status["error"]      = None
        _training_status["started_at"] = datetime.now(timezone.utc).isoformat()

    try:
        from navvix_v13.learner import learn
        sample_paths = list(TRAINING_DIR.glob("*.dxf")) + list(TRAINING_DIR.glob("*.dwfx"))
        if not sample_paths:
            raise ValueError("No training files uploaded yet")
        model, report = learn(sample_paths, MODEL_PATH)
        with _training_lock:
            _training_status.update({
                "running":     False,
                "last_report": report,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception:
        with _training_lock:
            _training_status.update({
                "running":     False,
                "error":       traceback.format_exc(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })


# ═══════════════════════════════════════════════════════════════════════════
# Job routes
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/api/jobs", status_code=201)
async def create_job(file: UploadFile, background_tasks: BackgroundTasks):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Only {', '.join(ALLOWED_EXT)} accepted")

    job_id = uuid.uuid4().hex
    d = job_dir(job_id); d.mkdir()
    data = await file.read()
    (d / "input.dxf").write_bytes(data)

    meta = {
        "id":         job_id,
        "filename":   file.filename,
        "size_bytes": len(data),
        "status":     "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_used": "v13_learned" if MODEL_PATH.exists() else "v12_rules",
    }
    write_meta(job_id, meta)
    background_tasks.add_task(process_job, job_id)
    return meta


@app.get("/api/jobs")
def list_jobs():
    jobs = []
    for d in STORAGE.iterdir():
        m = d / "meta.json"
        if m.exists():
            try:
                jobs.append(json.loads(m.read_text(encoding="utf-8")))
            except Exception:
                pass
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return read_meta(job_id)


@app.get("/api/jobs/{job_id}/pdf")
def get_pdf(job_id: str):
    pdf = job_dir(job_id) / "preview.pdf"
    if not pdf.exists():
        raise HTTPException(404, "PDF not ready")
    return FileResponse(str(pdf), media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})


@app.get("/api/jobs/{job_id}/dxf")
def get_dxf(job_id: str):
    meta = read_meta(job_id)
    dxf  = job_dir(job_id) / "dimensioned.dxf"
    if not dxf.exists():
        raise HTTPException(404, "DXF not ready")
    stem = Path(meta.get("filename", "drawing")).stem
    return FileResponse(str(dxf), media_type="application/octet-stream",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{stem}_dimensioned.dxf"'})


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    d = job_dir(job_id)
    if not d.exists():
        raise HTTPException(404, "Job not found")
    shutil.rmtree(d)


# ═══════════════════════════════════════════════════════════════════════════
# Training routes
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/training/files")
def list_training_files():
    files = []
    for f in sorted(TRAINING_DIR.iterdir()):
        if f.suffix.lower() in ALLOWED_EXT:
            stat = f.stat()
            files.append({
                "name":        f.name,
                "size_bytes":  stat.st_size,
                "uploaded_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc).isoformat(),
            })
    return files


@app.post("/api/training/files", status_code=201)
async def upload_training_file(file: UploadFile):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Only .dxf / .dwfx accepted")
    dest = TRAINING_DIR / (file.filename or "upload.dxf")
    data = await file.read()
    dest.write_bytes(data)
    return {"name": dest.name, "size_bytes": len(data)}


@app.delete("/api/training/files/{filename}", status_code=204)
def delete_training_file(filename: str):
    f = TRAINING_DIR / filename
    if not f.exists():
        raise HTTPException(404, "File not found")
    f.unlink()


@app.post("/api/training/train", status_code=202)
def start_training(background_tasks: BackgroundTasks):
    if _training_status["running"]:
        raise HTTPException(409, "Training already in progress")
    files = list(TRAINING_DIR.glob("*.dxf")) + list(TRAINING_DIR.glob("*.dwfx"))
    if not files:
        raise HTTPException(400, "No training files uploaded yet")
    background_tasks.add_task(_run_training)
    return {"status": "started", "files": len(files)}


@app.get("/api/training/status")
def get_training_status():
    return dict(_training_status)


@app.get("/api/training/model")
def get_model():
    if not MODEL_PATH.exists():
        return None
    try:
        return json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


@app.delete("/api/training/model", status_code=204)
def reset_model():
    if MODEL_PATH.exists():
        MODEL_PATH.unlink()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
