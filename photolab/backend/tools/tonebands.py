"""Where does our render differ from Lightroom? Mean ΔL* per Lightroom-L* band for edited photos."""

import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lrcompare import lab, rows  # noqa: E402

from app.engine import render as R  # noqa: E402
from app.engine.decode import get_decoded  # noqa: E402
from app.engine.lrimport import lr_to_params, parse_lr_settings  # noqa: E402
from app.library.exif import read_exif  # noqa: E402

cat, prev_dir = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 12
bands = np.arange(0, 101, 10)
acc = np.zeros((len(bands) - 1, 2))
for rel, path, text in rows(cat, prev_dir, True, n):
    pf = os.path.join(prev_dir, rel)
    if not (os.path.exists(pf) and os.path.exists(path)):
        continue
    info = read_exif(Path(path))
    meta = R.Meta(info.get("make"), info.get("model"), info.get("lens"), info.get("focal_length"), info.get("aperture"), 1)
    lr = parse_lr_settings(text)
    prm = lr_to_params(lr)
    dec = get_decoded(Path(path))
    w, h = R.fit_size(*R.geometry_for(dec, meta, prm).crop_px, 512, 512)
    ref = lab(np.asarray(Image.open(pf).convert("RGB").resize((w, h)), np.float32))
    ours = lab(R.to_uint8(R.render_params(dec, meta, prm, w, h, output_space="adobergb").image).permute(1, 2, 0).cpu().numpy().astype(np.float32))
    row = []
    for i in range(len(bands) - 1):
        m = (ref[..., 0] >= bands[i]) & (ref[..., 0] < bands[i + 1])
        if m.sum() > 50:
            d = (ours[..., 0] - ref[..., 0])[m].mean()
            acc[i] += (d * m.sum(), m.sum())
            row.append(f"{d:+5.1f}")
        else:
            row.append("    .")
    dc = np.linalg.norm((ours[..., 1:] - ref[..., 1:]), axis=-1).mean()
    keys = ("Exposure2012", "Highlights2012", "Shadows2012", "Whites2012", "Blacks2012", "Contrast2012", "Clarity2012", "Texture", "Vibrance", "Saturation")
    print(Path(path).stem[-14:], " ".join(row), f"| dC {dc:4.1f} |", " ".join(f"{k[:2]}{lr.get(k, 0)}" for k in keys))
print("ALL  ", " ".join(f"{a / max(b, 1):+5.1f}" for a, b in acc))
