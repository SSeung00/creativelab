"""AI noise reduction (SCUNet, real-image model) applied to the RAW base before development.

The denoised linear camera image is cached on disk (float16) so turning AI denoise on/off or
changing its amount afterwards is instant: the renderer blends original and denoised bases.
"""

from __future__ import annotations

import threading
from pathlib import Path

import torch
import torch.nn.functional as F

from app.ai import models
from app.config import CACHE_DIR
from app.engine import pipeline as P
from app.engine.decode import DEVICE, Decoded

DN_DIR = CACHE_DIR / "denoise"
DN_DIR.mkdir(parents=True, exist_ok=True)

_model = None
_lock = threading.Lock()
_mem: dict[int, torch.Tensor] = {}


def _net():
    global _model
    with _lock:
        if _model is None:
            from spandrel import ModelLoader

            _model = ModelLoader().load_from_file(str(models.path("scunet"))).model.to(DEVICE).eval().half()
    return _model


def cache_file(photo_id: int) -> Path:
    return DN_DIR / f"{photo_id // 1000:04d}" / f"{photo_id}.pt"


def has_denoised(photo_id: int) -> bool:
    return photo_id in _mem or cache_file(photo_id).exists()


def load_denoised(photo_id: int) -> torch.Tensor | None:
    t = _mem.get(photo_id)
    if t is not None:
        return t
    f = cache_file(photo_id)
    if not f.exists():
        return None
    t = torch.load(f, map_location=DEVICE, weights_only=True)
    if len(_mem) > 4:
        _mem.pop(next(iter(_mem)))
    _mem[photo_id] = t
    return t


@torch.no_grad()
def denoise_base(photo_id: int, dec: Decoded, progress=None) -> torch.Tensor:
    """Full-res linear camera RGB -> denoised (same space), cached."""
    net = _net()
    x = dec.full.float()
    n = torch.tensor(dec.cam_neutral, dtype=torch.float32, device=DEVICE).view(3, 1, 1)
    wb = x / n
    scale = float(torch.quantile(wb.flatten()[:: max(1, wb.numel() // 2_000_000)], 0.995).clamp(min=1e-3))
    g = P.srgb_encode((wb / scale).clamp(0, 1))
    _, H, W = g.shape
    tile, ov = 640, 48
    out = torch.zeros_like(g)
    acc = torch.zeros((1, H, W), device=DEVICE)
    ys = list(range(0, max(1, H - ov), tile - ov))
    xs = list(range(0, max(1, W - ov), tile - ov))
    total = len(ys) * len(xs)
    k = 0
    ramp = torch.ones((1, tile, tile), device=DEVICE)
    for y0 in ys:
        for x0 in xs:
            y1, x1 = min(H, y0 + tile), min(W, x0 + tile)
            patch = g[:, y0:y1, x0:x1]
            ph, pw = patch.shape[1:]
            pad_h, pad_w = (-ph) % 64, (-pw) % 64
            inp = F.pad(patch[None], (0, pad_w, 0, pad_h), mode="reflect").half()
            res = net(inp)[0, :, :ph, :pw].float()
            w = ramp[:, :ph, :pw].clone()
            # feather overlaps
            for i in range(ov):
                f = (i + 1) / (ov + 1)
                if y0 > 0:
                    w[:, i, :] *= f
                if x0 > 0:
                    w[:, :, i] *= f
                if y1 < H:
                    w[:, ph - 1 - i, :] *= f
                if x1 < W:
                    w[:, :, pw - 1 - i] *= f
            out[:, y0:y1, x0:x1] += res * w
            acc[:, y0:y1, x0:x1] += w
            k += 1
            if progress:
                progress(k, total)
    out = out / acc.clamp(min=1e-6)
    lin = P.srgb_decode(out.clamp(0, 1)) * scale
    # keep the original where it was clipped (denoiser cannot see beyond 1.0)
    lin = torch.where(wb > scale, wb, lin)
    res = (lin * n).half()
    f = cache_file(photo_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    torch.save(res.cpu(), f)
    _mem[photo_id] = res
    return res
