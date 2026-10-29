"""Library: folders, photos, ratings/flags, thumbnails and quick previews."""

from __future__ import annotations

import os
import string
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import col, func, select

from app import jobs
from app.config import PREVIEW_DIR
from app.db import SessionDep
from app.library.paths import forget_root, photo_path
from app.library.scan import scan_root
from app.library.thumbs import get_quick_preview, get_thumb
from app.models import Edit, HistoryStep, Photo, Root, Snapshot

router = APIRouter(tags=["library"])

IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


# ---------------------------------------------------------------- roots

class RootIn(BaseModel):
    path: str
    name: str | None = None


def _root_dict(r: Root, count: int = 0) -> dict:
    return {"id": r.id, "path": r.path, "name": r.name, "count": count,
            "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None}


@router.get("/roots")
def list_roots(session: SessionDep) -> list[dict]:
    counts = dict(session.exec(select(Photo.root_id, func.count()).where(Photo.missing == False).group_by(Photo.root_id)).all())  # noqa: E712
    return [_root_dict(r, counts.get(r.id, 0)) for r in session.exec(select(Root).order_by(Root.name))]


@router.post("/roots")
def add_root(body: RootIn, session: SessionDep) -> dict:
    p = Path(body.path).expanduser()
    if not p.is_dir():
        raise HTTPException(400, f"폴더를 찾을 수 없어요: {body.path}")
    path = str(p.resolve())
    existing = session.exec(select(Root).where(Root.path == path)).first()
    if existing:
        root = existing
    else:
        # A folder inside an existing root is already covered.
        for r in session.exec(select(Root)):
            if Path(path).is_relative_to(r.path):
                raise HTTPException(400, f"이미 추가된 '{r.name}' 폴더 안에 있어요.")
        root = Root(path=path, name=body.name or p.name or path)
        session.add(root)
        session.commit()
        session.refresh(root)
    job = jobs.start("scan", f"'{root.name}' 살펴보기", lambda ctx: _scan_and_thumbs(root.id, ctx))
    return {"root": _root_dict(root), "job_id": job}


@router.post("/roots/{root_id}/scan")
def rescan(root_id: int, session: SessionDep) -> dict:
    root = session.get(Root, root_id)
    if not root:
        raise HTTPException(404)
    return {"job_id": jobs.start("scan", f"'{root.name}' 다시 살펴보기", lambda ctx: _scan_and_thumbs(root_id, ctx))}


def _scan_and_thumbs(root_id: int, ctx) -> str:
    from app.db import engine
    from app.library.thumbs import prefetch
    from sqlmodel import Session

    msg = scan_root(root_id, ctx)
    with Session(engine) as s:
        ids = list(s.exec(select(Photo.id).where(Photo.root_id == root_id, Photo.missing == False)  # noqa: E712
                          .order_by(col(Photo.capture_time).desc())))
    prefetch(ids, ctx)
    return msg


@router.delete("/roots/{root_id}")
def remove_root(root_id: int, session: SessionDep) -> dict:
    """Forget a folder (its photos' edits too). Files on disk are not touched."""
    root = session.get(Root, root_id)
    if not root:
        raise HTTPException(404)
    ids = list(session.exec(select(Photo.id).where(Photo.root_id == root_id)))
    for model in (Edit, HistoryStep, Snapshot):
        key = model.photo_id
        for row in session.exec(select(model).where(col(key).in_(ids))):
            session.delete(row)
    for p in session.exec(select(Photo).where(Photo.root_id == root_id, Photo.master_id != None)):  # noqa: E711
        session.delete(p)
    session.commit()
    for p in session.exec(select(Photo).where(Photo.root_id == root_id)):
        session.delete(p)
    session.delete(root)
    session.commit()
    forget_root(root_id)
    return {"ok": True}


@router.get("/fs/browse")
def browse(path: str | None = None) -> dict:
    """Folder picker for adding roots (PC only — the auth middleware keeps phones out unless unlocked)."""
    if not path:
        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        return {"path": None, "parent": None, "dirs": [{"name": d, "path": d} for d in drives]}
    p = Path(path)
    if not p.is_dir():
        raise HTTPException(400, "폴더가 아니에요.")
    dirs = []
    try:
        for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
            if e.is_dir() and not e.name.startswith((".", "$")) and not e.name.endswith(".lrdata"):
                dirs.append({"name": e.name, "path": e.path})
    except PermissionError:
        pass
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "dirs": dirs}


