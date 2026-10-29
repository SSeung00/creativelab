"""Fit tone-operator constants so edited photos match Lightroom's renders.

python tools/tune.py <catalog.lrcat> <Previews.lrdata> [--n 48] [--iters 600]
Splits photos into train/test, optimises on train (Powell), reports both, writes data/tune.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lrcompare import lab, rows  # noqa: E402

from app.engine import decode as D  # noqa: E402
from app.engine import render as R  # noqa: E402
from app.engine.lrimport import lr_to_params, parse_lr_settings  # noqa: E402
from app.library.exif import read_exif  # noqa: E402

# Tone/colour constants with enough signal in the user's edits (texture, clarity, contrast and
# saturation were barely used, so they keep hand-set defaults).
KEYS = {  # name: (lower, upper)
    "shadows": (0.2, 3.0), "highlights": (0.2, 4.0), "whites": (0.1, 4.0), "blacks": (0.1, 6.0),
    "vibrance_k": (0.2, 3.0), "sh_hi": (-2.0, 3.0), "sh_lo": (-8.0, -1.5), "hl_lo": (-2.5, 1.5),
    "hl_hi": (1.0, 4.5), "wh_lo": (-2.0, 2.0), "wh_hi": (1.5, 4.5), "bl_hi": (-4.0, 0.0), "bl_lo": (-10.0, -3.0),
    "dehaze": (0.2, 4.0),
}
LOWPASS = 4  # compare 4x-downsampled images: tone & colour, not pixel-level detail
SIZE = 384


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog")
    ap.add_argument("previews")
    ap.add_argument("--n", type=int, default=48)
    ap.add_argument("--iters", type=int, default=600)
    args = ap.parse_args()
    D.MAX_CACHED = 200
    items = []
    for rel, path, text in rows(args.catalog, args.previews, True, args.n):
        prev = os.path.join(args.previews, rel)
        if not (os.path.exists(prev) and os.path.exists(path)):
            continue
        info = read_exif(Path(path))
        meta = R.Meta(info.get("make"), info.get("model"), info.get("lens"), info.get("focal_length"),
                      info.get("aperture"), 1)
        prm = lr_to_params(parse_lr_settings(text))
        dec = D.get_decoded(Path(path))
        geo = R.geometry_for(dec, meta, prm)
        w, h = R.fit_size(*geo.crop_px, SIZE, SIZE)
        ref = Image.open(prev).convert("RGB").resize((w // LOWPASS, h // LOWPASS), Image.Resampling.BOX)
        items.append((dec, meta, prm, w, h, lab(np.asarray(ref, np.float32)), Path(path).name))
    print(f"{len(items)} photos")
    rng = np.random.default_rng(1)
    idx = rng.permutation(len(items))
    test_i = set(idx[: max(4, len(items) // 4)].tolist())
    train = [it for i, it in enumerate(items) if i not in test_i]
    test = [it for i, it in enumerate(items) if i in test_i]
    tn = R.tune()

    def score(subset):
        errs = []
        for dec, meta, prm, w, h, ref_lab, _ in subset:
            res = R.render_params(dec, meta, prm, w, h, output_space="adobergb")
            small = torch.nn.functional.avg_pool2d(res.image[None], LOWPASS)[0]
            small = small[:, : ref_lab.shape[0], : ref_lab.shape[1]]
            ours = R.to_uint8(small).permute(1, 2, 0).cpu().numpy().astype(np.float32)
            de = np.linalg.norm(lab(ours) - ref_lab[: ours.shape[0], : ours.shape[1]], axis=-1)
            errs.append(float(np.mean(np.clip(de, 0, 30))))
        return float(np.mean(errs)), errs

    names = list(KEYS)

    best = {"v": 1e9, "x": None, "n": 0}

    def f(x):
        for k, v in zip(names, x, strict=True):
            lo, hi = KEYS[k]
            tn[k] = float(np.clip(v, lo, hi))
        v = score(train)[0]
        best["n"] += 1
        if v < best["v"]:
            best["v"], best["x"] = v, np.array(x)
        if best["n"] % 10 == 0:
            print(f"  eval {best['n']}: best train {best['v']:.3f}", flush=True)
        return v

    x0 = np.array([tn.get(k, R.TUNE_DEFAULT.get(k, (KEYS[k][0] + KEYS[k][1]) / 2)) for k in names])
    for k, v in zip(names, x0, strict=True):
        tn[k] = float(v)
    base_train = score(train)[0]
    base_test, _ = score(test)
    print(f"before: train {base_train:.3f}  test {base_test:.3f}")
    t = time.time()
    r = minimize(f, x0, method="Powell", bounds=[KEYS[k] for k in names],
                 options={"maxfev": args.iters, "xtol": 1e-3, "ftol": 1e-4})
    f(best["x"] if best["x"] is not None else r.x)
    tr = score(train)[0]
    te, per = score(test)
    print(f"after ({time.time() - t:.0f}s, {r.nfev} evals): train {tr:.3f}  test {te:.3f}")
    for k in names:
        print(f"  {k:14s} {tn[k]:.3f}")
    if te < base_test:
        out = {k: v for k, v in tn.items()}
        R.TUNE_FILE.write_text(json.dumps(out, indent=2))
        print("saved", R.TUNE_FILE)
    else:
        print("test got worse; not saved")


if __name__ == "__main__":
    with torch.no_grad():
        main()
