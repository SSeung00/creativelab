"""Compare PhotoLab renders with Lightroom's own 1:1 previews (calibration harness).

Usage: python tools/lrcompare.py <catalog.lrcat> <Previews.lrdata> [--n 12] [--edited] [--save DIR]

Reads the catalog read-only (copy it first if Lightroom is open). Originals are opened read-only.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.decode import get_decoded  # noqa: E402
from app.engine.lrimport import lr_to_params, parse_lr_settings  # noqa: E402
from app.engine.render import Meta, fit_size, geometry_for, render_params, to_uint8  # noqa: E402
from app.library.exif import read_exif  # noqa: E402


def srgb_to_lin(a):
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def lab(a):
    """Adobe RGB 8-bit -> CIELAB (D65) for delta-E."""
    lin = (a / 255.0) ** 2.19921875
    m = np.array([[0.5767, 0.1856, 0.1882], [0.2974, 0.6273, 0.0753], [0.0270, 0.0707, 0.9911]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def rows(cat: str, previews: str, edited: bool, n: int):
    pv = sqlite3.connect(f"file:{previews}/previews.db?mode=ro", uri=True)
    rendered = {}
    for image_id, uuid, digest, longdim in pv.execute("""
        select e.imageId, e.uuid, e.digest, max(l.longDimension) from ImageCacheEntry e
        join Pyramid p on p.uuid=e.uuid and p.digest=e.digest
        join PyramidLevel l on l.uuid=e.uuid and l.digest=e.digest
        where p.quality in ('final','full','standard') group by 1"""):
        rendered[int(image_id)] = f"{uuid[0]}/{uuid[:4]}/{uuid}-{digest}_{int(longdim)}"
    c = sqlite3.connect(f"file:{cat}?mode=ro", uri=True)
    q = """
    select i.id_local, r.absolutePath || fo.pathFromRoot || f.baseName || '.' || f.extension, s.text
    from Adobe_images i
    join AgLibraryFile f on f.id_local=i.rootFile
    join AgLibraryFolder fo on fo.id_local=f.folder
    join AgLibraryRootFolder r on r.id_local=fo.rootFolder
    join Adobe_imageDevelopSettings s on s.image=i.id_local
    where i.fileFormat='RAW' and s.hasDevelopAdjustmentsEx {} 0
    order by i.captureTime
    """.format(">" if edited else "<")
    allr = [(rendered[r[0]], r[1], r[2]) for r in c.execute(q).fetchall() if r[0] in rendered]
    step = max(1, len(allr) // n)
    return allr[::step][:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog")
    ap.add_argument("previews")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--edited", action="store_true")
    ap.add_argument("--save")
    args = ap.parse_args()
    results = []
    for rel, path, text in rows(args.catalog, args.previews, args.edited, args.n):
        prev = os.path.join(args.previews, rel)
        if not os.path.exists(prev) or not os.path.exists(path):
            continue
        ref = np.asarray(Image.open(prev).convert("RGB"), np.float32)
        info = read_exif(Path(path))
        meta = Meta(info.get("make"), info.get("model"), info.get("lens"), info.get("focal_length"),
                    info.get("aperture"), 1)  # LR previews are stored unrotated
        prm = lr_to_params(parse_lr_settings(text))
        dec = get_decoded(Path(path))
        geo = geometry_for(dec, meta, prm)
        w, h = fit_size(*geo.crop_px, ref.shape[1], ref.shape[0])
        res = render_params(dec, meta, prm, ref.shape[1], ref.shape[0], output_space="adobergb")
        ours = to_uint8(res.image).permute(1, 2, 0).cpu().numpy().astype(np.float32)
        if ours.shape != ref.shape:
            print("size mismatch", ours.shape, ref.shape, (w, h))
            continue
        de = np.linalg.norm(lab(ours) - lab(ref), axis=-1)
        dl = (lab(ours)[..., 0] - lab(ref)[..., 0]).mean()
        diff = (ours - ref).mean((0, 1))
        results.append((np.median(de), de.mean(), dl))
        print(f"{Path(path).name:32s} dE med {np.median(de):5.2f} mean {de.mean():5.2f}  dL {dl:+5.2f}  dRGB {diff.round(1)}")
        if args.save:
            os.makedirs(args.save, exist_ok=True)
            side = np.concatenate([ref, ours], 1).astype(np.uint8)
            Image.fromarray(side).resize((side.shape[1] // 3, side.shape[0] // 3)).save(
                os.path.join(args.save, Path(path).stem + ".jpg"), quality=85)
    if results:
        r = np.array(results)
        print(f"\nN={len(r)}  median dE {np.median(r[:, 0]):.2f}  mean dE {r[:, 1].mean():.2f}  mean dL {r[:, 2].mean():+.2f}")


if __name__ == "__main__":
    main()
