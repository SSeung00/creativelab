"""Full-resolution export (JPEG / 16-bit TIFF / PNG) with resize, output sharpening and EXIF."""

from __future__ import annotations

import io
import re
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from PIL.TiffImagePlugin import IFDRational
from sqlmodel import Session

from app.engine import pipeline as P
from app.engine.ai_masks import cached_ai_masks
from app.engine.render import decoded_for, geometry_for, load_params, meta_of, render_params
from app.models import Photo

ICC_DIR = Path(r"C:\Windows\System32\spool\drivers\color")
ICC_FILES = {"srgb": "sRGB Color Space Profile.icm", "adobergb": "AdobeRGB1998.icc", "prophoto": "ProPhoto.icm"}

PRESETS = {
    "instagram": {"format": "jpeg", "quality": 92, "long_edge": 2160, "color_space": "srgb", "sharpen": "screen"},
    "phone": {"format": "jpeg", "quality": 90, "long_edge": 3000, "color_space": "srgb", "sharpen": "screen"},
    "full": {"format": "jpeg", "quality": 95, "long_edge": None, "color_space": "srgb", "sharpen": "none"},
    "print": {"format": "tiff", "quality": 100, "long_edge": None, "color_space": "adobergb", "sharpen": "matte"},
}


def _icc(space: str) -> bytes | None:
    f = ICC_DIR / ICC_FILES.get(space, "")
    try:
        return f.read_bytes() if f.is_file() else None
    except OSError:
        return None


def _rat(v, den: int = 100) -> IFDRational:
    f = Fraction(v).limit_denominator(den)
    return IFDRational(f.numerator, f.denominator)


def _exif(photo: Photo, w: int, h: int) -> bytes:
    ex = Image.Exif()
    ex[0x010F] = photo.make or ""
    ex[0x0110] = photo.model or ""
    ex[0x0131] = "PhotoLab"
    ex[0x0112] = 1
    ifd = ex.get_ifd(0x8769)
    if photo.capture_time:
        ifd[0x9003] = photo.capture_time.strftime("%Y:%m:%d %H:%M:%S")
        ifd[0x9004] = ifd[0x9003]
        ex[0x0132] = ifd[0x9003]
    if photo.lens:
        ifd[0xA434] = photo.lens
    if photo.aperture:
        ifd[0x829D] = _rat(photo.aperture)
    if photo.shutter:
        try:
            ifd[0x829A] = _rat(Fraction(photo.shutter), 100000)
        except (ValueError, ZeroDivisionError):
            pass
    if photo.iso:
        ifd[0x8827] = photo.iso
    if photo.focal_length:
        ifd[0x920A] = _rat(photo.focal_length)
    ifd[0xA002] = w
    ifd[0xA003] = h
    return ex.tobytes()


def output_sharpen(img: torch.Tensor, level: str) -> torch.Tensor:
    amount = {"screen": 0.35, "matte": 0.55, "glossy": 0.45, "none": 0.0}.get(level, 0.0)
    if not amount:
        return img
    y = (0.299 * img[0] + 0.587 * img[1] + 0.114 * img[2]).unsqueeze(0)
    d = (y - P.gaussian(y, 0.7)).squeeze(0)
    d = 0.08 * torch.tanh(d / 0.08)
    return (img + amount * 1.5 * d.unsqueeze(0)).clamp(0, 1)


def file_name(photo: Photo, pattern: str, seq: int, ext: str) -> str:
    stem = Path(photo.filename).stem
    if photo.copy_name:
        stem += "-" + re.sub(r"\s+", "", photo.copy_name)
    t = photo.capture_time or datetime.now()
    name = (pattern or "{name}").format(
        name=stem, date=t.strftime("%Y%m%d"), time=t.strftime("%H%M%S"), seq=f"{seq:04d}",
        folder=Path(photo.folder).name if photo.folder else "",
    )
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip() or stem
    return f"{name}.{ext}"


def export_photo(session: Session, photo: Photo, opts: dict, dest_dir: Path, seq: int) -> Path:
    dec = decoded_for(session, photo)
    meta = meta_of(photo)
    prm = load_params(session, photo)
    geo = geometry_for(dec, meta, prm)
    cw, ch = geo.crop_px
    space = opts.get("color_space", "srgb")
    res = render_params(dec, meta, prm, cw, ch, output_space=space, ai_masks=cached_ai_masks(photo, prm))
    img = res.image
    le = opts.get("long_edge")
    if le and max(cw, ch) > le:
        s = le / max(cw, ch)
        w, h = max(1, round(cw * s)), max(1, round(ch * s))
        with torch.no_grad():
            img = F.interpolate(img.unsqueeze(0), size=(h, w), mode="bilinear", antialias=True,
                                align_corners=False).squeeze(0).clamp(0, 1)
    with torch.no_grad():
        img = output_sharpen(img, opts.get("sharpen", "none"))
    fmt = opts.get("format", "jpeg")
    ext = {"jpeg": "jpg", "tiff": "tif", "png": "png"}[fmt]
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / file_name(photo, opts.get("naming", "{name}"), seq, ext)
    n = 2
    while out.exists() and not opts.get("overwrite"):
        out = out.with_name(f"{out.stem}-{n}{out.suffix}")
        n += 1
    h, w = img.shape[1:]
    icc = _icc(space)
    exif = _exif(photo, w, h)
    if fmt == "tiff":
        import tifffile

        a16 = (img.permute(1, 2, 0).cpu().numpy() * 65535 + 0.5).clip(0, 65535).astype(np.uint16)
        extratags = [(34675, "B", len(icc), icc, True)] if icc else []
        tifffile.imwrite(out, a16, photometric="rgb", compression="zlib", extratags=extratags,
                         metadata=None, software="PhotoLab")
    else:
        a8 = (img.permute(1, 2, 0).cpu().numpy() * 255 + 0.5).clip(0, 255).astype(np.uint8)
        im = Image.fromarray(a8)
        kw = {"icc_profile": icc} if icc else {}
        if fmt == "jpeg":
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=int(opts.get("quality", 92)), subsampling=0 if opts.get("quality", 92) >= 90 else 2,
                    optimize=True, exif=exif, **kw)
            out.write_bytes(buf.getvalue())
        else:
            im.save(out, "PNG", exif=exif, **kw)
    return out
