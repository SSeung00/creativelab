"""AI masks (subject / sky / person / background) cached as PNGs in sensor orientation.

The models live in app/ai/; this module only loads finished masks for rendering.
"""

from __future__ import annotations

import numpy as np
import torch
from PIL import Image

from app.config import CACHE_DIR
from app.engine.decode import DEVICE

AI_DIR = CACHE_DIR / "ai"
AI_DIR.mkdir(parents=True, exist_ok=True)
KINDS = ("subject", "sky", "person", "background")

_mem: dict[str, torch.Tensor] = {}


def mask_file(photo_id: int, kind: str):
    return AI_DIR / f"{photo_id // 1000:04d}" / f"{photo_id}_{kind}.png"


def load_mask(photo_id: int, kind: str) -> torch.Tensor | None:
    key = f"{photo_id}:{kind}"
    if key in _mem:
        return _mem[key]
    f = mask_file(photo_id, kind)
    if not f.exists():
        return None
    a = np.asarray(Image.open(f).convert("L"), np.float32) / 255.0
    t = torch.tensor(a, device=DEVICE)
    if len(_mem) > 64:
        _mem.clear()
    _mem[key] = t
    return t


def save_mask(photo_id: int, kind: str, mask: np.ndarray) -> None:
    f = mask_file(photo_id, kind)
    f.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8)).save(f)
    _mem.pop(f"{photo_id}:{kind}", None)


def needed_kinds(prm: dict) -> set[str]:
    kinds = set()
    for m in prm.get("masks", []):
        for c in m.get("components", []):
            if c.get("type") == "ai":
                kinds.add(c.get("kind", "subject"))
    return kinds


def cached_ai_masks(photo, prm: dict) -> dict[str, torch.Tensor]:
    pid = photo.master_id or photo.id
    out = {}
    for k in needed_kinds(prm):
        m = load_mask(pid, k)
        if m is not None:
            out[k] = m
    return out