# ---------------------------------------------------------------- folders

@router.get("/folders")
def folders(root_id: int, session: SessionDep) -> list[dict]:
    rows = session.exec(
        select(Photo.folder, func.count(), func.min(Photo.capture_time))
        .where(Photo.root_id == root_id, Photo.missing == False, Photo.master_id == None)  # noqa: E711, E712
        .group_by(Photo.folder)
    ).all()
    return [{"folder": f, "count": c, "first": t.isoformat() if t else None} for f, c, t in sorted(rows)]


# ---------------------------------------------------------------- photos

def _photo_dict(p: Photo) -> dict:
    return {
        "id": p.id, "root_id": p.root_id, "folder": p.folder, "filename": p.filename, "ext": p.ext,
        "time": p.capture_time.isoformat() if p.capture_time else None,
        "rating": p.rating, "flag": p.flag, "label": p.color_label, "edited": p.edited,
        "rev": p.edit_rev, "orientation": p.orientation, "copy": p.copy_name, "master_id": p.master_id,
        "pair": p.pair_jpg is not None,
    }


@router.get("/photos")
def list_photos(
    session: SessionDep,
    root_id: int | None = None,
    folder: str | None = None,
    recursive: bool = True,
    min_rating: int = 0,
    flag: str | None = None,  # pick | reject | unflagged | notreject
    edited: bool | None = None,
    label: str | None = None,
    q: str | None = None,
    sort: str = "time",
) -> list[dict]:
    st = select(Photo).where(Photo.missing == False)  # noqa: E712
    if root_id is not None:
        st = st.where(Photo.root_id == root_id)
    if folder is not None and folder != "":
        if recursive:
            st = st.where((Photo.folder == folder) | col(Photo.folder).startswith(folder + "/"))
        else:
            st = st.where(Photo.folder == folder)
    elif folder == "" and not recursive:
        st = st.where(Photo.folder == "")
    if min_rating:
        st = st.where(Photo.rating >= min_rating)
    if flag == "pick":
        st = st.where(Photo.flag == 1)
    elif flag == "reject":
        st = st.where(Photo.flag == -1)
    elif flag == "unflagged":
        st = st.where(Photo.flag == 0)
    elif flag == "notreject":
        st = st.where(Photo.flag >= 0)
    if edited is not None:
        st = st.where(Photo.edited == edited)
    if label:
        st = st.where(Photo.color_label == label)
    if q:
        like = f"%{q}%"
        st = st.where(col(Photo.filename).ilike(like) | col(Photo.folder).ilike(like) | col(Photo.lens).ilike(like))
    order = {
        "time": (Photo.capture_time, Photo.filename),
        "name": (Photo.folder, Photo.filename),
        "rating": (col(Photo.rating).desc(), Photo.capture_time),
    }.get(sort, (Photo.capture_time, Photo.filename))
    st = st.order_by(*order)
    return [_photo_dict(p) for p in session.exec(st)]


@router.get("/photos/{photo_id}")
def photo_detail(photo_id: int, session: SessionDep) -> dict:
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)
    d = _photo_dict(p)
    d.update({
        "path": str(photo_path(session, p)), "size": p.size, "make": p.make, "model": p.model,
        "lens": p.lens, "focal_length": p.focal_length, "aperture": p.aperture, "shutter": p.shutter,
        "iso": p.iso, "exposure_bias": p.exposure_bias, "width": p.width, "height": p.height,
    })
    return d


class PhotoPatch(BaseModel):
    ids: list[int]
    rating: int | None = None
    flag: int | None = None
    label: str | None = None
    clear_label: bool = False


