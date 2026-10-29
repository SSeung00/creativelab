"""Health, background jobs, SD-card import and Lightroom catalog import."""

from __future__ import annotations

from pathlib import Path

import torch
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import col, select

from app import auth, jobs
from app.db import SessionDep
from app.library import importer, lrcatalog
from app.library.scan import scan_root
from app.models import Job, Root

router = APIRouter(tags=["system"])


@router.get("/health")
def health(request: Request) -> dict:
    return {"ok": True, "local": auth.is_local(request.scope)}


@router.get("/system")
def system_info() -> dict:
    gpu = None
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        gpu = {"name": torch.cuda.get_device_name(0), "free_gb": round(free / 2**30, 1),
               "total_gb": round(total / 2**30, 1)}
    return {"gpu": gpu, "torch": torch.__version__}


def _job(j: Job) -> dict:
    cur = jobs.live.get(j.id, {})
    return {"id": j.id, "kind": j.kind, "title": j.title, "status": j.status, "done": cur.get("done", j.done),
            "total": cur.get("total", j.total), "message": cur.get("message", j.message), "result": j.result,
            "created_at": j.created_at.isoformat(),
            "finished_at": j.finished_at.isoformat() if j.finished_at else None}


@router.get("/jobs")
def list_jobs(session: SessionDep, active: bool = False) -> list[dict]:
    st = select(Job).order_by(col(Job.id).desc()).limit(30)
    if active:
        st = select(Job).where(col(Job.status).in_(["queued", "running"])).order_by(Job.id)
    return [_job(j) for j in session.exec(st)]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: SessionDep) -> dict:
    j = session.get(Job, job_id)
    if not j:
        raise HTTPException(404)
    return _job(j)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    jobs.cancel(job_id)
    return {"ok": True}


# ---------------------------------------------------------------- SD card import

@router.get("/import/cards")
def cards() -> list[dict]:
    return importer.find_cards()


class ImportIn(BaseModel):
    source: str
    root_id: int
    pattern: str = "%Y/%y%m%d"
    backup: str | None = None
    videos: bool = False


@router.post("/import/plan")
def import_plan(body: ImportIn, session: SessionDep) -> dict:
    root = session.get(Root, body.root_id)
    if not root:
        raise HTTPException(404)
    items = importer.plan(Path(body.source), Path(root.path), body.pattern, body.videos)
    new = [(s, d) for s, d, there in items if not there]
    folders: dict[str, int] = {}
    for _s, d in new:
        k = str(d.parent.relative_to(root.path)).replace("\\", "/")
        folders[k] = folders.get(k, 0) + 1
    return {"total": len(items), "new": len(new), "bytes": sum(s.stat().st_size for s, _ in new),
            "folders": [{"folder": k, "count": v} for k, v in sorted(folders.items())]}


@router.post("/import")
def import_cards(body: ImportIn, session: SessionDep) -> dict:
    root = session.get(Root, body.root_id)
    if not root:
        raise HTTPException(404)
    if body.backup and Path(body.backup).resolve() == Path(root.path).resolve():
        raise HTTPException(400, "백업 폴더는 사진 폴더와 달라야 해요.")

    def run(ctx):
        msg = importer.run_import(body.source, root.path, body.pattern, body.backup, body.videos, ctx)
        scan_root(root.id, ctx)
        return msg

    return {"job_id": jobs.start("import", "메모리카드에서 가져오기", run)}


# ---------------------------------------------------------------- Lightroom

class LrIn(BaseModel):
    catalog: str
    root_id: int | None = None
    edits: bool = True
    ratings: bool = True
    overwrite: bool = False


@router.post("/lightroom/preview")
def lr_preview(body: LrIn) -> dict:
    p = Path(body.catalog)
    if not p.is_file() or p.suffix.lower() != ".lrcat":
        raise HTTPException(400, "라이트룸 카탈로그(.lrcat) 파일을 골라 주세요.")
    return lrcatalog.preview(p, body.root_id)


@router.post("/lightroom/import")
def lr_import(body: LrIn) -> dict:
    p = Path(body.catalog)
    if not p.is_file():
        raise HTTPException(400, "카탈로그 파일을 찾을 수 없어요.")
    return {"job_id": jobs.start("lightroom", "라이트룸 카탈로그 가져오기",
                                 lambda ctx: lrcatalog.run_import(p, body.root_id, body.edits, body.ratings,
                                                                  body.overwrite, ctx))}


@router.get("/lightroom/find")
def lr_find(root: str) -> list[str]:
    """Find .lrcat files under a folder (not inside Previews/backups)."""
    out = []
    base = Path(root)
    if not base.is_dir():
        return out
    for p in base.rglob("*.lrcat"):
        if "Backups" in p.parts or "Old Lightroom Catalogs" in p.parts:
            continue
        out.append(str(p))
        if len(out) > 50:
            break
    return out
