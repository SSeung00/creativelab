"""RAW -> linear camera RGB (LibRaw), kept on the GPU in a small LRU cache.

The decode is the only CPU-heavy step and runs once per photo; every slider change after
that re-renders from the cached linear data.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rawpy
import torch
from PIL import Image

from app.config import RAW_EXTS

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
PREVIEW_LONG_EDGE = 2560


@dataclass
class Decoded:
    key: str
    full: torch.Tensor  # (3,H,W) float16 linear camera RGB, white = 1.0, sensor orientation
    preview: torch.Tensor  # (3,h,w) float32, long edge <= PREVIEW_LONG_EDGE
    cam_neutral: np.ndarray  # camera As Shot neutral (max 1), None for non-RAW
    is_raw: bool
    width: int
    height: int


_cache: OrderedDict[str, Decoded] = OrderedDict()
_lock = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}
MAX_CACHED = 8


def _decode_raw(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with rawpy.imread(str(path)) as raw:
        wb = np.array(raw.camera_whitebalance[:3], dtype=np.float64)
        if wb.min() <= 0:
            wb = np.array(raw.daylight_whitebalance[:3], dtype=np.float64)
        rgb = raw.postprocess(
            output_color=rawpy.ColorSpace.raw,
            gamma=(1, 1),
            no_auto_bright=True,
            output_bps=16,
            use_camera_wb=False,
            use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            user_flip=0,
            highlight_mode=rawpy.HighlightMode.Clip,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            fbdd_noise_reduction=rawpy.FBDDNoiseReductionMode.Off,
        )
    neutral = 1.0 / wb
    return rgb, neutral / neutral.max()


def _decode_rgb(path: Path) -> np.ndarray:
    """JPEG/TIFF/PNG -> linear sRGB float (treated like a 'camera' with sRGB primaries)."""
    from PIL import ImageOps

    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    a = np.asarray(im).astype(np.float32) / 255.0
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def _to_preview(full: torch.Tensor) -> torch.Tensor:
    _, h, w = full.shape
    s = PREVIEW_LONG_EDGE / max(h, w)
    x = full.float().unsqueeze(0)
    if s < 1:
        x = torch.nn.functional.interpolate(x, scale_factor=s, mode="area")
    return x.squeeze(0).contiguous()


def get_decoded(path: Path, key: str | None = None) -> Decoded:
    key = key or str(path)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
        klock = _key_locks.setdefault(key, threading.Lock())
    with klock:
        with _lock:
            hit = _cache.get(key)
            if hit is not None:
                return hit
        is_raw = path.suffix.lower() in RAW_EXTS
        if is_raw:
            rgb16, neutral = _decode_raw(path)
            t = torch.from_numpy(rgb16.astype(np.float32)).to(DEVICE)
            t = (t / 65535.0).permute(2, 0, 1).contiguous()
        else:
            rgb = _decode_rgb(path)
            neutral = np.ones(3)
            t = torch.from_numpy(rgb).to(DEVICE).permute(2, 0, 1).contiguous()
        dec = Decoded(
            key=key,
            full=t.half(),
            preview=_to_preview(t),
            cam_neutral=neutral,
            is_raw=is_raw,
            width=t.shape[2],
            height=t.shape[1],
        )
        del t
        with _lock:
            _cache[key] = dec
            while len(_cache) > MAX_CACHED:
                _cache.popitem(last=False)
        return dec


def evict(key_prefix: str) -> None:
    with _lock:
        for k in [k for k in _cache if k.startswith(key_prefix)]:
            del _cache[k]