@router.patch("/photos")
def patch_photos(body: PhotoPatch, session: SessionDep) -> dict:
    for p in session.exec(select(Photo).where(col(Photo.id).in_(body.ids))):
        if body.rating is not None:
            p.rating = max(0, min(5, body.rating))
        if body.flag is not None:
            p.flag = max(-1, min(1, body.flag))
        if body.label is not None or body.clear_label:
            p.color_label = None if body.clear_label else body.label
        session.add(p)
    session.commit()
    return {"ok": True, "count": len(body.ids)}


@router.post("/photos/{photo_id}/virtual-copy")
def virtual_copy(photo_id: int, session: SessionDep) -> dict:
    src = session.get(Photo, photo_id)
    if not src:
        raise HTTPException(404)
    master_id = src.master_id or src.id
    master = session.get(Photo, master_id)
    n = session.exec(select(func.count()).where(Photo.master_id == master_id)).one()
    data = master.model_dump(exclude={"id", "master_id", "copy_name", "added_at", "rating", "flag"})
    copy = Photo(**data, master_id=master_id, copy_name=f"복사본 {n + 1}")
    session.add(copy)
    session.commit()
    session.refresh(copy)
    e = session.get(Edit, src.id)
    if e:
        session.add(Edit(photo_id=copy.id, params=e.params))
        copy.edited, copy.edit_rev = True, 1
        session.add(copy)
        session.commit()
    return _photo_dict(copy)


@router.delete("/photos/{photo_id}")
def delete_virtual_copy(photo_id: int, session: SessionDep) -> dict:
    p = session.get(Photo, photo_id)
    if not p or not p.master_id:
        raise HTTPException(400, "가상 복사본만 지울 수 있어요. (원본 파일은 PhotoLab에서 지우지 않아요)")
    for model in (Edit, HistoryStep, Snapshot):
        for row in session.exec(select(model).where(model.photo_id == photo_id)):
            session.delete(row)
    session.delete(p)
    session.commit()
    return {"ok": True}


@router.get("/photos/{photo_id}/thumb")
def thumb(photo_id: int, session: SessionDep, v: int = Query(0)) -> FileResponse:
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)
    try:
        f = get_thumb(session, p)
    except Exception as e:
        raise HTTPException(500, f"썸네일을 만들 수 없어요: {e}") from e
    return FileResponse(f, media_type="image/jpeg", headers=IMMUTABLE if v else {"Cache-Control": "no-cache"})


@router.get("/photos/{photo_id}/preview")
def preview(photo_id: int, session: SessionDep, v: int = Query(0), size: int = 2048) -> FileResponse:
    """Loupe image: the camera's embedded JPEG for untouched photos, a render for edited ones."""
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)
    if p.edited and p.edit_rev:
        f = PREVIEW_DIR / f"{p.id // 1000:04d}" / f"{p.id}_r{p.edit_rev}_{size}.jpg"
        if not f.exists():
            from app.engine.ai_masks import cached_ai_masks
            from app.engine.render import (
                decoded_for, encode_jpeg, fit_size, geometry_for, load_params, meta_of, render_params,
            )

            dec = decoded_for(session, p)
            meta = meta_of(p)
            prm = load_params(session, p)
            w, h = fit_size(*geometry_for(dec, meta, prm).crop_px, size, size)
            res = render_params(dec, meta, prm, w, h, ai_masks=cached_ai_masks(p, prm))
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(encode_jpeg(res.image, 90))
        return FileResponse(f, media_type="image/jpeg", headers=IMMUTABLE if v else {"Cache-Control": "no-cache"})
    f = get_quick_preview(session, p)
    return FileResponse(f, media_type="image/jpeg", headers=IMMUTABLE if v else {"Cache-Control": "no-cache"})


@router.get("/stats")
def stats(session: SessionDep) -> dict:
    total = session.exec(select(func.count()).select_from(Photo).where(Photo.missing == False)).one()  # noqa: E712
    edited = session.exec(select(func.count()).select_from(Photo).where(Photo.edited == True)).one()  # noqa: E712
    picks = session.exec(select(func.count()).select_from(Photo).where(Photo.flag == 1)).one()
    return {"total": total, "edited": edited, "picks": picks, "now": datetime.now().isoformat()}
