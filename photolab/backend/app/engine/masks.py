"""Local adjustment masks -> per-pixel parameter offsets.

A mask = components (linear / radial / brush / ai / range) combined with add / subtract /
intersect, plus an `adjust` dict of local sliders. Geometry is in crop-normalised coordinates
(0..1 over the cropped output), so masks follow the crop; AI masks live in sensor coordinates
and are sampled through the same grid as the image.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageDraw, ImageFilter

from app.engine.decode import DEVICE

# local slider -> (pipeline key, scale)
LOCAL_SCALE = {
    "exposure": 1.0, "contrast": 0.01, "highlights": 0.01, "shadows": 0.01, "whites": 0.01,
    "blacks": 0.01, "temp": 0.01, "tint": 0.01, "texture": 0.01, "clarity": 0.01, "dehaze": 0.01,
    "saturation": 0.01, "vibrance": 0.01, "hue": 0.01 * 30 / 60, "sharpness": 0.01, "noise": 0.01,
}


def _linear(c: dict, uu, vv) -> torch.Tensor:
    x0, y0, x1, y1 = c["x0"], c["y0"], c["x1"], c["y1"]
    dx, dy = x1 - x0, y1 - y0
    ln2 = dx * dx + dy * dy
    if ln2 < 1e-9:
        return torch.zeros_like(uu)
    t = ((uu - x0) * dx + (vv - y0) * dy) / ln2
    t = t.clamp(0, 1)
    return 1 - t * t * (3 - 2 * t)


def _radial(c: dict, uu, vv) -> torch.Tensor:
    cx, cy = c["cx"], c["cy"]
    rx, ry = max(c.get("rx", 0.2), 1e-4), max(c.get("ry", 0.2), 1e-4)
    a = math.radians(c.get("angle", 0))
    du, dv = uu - cx, vv - cy
    x = (math.cos(a) * du + math.sin(a) * dv) / rx
    y = (-math.sin(a) * du + math.cos(a) * dv) / ry
    d = torch.sqrt(x * x + y * y)
    feather = max(c.get("feather", 50) / 100.0, 0.01)
    inner = 1 - feather
    t = ((d - inner) / (1 - inner + 1e-6)).clamp(0, 1)
    w = 1 - t * t * (3 - 2 * t)
    return w


@functools.lru_cache(maxsize=32)
def _brush_canvas(strokes_json: str, aspect: float) -> torch.Tensor:
    strokes = json.loads(strokes_json)
    W = 1024 if aspect >= 1 else round(1024 * aspect)
    H = round(1024 / aspect) if aspect >= 1 else 1024
    acc = np.zeros((H, W), np.float32)
    for s in strokes:
        pts = s.get("pts") or []
        if not pts:
            continue
        size = max(1, int(s.get("size", 0.05) * max(W, H)))
        feather = s.get("feather", 50) / 100.0
        flow = s.get("flow", 100) / 100.0
        layer = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(layer)
        xy = [(p[0] * W, p[1] * H) for p in pts]
        core = max(1, int(size * (1 - feather * 0.7)))
        if len(xy) == 1:
            x, y = xy[0]
            d.ellipse([x - core / 2, y - core / 2, x + core / 2, y + core / 2], fill=255)
        else:
            d.line(xy, fill=255, width=core, joint="curve")
            for x, y in (xy[0], xy[-1]):
                d.ellipse([x - core / 2, y - core / 2, x + core / 2, y + core / 2], fill=255)
        if feather > 0:
            layer = layer.filter(ImageFilter.GaussianBlur(radius=max(0.5, size * feather * 0.25)))
        a = np.asarray(layer, np.float32) / 255.0 * flow
        if s.get("erase"):
            acc = acc * (1 - a)
        else:
            acc = np.maximum(acc, a) if flow >= 1 else acc + (1 - acc) * a
    return torch.tensor(acc, device=DEVICE)


def _sample(m: torch.Tensor, uu, vv) -> torch.Tensor:
    grid = torch.stack([uu * 2 - 1, vv * 2 - 1], -1).unsqueeze(0)
    return F.grid_sample(m[None, None], grid, mode="bilinear", padding_mode="border",
                         align_corners=False)[0, 0]


def component_weight(c: dict, uu, vv, grid, ai_masks: dict, aspect: float, lum=None) -> torch.Tensor:
    t = c.get("type")
    if t == "linear":
        w = _linear(c, uu, vv)
    elif t == "radial":
        w = _radial(c, uu, vv)
    elif t == "brush":
        w = _sample(_brush_canvas(json.dumps(c.get("strokes", []), sort_keys=True), aspect), uu, vv)
    elif t == "ai":
        m = ai_masks.get(c.get("kind", "subject"))
        if m is None:
            w = torch.zeros_like(uu)
        else:
            w = F.grid_sample(m[None, None].float(), grid, mode="bilinear", padding_mode="border",
                              align_corners=False)[0, 0]
    elif t == "range" and lum is not None:
        lo, hi = c.get("lo", 0.0), c.get("hi", 1.0)
        sm = max(c.get("smooth", 0.1), 1e-3)
        w = ((lum - lo + sm) / sm).clamp(0, 1) * ((hi + sm - lum) / sm).clamp(0, 1)
    else:
        w = torch.zeros_like(uu)
    if c.get("invert"):
        w = 1 - w
    return w.clamp(0, 1)


def mask_weight(mask: dict, uu, vv, grid, ai_masks: dict, aspect: float = 1.5) -> torch.Tensor:
    w = None
    for c in mask.get("components", []):
        cw = component_weight(c, uu, vv, grid, ai_masks, aspect)
        mode = c.get("mode", "add")
        if w is None:
            w = cw if mode == "add" else torch.zeros_like(uu)
        elif mode == "add":
            w = torch.maximum(w, cw)
        elif mode == "subtract":
            w = w * (1 - cw)
        elif mode == "intersect":
            w = w * cw
    if w is None:
        w = torch.zeros_like(uu)
    if mask.get("invert"):
        w = 1 - w
    return w * (mask.get("amount", 100) / 100.0)


def mask_weights(masks: list[dict], uu, vv, grid, ai_masks: dict) -> dict[str, torch.Tensor]:
    out: dict[str, torch.Tensor] = {}
    aspect = float(uu.shape[1]) / max(1.0, float(uu.shape[0]))
    for m in masks:
        adj = {k: v for k, v in (m.get("adjust") or {}).items() if v}
        if not adj:
            continue
        w = mask_weight(m, uu, vv, grid, ai_masks, aspect)
        for k, v in adj.items():
            if k not in LOCAL_SCALE:
                continue
            add = w * (float(v) * LOCAL_SCALE[k])
            out[k] = out[k] + add if k in out else add
    return out


def mask_overlay(mask: dict, uu, vv, grid, ai_masks: dict) -> torch.Tensor:
    aspect = float(uu.shape[1]) / max(1.0, float(uu.shape[0]))
    return mask_weight(mask, uu, vv, grid, ai_masks, aspect)


def masks_hash(masks: list[dict]) -> str:
    return hashlib.md5(json.dumps(masks, sort_keys=True).encode()).hexdigest()
