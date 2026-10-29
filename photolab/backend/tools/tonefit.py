"""Derive Lightroom's effective tone response: scene-linear luminance (ours) vs LR output luminance."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lrcompare import rows  # noqa: E402

from app.engine import pipeline as P  # noqa: E402
from app.engine.decode import get_decoded  # noqa: E402
from app.engine.lrimport import lr_to_params, parse_lr_settings  # noqa: E402
from app.engine.render import (  # noqa: E402
    Meta, _sample, _scene_linear, color_plan, geometry_for, resolve_profile, sample_grid,
)
from app.library.exif import read_exif  # noqa: E402

cat, previews = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 20
xs, ys = [], []
for rel, path, text in rows(cat, previews, False, n):
    prev = os.path.join(previews, rel)
    if not os.path.exists(prev):
        continue
    ref = np.asarray(Image.open(prev).convert("RGB"), np.float32) / 255.0
    info = read_exif(Path(path))
    meta = Meta(info.get("make"), info.get("model"), info.get("lens"), info.get("focal_length"), info.get("aperture"), 1)
    prm = lr_to_params(parse_lr_settings(text))
    dec = get_decoded(Path(path))
    prof = resolve_profile(meta, prm["profile"])
    cp = color_plan(dec, meta, prm, prof)
    geo = geometry_for(dec, meta, prm)
    h, w = ref.shape[:2]
    with torch.no_grad():
        grid, vig, _, _ = sample_grid(geo, w, h)
        rgb = _scene_linear(_sample(dec.full, grid), cp, vig)
        Y = P.luma(rgb).cpu().numpy()
    lin = ref ** 2.19921875
    Yr = lin @ np.array([0.2974, 0.6273, 0.0753])
    sel = np.random.default_rng(0).choice(Y.size, 40000, replace=False)
    xs.append(Y.flatten()[sel])
    ys.append(Yr.flatten()[sel])
x = np.concatenate(xs)
y = np.concatenate(ys)
bins = np.exp2(np.arange(-10, 3.01, 0.25)) * 0.18
print("scene/0.18 (stops)  ->  LR out Y (median)   LR out L*")
for lo, hi in zip(bins[:-1], bins[1:], strict=False):
    m = (x >= lo) & (x < hi)
    if m.sum() < 200:
        continue
    med = np.median(y[m])
    L = 116 * np.cbrt(med) - 16 if med > 0.008856 else 903.3 * med
    print(f"{np.log2(np.sqrt(lo * hi) / 0.18):+6.2f}  {med:7.4f}  {L:6.1f}  n={m.sum()}")
np.savez(os.environ.get("TONEFIT_OUT", "tonefit.npz"), x=x, y=y)
