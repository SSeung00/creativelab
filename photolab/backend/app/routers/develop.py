"""Develop: load/save edit params, live renders, history, snapshots, presets, sync."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

import numpy as np
import torch
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import col, select

from app.db import SessionDep
from app.engine import adobe, color
from app.engine import params as PR
from app.engine.ai_masks import cached_ai_masks, needed_kinds
from app.engine.render import (
    _sample, available_profiles, decoded_for, encode_jpeg, fit_size, geometry_for, meta_of,
    render_params, resolve_profile, sample_grid,
)
from app.models import Edit, HistoryStep, Photo, Preset, Snapshot

router = APIRouter(prefix="/develop", tags=["develop"])
HISTORY_LIMIT = 200


def _photo(session, photo_id: int) -> Photo:
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404, "사진을 찾을 수 없어요.")
    return p


def _params(session, p: Photo) -> dict:
    e = session.get(Edit, p.id)
    return PR.full(e.params if e else None)


def _save(session, p: Photo, prm: dict, label: str | None) -> int | None:
    d = PR.diff(prm)
    e = session.get(Edit, p.id)
    if not d:
        if e:
            session.delete(e)
        p.edited = False
    else:
        txt = json.dumps(d, ensure_ascii=False)
        if e:
            e.params, e.updated_at = txt, datetime.now()
        else:
            e = Edit(photo_id=p.id, params=txt)
        session.add(e)
        p.edited = True
    p.edit_rev += 1
    session.add(p)
    step = None
    if label:
        step = HistoryStep(photo_id=p.id, label=label, params=json.dumps(d, ensure_ascii=False))
        session.add(step)
        old = session.exec(select(HistoryStep.id).where(HistoryStep.photo_id == p.id)
                           .order_by(col(HistoryStep.id).desc()).offset(HISTORY_LIMIT)).all()
        for hid in old:
            session.delete(session.get(HistoryStep, hid))
    session.commit()
    return step.id if step else None


# ---------------------------------------------------------------- load / save

@router.get("/profiles")
def profiles(photo_id: int, session: SessionDep) -> list[dict]:
    return available_profiles(meta_of(_photo(session, photo_id)))


@router.get("/{photo_id}")
def load(photo_id: int, session: SessionDep) -> dict:
    p = _photo(session, photo_id)
    prm = _params(session, p)
    meta = meta_of(p)
    dec = decoded_for(session, p)
    info: dict[str, Any] = {"is_raw": dec.is_raw, "width": dec.width, "height": dec.height}
    prof = resolve_profile(meta, prm["profile"])
    if dec.is_raw and prof.dcp is not None:
        cal = color.neutral_cal(meta.model)
        t, ti = color.xy_to_temp_tint(*color.camera_neutral_to_xy(prof.dcp, dec.cam_neutral, cal))
        info["as_shot"] = {"temp": round(t), "tint": round(ti)}
    lp = adobe.find_lens_profile(meta.lens, meta.make) if dec.is_raw else None
    info["lens_profile"] = lp.name if lp else None
    geo = geometry_for(dec, meta, prm)
    info["oriented"] = [geo.OW, geo.OH]
    hist = session.exec(select(HistoryStep).where(HistoryStep.photo_id == p.id)
                        .order_by(col(HistoryStep.id).desc()).limit(80)).all()
    snaps = session.exec(select(Snapshot).where(Snapshot.photo_id == p.id).order_by(Snapshot.id)).all()
    return {
        "params": prm,
        "defaults": PR.DEFAULTS,
        "info": info,
        "profiles": available_profiles(meta),
        "history": [{"id": h.id, "label": h.label, "at": h.created_at.isoformat()} for h in hist],
        "snapshots": [{"id": s.id, "name": s.name, "at": s.created_at.isoformat()} for s in snaps],
        "rev": p.edit_rev,
    }


class SaveIn(BaseModel):
    params: dict
    label: str | None = None


@router.put("/{photo_id}")
def save(photo_id: int, body: SaveIn, session: SessionDep) -> dict:
    p = _photo(session, photo_id)
    hid = _save(session, p, PR.full(body.params), body.label)
    return {"ok": True, "rev": p.edit_rev, "edited": p.edited, "history_id": hid}


@router.post("/{photo_id}/reset")
def reset(photo_id: int, session: SessionDep) -> dict:
    p = _photo(session, photo_id)
    _save(session, p, PR.full({}), "초기화")
    return {"ok": True, "params": PR.full({}), "rev": p.edit_rev}


@router.get("/{photo_id}/history/{hid}")
def history_params(photo_id: int, hid: int, session: SessionDep) -> dict:
    h = session.get(HistoryStep, hid)
    if not h or h.photo_id != photo_id:
        raise HTTPException(404)
    return {"params": PR.full(h.params)}


# ---------------------------------------------------------------- render

class RenderIn(BaseModel):
    params: dict
    w: int = 1600
    h: int = 1200
    region: list[float] | None = None  # crop-normalised [x0,y0,x1,y1] for zoomed views
    hist: bool = True
    quality: int = 88
    mask_overlay: int | None = None  # index of a mask to visualise


@router.post("/{photo_id}/render")
def render(photo_id: int, body: RenderIn, session: SessionDep) -> Response:
    p = _photo(session, photo_id)
    prm = PR.full(body.params)
    dec = decoded_for(session, p)
    meta = meta_of(p)
    geo = geometry_for(dec, meta, prm)
    cw, ch = geo.crop_px
    region = tuple(body.region) if body.region else (0.0, 0.0, 1.0, 1.0)
    rw, rh = (region[2] - region[0]) * cw, (region[3] - region[1]) * ch
    s = min(body.w / rw, body.h / rh, 2.0)
    w, h = max(1, round(rw * s)), max(1, round(rh * s))
    ai = cached_ai_masks(p, prm)
    res = render_params(dec, meta, prm, w, h, region=region, ai_masks=ai, want_hist=body.hist)
    img = res.image
    if body.mask_overlay is not None and 0 <= body.mask_overlay < len(prm["masks"]):
        from app.engine.masks import mask_overlay

        with torch.no_grad():
            grid, _vig, uu, vv = sample_grid(geo, w, h, region)
            m = mask_overlay(prm["masks"][body.mask_overlay], uu, vv, grid, ai)
            red = torch.tensor([1.0, 0.15, 0.2], device=img.device).view(3, 1, 1)
            img = img * (1 - 0.5 * m) + red * 0.5 * m
    info = {
        "w": w, "h": h, "crop_px": [cw, ch], "oriented": [geo.OW, geo.OH],
        "as_shot": {"temp": round(res.as_shot_temp), "tint": round(res.as_shot_tint)},
        "wb": {"temp": round(res.temp), "tint": round(res.tint)},
        "clipped": res.clipped,
        "missing_ai": sorted(needed_kinds(prm) - set(ai)),
        "missing_denoise": res.missing_denoise,
    }
    headers = {"X-Info": base64.b64encode(json.dumps(info).encode()).decode(), "Cache-Control": "no-store"}
    if res.histogram:
        hist = np.array(res.histogram).reshape(3, 64, 4).sum(-1)
        headers["X-Hist"] = base64.b64encode(hist.astype(np.uint32).tobytes()).decode()
    return Response(encode_jpeg(img, body.quality), media_type="image/jpeg", headers=headers)


class PickIn(BaseModel):
    params: dict
    x: float  # crop-normalised
    y: float


@router.post("/{photo_id}/pick-wb")
def pick_wb(photo_id: int, body: PickIn, session: SessionDep) -> dict:
    """White balance eyedropper: make the clicked spot neutral."""
    p = _photo(session, photo_id)
    prm = PR.full(body.params)
    dec = decoded_for(session, p)
    meta = meta_of(p)
    prof = resolve_profile(meta, prm["profile"])
    if not dec.is_raw or prof.dcp is None:
        raise HTTPException(400, "RAW 사진에서만 쓸 수 있어요.")
    geo = geometry_for(dec, meta, prm)
    r = 0.006
    region = (max(0, body.x - r), max(0, body.y - r), min(1, body.x + r), min(1, body.y + r))
    with torch.no_grad():
        grid, vig, _, _ = sample_grid(geo, 9, 9, region)
        cam = _sample(dec.preview, grid)
        if vig is not None:
            cam = cam * vig.unsqueeze(0)
        n = cam.flatten(1).mean(1).double().cpu().numpy()
    if n.max() <= 1e-5 or n.max() > 0.98:
        raise HTTPException(400, "너무 어둡거나 하얗게 날아간 곳이에요. 다른 곳을 찍어 주세요.")
    n = n / n.max()
    t, ti = color.xy_to_temp_tint(*color.camera_neutral_to_xy(prof.dcp, n, color.neutral_cal(meta.model)))
    return {"temp": round(max(2000, min(50000, t))), "tint": round(max(-150, min(150, ti)))}


class AutoIn(BaseModel):
    params: dict


@router.post("/{photo_id}/auto")
def auto_tone(photo_id: int, body: AutoIn, session: SessionDep) -> dict:
    """Lightroom-like 'Auto': exposure, contrast, highlights, shadows, whites, blacks, vibrance."""
    from app.engine.render import _sample as sample_img
    from app.engine.render import _scene_linear, color_plan, tune
    from app.engine import pipeline as P

    p = _photo(session, photo_id)
    prm = PR.full(body.params)
    dec = decoded_for(session, p)
    meta = meta_of(p)
    prof = resolve_profile(meta, prm["profile"])
    cp = color_plan(dec, meta, prm, prof)
    geo = geometry_for(dec, meta, prm)
    cw, ch = geo.crop_px
    s = 512 / max(cw, ch)
    with torch.no_grad():
        grid, vig, _, _ = sample_grid(geo, max(8, round(cw * s)), max(8, round(ch * s)))
        rgb = _scene_linear(sample_img(dec.preview, grid), cp, vig) * (2 ** (tune()["baseline_ev"] + cp.baseline_ev))
        L = torch.log2(P.luma(rgb).clamp(min=1e-6) / P.MID_GREY).flatten()
        q = torch.quantile(L, torch.tensor([0.005, 0.1, 0.5, 0.9, 0.99, 0.995], device=L.device)).tolist()
    p005, p10, p50, p90, p99, p995 = q
    # exposure: put the (centre-weighted) median a little under mid grey
    exposure = float(np.clip(-0.35 - p50, -3.0, 3.0)) * 0.85
    p10, p50, p90, p99, p995, p005 = (v + exposure for v in (p10, p50, p90, p99, p995, p005))
    highlights = -min(100.0, max(0.0, (p99 - 1.8) * 45))
    shadows = min(70.0, max(0.0, (-3.2 - p10) * 25))
    whites = float(np.clip((2.4 - p995) * 18, -40, 40))
    blacks = float(np.clip((p005 + 6.0) * -6, -35, 20))
    spread = p90 - p10
    contrast = float(np.clip((4.2 - spread) * 8, -20, 25))
    return {
        "exposure": round(exposure * 20) / 20, "contrast": round(contrast), "highlights": round(highlights),
        "shadows": round(shadows), "whites": round(whites), "blacks": round(blacks), "vibrance": 12,
    }


# ---------------------------------------------------------------- snapshots

class SnapIn(BaseModel):
    name: str
    params: dict


@router.post("/{photo_id}/snapshots")
def add_snapshot(photo_id: int, body: SnapIn, session: SessionDep) -> dict:
    _photo(session, photo_id)
    s = Snapshot(photo_id=photo_id, name=body.name, params=json.dumps(PR.diff(PR.full(body.params))))
    session.add(s)
    session.commit()
    session.refresh(s)
    return {"id": s.id, "name": s.name, "at": s.created_at.isoformat()}


@router.get("/{photo_id}/snapshots/{sid}")
def get_snapshot(photo_id: int, sid: int, session: SessionDep) -> dict:
    s = session.get(Snapshot, sid)
    if not s or s.photo_id != photo_id:
        raise HTTPException(404)
    return {"params": PR.full(s.params)}


@router.delete("/{photo_id}/snapshots/{sid}")
def del_snapshot(photo_id: int, sid: int, session: SessionDep) -> dict:
    s = session.get(Snapshot, sid)
    if s and s.photo_id == photo_id:
        session.delete(s)
        session.commit()
    return {"ok": True}


# ---------------------------------------------------------------- sync / paste / presets

class ApplyIn(BaseModel):
    ids: list[int]
    partial: dict | None = None  # explicit settings
    from_id: int | None = None  # or copy groups from this photo
    groups: list[str] | None = None
    label: str = "설정 붙여넣기"


@router.post("/apply")
def apply(body: ApplyIn, session: SessionDep) -> dict:
    partial = body.partial or {}
    if body.from_id is not None:
        src = _params(session, _photo(session, body.from_id))
        groups = body.groups or [g for g in PR.GROUPS if g not in ("geometry", "masks")]
        partial = PR.pick_groups(src, groups)
    n = 0
    for pid in body.ids:
        if pid == body.from_id:
            continue
        p = session.get(Photo, pid)
        if not p:
            continue
        prm = PR.apply_partial(_params(session, p), partial)
        _save(session, p, prm, body.label)
        n += 1
    return {"ok": True, "count": n}


@router.get("/presets/all")
def list_presets(session: SessionDep) -> list[dict]:
    out = [{"id": pr.id, "name": pr.name, "group": pr.group, "params": json.loads(pr.params)}
           for pr in session.exec(select(Preset).order_by(Preset.group, Preset.name))]
    return BUILTIN_PRESETS + out


class PresetIn(BaseModel):
    name: str
    group: str = "사용자"
    params: dict | None = None
    from_id: int | None = None
    groups: list[str] | None = None


@router.post("/presets")
def add_preset(body: PresetIn, session: SessionDep) -> dict:
    partial = body.params or {}
    if body.from_id is not None:
        partial = PR.pick_groups(_params(session, _photo(session, body.from_id)),
                                 body.groups or ["basic", "curve", "color", "detail", "effects"])
    pr = Preset(name=body.name, group=body.group, params=json.dumps(partial, ensure_ascii=False))
    session.add(pr)
    session.commit()
    session.refresh(pr)
    return {"id": pr.id, "name": pr.name, "group": pr.group, "params": partial}


@router.delete("/presets/{pid}")
def del_preset(pid: int, session: SessionDep) -> dict:
    pr = session.get(Preset, pid)
    if pr:
        session.delete(pr)
        session.commit()
    return {"ok": True}


BUILTIN_PRESETS = [
    {"id": -1, "name": "선명한 풍경", "group": "기본 제공",
     "params": {"profile": "Adobe Landscape", "clarity": 15, "dehaze": 8, "vibrance": 20, "texture": 10}},
    {"id": -2, "name": "부드러운 인물", "group": "기본 제공",
     "params": {"profile": "Adobe Portrait", "texture": -20, "clarity": -10, "highlights": -20, "shadows": 15}},
    {"id": -3, "name": "따뜻한 필름", "group": "기본 제공",
     "params": {"contrast": -10, "highlights": -30, "shadows": 20, "blacks": 10, "vibrance": -10,
                "curve": [[0, 20], [64, 62], [192, 196], [255, 245]],
                "grading": {"shadows": {"hue": 210, "sat": 12, "lum": 0},
                            "highlights": {"hue": 45, "sat": 15, "lum": 0}},
                "grain": {"amount": 20, "size": 25, "roughness": 50}}},
    {"id": -4, "name": "흑백 하이콘", "group": "기본 제공",
     "params": {"bw": True, "contrast": 30, "clarity": 20, "blacks": -15, "whites": 10}},
    {"id": -5, "name": "여행 스냅 (자동 느낌)", "group": "기본 제공",
     "params": {"highlights": -40, "shadows": 30, "whites": 10, "blacks": -5, "vibrance": 15}},
]
