"""AI tools: masks, denoise, culling. Models load lazily on the GPU (first use downloads them)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app import jobs
from app.db import SessionDep, engine
from app.engine.ai_masks import KINDS, load_mask
from app.engine.render import decoded_for
from app.models import AiScore, Photo

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
def status() -> dict:
    from app.ai import models

    return models.status()


class MaskIn(BaseModel):
    kind: str


@router.post("/mask/{photo_id}")
def make_mask(photo_id: int, body: MaskIn, session: SessionDep) -> dict:
    if body.kind not in KINDS:
        raise HTTPException(400, "알 수 없는 마스크 종류예요.")
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)
    pid = p.master_id or p.id
    if load_mask(pid, body.kind) is not None:
        return {"ready": True}
    labels = {"subject": "피사체", "sky": "하늘", "person": "인물", "background": "배경"}

    def run(ctx):
        from app.ai.segment import generate

        ctx.progress(0, 1, f"AI {labels[body.kind]} 찾는 중… (처음엔 모델을 내려받아요)", force=True)
        with Session(engine) as s:
            photo = s.get(Photo, photo_id)
            dec = decoded_for(s, photo)
            generate(pid, dec, photo.orientation or 1, body.kind)
        return "완료"

    return {"job_id": jobs.start("ai-mask", f"AI 마스크: {labels[body.kind]}", run)}


@router.post("/denoise/{photo_id}")
def denoise(photo_id: int, session: SessionDep) -> dict:
    from app.ai.denoise import has_denoised

    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)
    pid = p.master_id or p.id
    if has_denoised(pid):
        return {"ready": True}

    def run(ctx):
        from app.ai.denoise import denoise_base

        with Session(engine) as s:
            photo = s.get(Photo, photo_id)
            dec = decoded_for(s, photo)
        denoise_base(pid, dec, lambda k, n: ctx.progress(k, n, f"AI 노이즈 제거 중… {k}/{n}"))
        return "완료"

    return {"job_id": jobs.start("ai-denoise", "AI 노이즈 제거", run)}


class CullIn(BaseModel):
    ids: list[int] | None = None
    root_id: int | None = None
    folder: str | None = None


@router.post("/cull")
def cull(body: CullIn, session: SessionDep) -> dict:
    ids = body.ids
    if not ids:
        st = select(Photo.id).where(Photo.missing == False, Photo.master_id == None)  # noqa: E711, E712
        if body.root_id:
            st = st.where(Photo.root_id == body.root_id)
        if body.folder:
            st = st.where((Photo.folder == body.folder) | col(Photo.folder).startswith(body.folder + "/"))
        ids = list(session.exec(st))
    if not ids:
        raise HTTPException(400, "분석할 사진이 없어요.")
    from app.ai.cull import run

    return {"job_id": jobs.start("ai-cull", f"AI 골라내기 ({len(ids):,}장)", lambda ctx: run(ids, ctx))}


@router.get("/scores")
def scores(session: SessionDep, root_id: int | None = None) -> dict[int, dict]:
    st = select(AiScore)
    if root_id:
        st = st.join(Photo, Photo.id == AiScore.photo_id).where(Photo.root_id == root_id)
    return {
        s.photo_id: {"sharp": round(s.sharp_rel, 2), "blurry": s.blurry, "eyes": s.eyes_closed, "faces": s.faces,
                     "group": s.group_id, "gsize": s.group_size, "best": s.best_in_group,
                     "hi": round(s.clipped_hi, 3)}
        for s in session.exec(st)
    }
