"""AI masks: subject (IS-Net), sky / person (SegFormer ADE20K), background = 1 - subject.

Masks are inferred on the upright image, refined with a guided filter against the photo
itself (so edges follow hair, leaves, horizons), then stored in sensor orientation.
"""

from __future__ import annotations

import threading

import numpy as np
import torch
import torch.nn.functional as F

from app.ai import models
from app.engine import pipeline as P
from app.engine.ai_masks import load_mask, save_mask
from app.engine.decode import DEVICE, Decoded

WORK = 1536  # working resolution (long edge) of stored masks
ADE_SKY, ADE_PERSON = 2, 12

_seg = None
_isnet = None
_load_lock = threading.Lock()


def _segformer():
    global _seg
    with _load_lock:
        if _seg is None:
            from transformers import SegformerForSemanticSegmentation

            m = SegformerForSemanticSegmentation.from_pretrained(models.SEGFORMER, cache_dir=str(models.HF_DIR))
            _seg = m.to(DEVICE).eval().half()
    return _seg


def _isnet_session():
    global _isnet
    with _load_lock:
        if _isnet is None:
            import onnxruntime as ort

            providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
            try:
                _isnet = ort.InferenceSession(str(models.path("isnet")), providers=providers)
            except Exception:
                _isnet = ort.InferenceSession(str(models.path("isnet")), providers=["CPUExecutionProvider"])
    return _isnet


# EXIF orientation -> (k rot90 counter-clockwise, flip) to make the sensor image upright
def _upright(t: torch.Tensor, o: int) -> torch.Tensor:
    if o in (2, 4, 5, 7):
        t = torch.flip(t, dims=[-1])
    k = {1: 0, 2: 0, 3: 2, 4: 2, 5: 1, 6: 3, 7: 3, 8: 1}.get(o, 0)
    return torch.rot90(t, k, dims=(-2, -1))


def _to_sensor(t: torch.Tensor, o: int) -> torch.Tensor:
    k = {1: 0, 2: 0, 3: 2, 4: 2, 5: 1, 6: 3, 7: 3, 8: 1}.get(o, 0)
    t = torch.rot90(t, -k, dims=(-2, -1))
    if o in (2, 4, 5, 7):
        t = torch.flip(t, dims=[-1])
    return t


def _display_rgb(dec: Decoded) -> torch.Tensor:
    """Quick display-referred RGB (sensor orientation) for model input, (3,h,w) in 0..1."""
    x = dec.preview / torch.tensor(dec.cam_neutral, dtype=torch.float32, device=DEVICE).view(3, 1, 1)
    x = x / x.mean().clamp(min=1e-4) * 0.18
    return P.srgb_encode(x.clamp(0, 1) * 1.0).clamp(0, 1)


def _resize(t: torch.Tensor, h: int, w: int) -> torch.Tensor:
    return F.interpolate(t[None] if t.dim() == 3 else t[None, None], size=(h, w), mode="bilinear", align_corners=False)[0]


def _refine(prob: torch.Tensor, guide_rgb: torch.Tensor) -> torch.Tensor:
    """Edge-aware refinement of a soft mask (H,W) with the image (3,H,W)."""
    I = 0.299 * guide_rgb[0] + 0.587 * guide_rgb[1] + 0.114 * guide_rgb[2]
    r = max(2, round(max(prob.shape) * 0.004))
    eps = 1e-3
    mI = P.box(I, r)
    mp = P.box(prob, r)
    cov = P.box(I * prob, r) - mI * mp
    var = P.box(I * I, r) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return (P.box(a, r) * I + P.box(b, r)).clamp(0, 1)


@torch.no_grad()
def generate(photo_id: int, dec: Decoded, orientation: int, kind: str) -> None:
    if kind == "background":
        if load_mask(photo_id, "subject") is None:
            generate(photo_id, dec, orientation, "subject")
        save_mask(photo_id, "background", 1 - load_mask(photo_id, "subject").cpu().numpy())
        return
    rgb = _display_rgb(dec)
    _, H, W = rgb.shape
    s = WORK / max(H, W)
    h, w = round(H * s), round(W * s)
    work = _resize(rgb, h, w)  # sensor orientation, working res
    up = _upright(work, orientation)
    uh, uw = up.shape[1:]
    if kind == "subject":
        sess = _isnet_session()
        x = _resize(up, 1024, 1024)
        x = x / x.max().clamp(min=1e-6)
        x = x - torch.tensor([0.485, 0.456, 0.406], device=DEVICE).view(3, 1, 1)  # rembg/DIS normalisation
        out = sess.run(None, {sess.get_inputs()[0].name: x[None].float().cpu().numpy()})[0]
        m = torch.tensor(out[0, 0] if out.ndim == 4 else out[0], device=DEVICE)
        m = (m - m.min()) / (m.max() - m.min() + 1e-6)
        prob = _resize(m, uh, uw)[0]
    else:
        seg = _segformer()
        mean = torch.tensor([0.485, 0.456, 0.406], device=DEVICE).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225], device=DEVICE).view(3, 1, 1)
        x = _resize(up, 640, 640)
        logits = seg(pixel_values=((x - mean) / std)[None].half()).logits.float()
        probs = torch.softmax(F.interpolate(logits, size=(uh, uw), mode="bilinear", align_corners=False)[0], 0)
        prob = probs[ADE_SKY if kind == "sky" else ADE_PERSON]
    prob = _refine(prob, up)
    if kind == "sky":
        # skies are smooth; keep only confident regions, soften the edge a little
        prob = P.smoothstep(0.25, 0.75, prob)
    save_mask(photo_id, kind, _to_sensor(prob, orientation).cpu().numpy())
