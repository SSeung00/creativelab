"""Export jobs and access to exported files (also for downloading on the phone)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app import jobs
from app.config import EXPORT_DIR
from app.db import SessionDep, engine
from app.engine.export import PRESETS, export_photo
from app.models import Photo, Setting

router = APIRouter(tags=["export"])


class ExportIn(BaseModel):
    ids: list[int]
    preset: str | None = None
    format: str = "jpeg"
    quality: int = 92
    long_edge: int | None = None
    color_space: str = "srgb"
    sharpen: str = "none"
    naming: str = "{name}"
    dest_dir: str | None = None  # default: data/exports/<date time>
    subfolder: str | None = None


@router.get("/export/presets")
def presets() -> dict:
    return PRESETS


@router.post("/export")
def start_export(body: ExportIn) -> dict:
    opts = body.model_dump()
    if body.preset in PRESETS:
        opts.update(PRESETS[body.preset])
    base = Path(body.dest_dir) if body.dest_dir else EXPORT_DIR
    sub = body.subfolder or datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = base / sub
    ids = list(body.ids)

    def run(ctx: jobs.JobContext) -> str:
        files = []
        with Session(engine) as s:
            photos = {p.id: p for p in s.exec(select(Photo).where(col(Photo.id).in_(ids)))}
            for i, pid in enumerate(ids):
                if ctx.cancelled:
                    break
                p = photos.get(pid)
                if not p:
                    continue
                ctx.progress(i, len(ids), f"{p.filename} 내보내는 중… ({i + 1}/{len(ids)})", force=True)
                files.append(str(export_photo(s, p, opts, dest, i + 1)))
            ctx.progress(len(ids), len(ids), "완료", force=True)
            row = s.get(Setting, "last_export_dir") or Setting(key="last_export_dir")
            row.value = str(dest)
            s.add(row)
            s.commit()
        return json.dumps({"dir": str(dest), "files": files}, ensure_ascii=False)

    return {"job_id": jobs.start("export", f"{len(ids)}장 내보내기", run), "dest": str(dest)}


def _allowed(path: Path, session) -> bool:
    roots = [EXPORT_DIR.resolve()]
    last = session.get(Setting, "last_export_dir")
    if last and last.value:
        roots.append(Path(last.value).resolve())
    rp = path.resolve()
    return any(rp.is_relative_to(r) for r in roots)


@router.get("/exports")
def recent_exports(session: SessionDep, limit: int = 60) -> list[dict]:
    files = sorted((f for f in EXPORT_DIR.rglob("*") if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"path": str(f), "name": f.name, "folder": f.parent.name, "size": f.stat().st_size,
             "time": datetime.fromtimestamp(f.stat().st_mtime).isoformat()} for f in files[:limit]]


@router.get("/exports/file")
def export_file(path: str, session: SessionDep, download: bool = False) -> FileResponse:
    p = Path(path)
    if not p.is_file() or not _allowed(p, session):
        raise HTTPException(404)
    return FileResponse(p, filename=p.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


@router.post("/exports/open-folder")
def open_folder(request: Request, path: str | None = None) -> dict:
    import os

    from app.auth import is_local

    if not is_local(request.scope):
        raise HTTPException(403, "PC에서만 폴더를 열 수 있어요.")
    target = Path(path) if path else EXPORT_DIR
    if not target.exists():
        raise HTTPException(404)
    os.startfile(target if target.is_dir() else target.parent)  # noqa: S606 (local PC only)
    return {"ok": True}
