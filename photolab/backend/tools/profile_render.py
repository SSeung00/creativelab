"""Time each stage of a preview render (CUDA-synchronised)."""

import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import render as R  # noqa: E402
from app.engine.decode import get_decoded  # noqa: E402
from app.engine.params import full  # noqa: E402

path = Path(sys.argv[1])
meta = R.Meta("SONY", "ILCE-7M4", "FE 24-105mm F4 G OSS", 50.0, 8.0, 1)
dec = get_decoded(path)
prm = full({"highlights": -30, "shadows": 20, "clarity": 10, "vibrance": 10})

stamps = []
orig = {}


def wrap(mod, name):
    fn = getattr(mod, name)
    orig[name] = fn

    def w(*a, **k):
        torch.cuda.synchronize()
        t = time.perf_counter()
        r = fn(*a, **k)
        torch.cuda.synchronize()
        stamps.append((name, (time.perf_counter() - t) * 1000))
        return r

    setattr(mod, name, w)


for n in ("color_plan", "geometry_for", "analyse", "sample_grid", "_scene_linear", "_sample"):
    wrap(R, n)
for n in ("apply_hsv_table", "rgb_tone", "tone_gain", "apply_hsl", "apply_vibrance_saturation",
          "apply_grading", "denoise", "sharpen", "srgb_encode", "srgb_decode", "lut1d", "mat3"):
    wrap(R.P, n)

for i in range(4):
    stamps.clear()
    torch.cuda.synchronize()
    t = time.perf_counter()
    res = R.render_params(dec, meta, prm, 1600, 1067, want_hist=True)
    torch.cuda.synchronize()
    total = (time.perf_counter() - t) * 1000
    t2 = time.perf_counter()
    R.encode_jpeg(res.image)
    enc = (time.perf_counter() - t2) * 1000
agg = {}
for n, ms in stamps:
    agg[n] = agg.get(n, 0) + ms
for n, ms in sorted(agg.items(), key=lambda x: -x[1]):
    print(f"{n:28s} {ms:7.1f} ms")
print(f"TOTAL {total:.1f} ms   jpeg {enc:.1f} ms")
