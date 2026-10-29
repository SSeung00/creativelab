"""AI culling helper: focus/blur, closed eyes, exposure clipping, burst grouping, best shot.

Everything runs on the camera's embedded JPEG (no RAW decode), so a folder of a thousand
photos takes about a minute.
"""

from __future__ import annotations

import io
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps
from sqlmodel import Session, col, select

from app.ai import models
from app.db import engine
from app.engine.decode import DEVICE
from app.jobs import JobContext
from app.library.thumbs import embedded_jpeg, orient
from app.models import AiScore, Photo

_face = None
_face_lock = threading.Lock()
LAPLACE = torch.tensor([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=torch.float32).view(1, 1, 3, 3)


def _landmarker():
    global _face
    with _face_lock:
        if _face is None:
            from mediapipe.tasks.python import BaseOptions
            from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions, RunningMode

            opts = FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(models.path("face"))),
                running_mode=RunningMode.IMAGE,
                num_faces=6,
                output_face_blendshapes=True,
                min_face_detection_confidence=0.5,
            )
            _face = FaceLandmarker.create_from_options(opts)
    return _face


def _load(session: Session, p: Photo) -> np.ndarray:
    data, _ = embedded_jpeg(session, p)
    im = Image.open(io.BytesIO(data))
    im.draft("RGB", (1600, 1600))
    im = im.convert("RGB")
    im = ImageOps.exif_transpose(im) if p.ext in ("jpg", "jpeg") else orient(im, p.orientation)
    im.thumbnail((1600, 1600))
    return np.asarray(im)


@torch.no_grad()
def _sharpness(rgb: np.ndarray) -> tuple[float, float]:
    """(edge sharpness, edge strength). Sharpness compares gradients at full and 1/4 resolution
    on the strongest edges: a crisp edge keeps its full-res gradient, a blurred one spreads out.
    The ratio barely depends on how much texture the scene has."""
    t = torch.tensor(rgb, dtype=torch.float32, device=DEVICE).permute(2, 0, 1) / 255.0
    y = (0.299 * t[0] + 0.587 * t[1] + 0.114 * t[2])[None, None]

    def grad(x):
        gx = x[..., :, 1:] - x[..., :, :-1]
        gy = x[..., 1:, :] - x[..., :-1, :]
        return torch.sqrt(gx[..., :-1, :] ** 2 + gy[..., :, :-1] ** 2)

    small = F.avg_pool2d(y, 4)
    gc = grad(small)
    edge = torch.quantile(gc.flatten(), 0.985).item()
    gf = F.max_pool2d(grad(y), 4)  # strongest fine gradient within each coarse cell
    h = min(gf.shape[-2], gc.shape[-2])
    w = min(gf.shape[-1], gc.shape[-1])
    gf, gcc = gf[..., :h, :w], gc[..., :h, :w]
    m = gcc > torch.quantile(gcc.flatten(), 0.97)
    ratio = torch.quantile(gf[m] / gcc[m].clamp(min=1e-4), 0.9).item() if bool(m.any()) else 0.0
    return ratio, edge


def _eyes(rgb: np.ndarray) -> tuple[int, bool]:
    try:
        import mediapipe as mp

        lm = _landmarker()
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
    except Exception:
        return 0, False
    faces = len(res.face_landmarks or [])
    closed = False
    h, w = rgb.shape[:2]
    for i, shapes in enumerate(res.face_blendshapes or []):
        pts = res.face_landmarks[i]
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        size = (max(xs) - min(xs)) * w
        if size < 60:  # tiny background faces don't count
            continue
        s = {b.category_name: b.score for b in shapes}
        if s.get("eyeBlinkLeft", 0) > 0.55 and s.get("eyeBlinkRight", 0) > 0.55:
            closed = True
    return faces, closed


def _signature(rgb: np.ndarray) -> np.ndarray:
    small = np.asarray(Image.fromarray(rgb).resize((16, 12), Image.Resampling.BILINEAR), np.float32) / 255.0
    return small.flatten()


def run(photo_ids: list[int], ctx: JobContext) -> str:
    with Session(engine) as s:
        photos = list(s.exec(select(Photo).where(col(Photo.id).in_(photo_ids))))
    photos.sort(key=lambda p: (p.capture_time or datetime.min, p.filename))
    n = len(photos)
    feats: dict[int, dict] = {}

    def work(p: Photo):
        try:
            with Session(engine) as s:
                return p.id, _load(s, p)
        except Exception:
            return p.id, None

    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for pid, rgb in pool.map(work, photos):
            done += 1
            if rgb is None:
                continue
            faces, closed = _eyes(rgb)
            lum = rgb.mean(-1)
            ratio, edge = _sharpness(rgb)
            feats[pid] = {
                "sharp": ratio,
                "edge": edge,
                "faces": faces,
                "closed": closed,
                "hi": float((rgb.max(-1) >= 250).mean()),
                "lo": float((lum <= 4).mean()),
                "sig": _signature(rgb),
            }
            ctx.progress(done, n, f"사진 살펴보는 중… {done:,}/{n:,}")
            if ctx.cancelled:
                return "취소됨"

    # burst groups: shots within 2 s that look alike
    groups: list[list[Photo]] = []
    for p in photos:
        if p.id not in feats:
            continue
        if groups:
            last = groups[-1][-1]
            dt = (p.capture_time - last.capture_time).total_seconds() if p.capture_time and last.capture_time else 99
            diff = float(np.abs(feats[p.id]["sig"] - feats[last.id]["sig"]).mean())
            if dt <= 2.0 and diff < 0.08:
                groups[-1].append(p)
                continue
        groups.append([p])

    EDGE_MIN = 0.06  # flatter scenes (night sky, fog, plain walls): no verdict
    blurry_n = closed_n = 0
    with Session(engine) as s:
        for gi, g in enumerate(groups):
            sharp = np.array([feats[p.id]["sharp"] for p in g])
            ref = float(np.median(sharp)) if len(g) >= 3 else float(sharp.max())
            best = None
            best_score = -1e9
            for p in g:
                f = feats[p.id]
                rel = (f["sharp"] - ref) / 0.15  # ~ +-1 per noticeable focus step
                judged = f["edge"] >= EDGE_MIN
                blurry = judged and (f["sharp"] < 0.7 or (len(g) >= 3 and rel < -2.5))
                score = f["sharp"] - (0.6 if f["closed"] else 0) - 2 * max(0.0, f["hi"] - 0.02) - f["lo"]
                if score > best_score:
                    best, best_score = p.id, score
                row = s.get(AiScore, p.id) or AiScore(photo_id=p.id)
                row.sharpness = f["sharp"]
                row.sharp_rel = rel
                row.blurry = bool(blurry)
                row.faces = f["faces"]
                row.eyes_closed = f["closed"]
                row.clipped_hi = f["hi"]
                row.clipped_lo = f["lo"]
                row.group_id = gi if len(g) > 1 else None
                row.group_size = len(g)
                row.score = score
                row.scored_at = datetime.now()
                s.add(row)
                blurry_n += blurry
                closed_n += f["closed"]
            for p in g:
                row = s.get(AiScore, p.id)
                row.best_in_group = len(g) > 1 and p.id == best
                s.add(row)
        s.commit()
    bursts = sum(1 for g in groups if len(g) > 1)
    return f"{n:,}장 분석 · 흔들림/초점 의심 {blurry_n}장 · 눈 감음 {closed_n}장 · 연사 묶음 {bursts}개"
