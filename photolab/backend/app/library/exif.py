from __future__ import annotations

from datetime import datetime
from fractions import Fraction
from pathlib import Path

import exifread

_ORIENT = {
    "Horizontal (normal)": 1, "Mirrored horizontal": 2, "Rotated 180": 3, "Mirrored vertical": 4,
    "Mirrored horizontal and rotated 270 CW": 5, "Rotated 90 CW": 6,
    "Mirrored horizontal and rotated 90 CW": 7, "Rotated 90 CCW": 8,
}


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        x = v.values[0] if hasattr(v, "values") else v
        return float(Fraction(str(x)))
    except Exception:
        return None


def read_exif(path: Path) -> dict:
    with open(path, "rb") as fh:
        tags = exifread.process_file(fh, details=False)
    out: dict = {}
    dt = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    if dt:
        try:
            out["capture_time"] = datetime.strptime(str(dt).strip(), "%Y:%m:%d %H:%M:%S")
            sub = tags.get("EXIF SubSecTimeOriginal")
            if sub and str(sub).strip().isdigit():
                ms = int(str(sub).strip()[:3].ljust(3, "0"))
                out["capture_time"] = out["capture_time"].replace(microsecond=ms * 1000)
        except ValueError:
            pass
    o = tags.get("Image Orientation")
    if o is not None:
        out["orientation"] = _ORIENT.get(str(o), 1)
    for key, tag in (("make", "Image Make"), ("model", "Image Model"), ("lens", "EXIF LensModel")):
        if tags.get(tag):
            out[key] = str(tags[tag]).strip()
    out["focal_length"] = _num(tags.get("EXIF FocalLength"))
    out["aperture"] = _num(tags.get("EXIF FNumber"))
    et = tags.get("EXIF ExposureTime")
    if et:
        out["shutter"] = str(et)
    iso = tags.get("EXIF ISOSpeedRatings") or tags.get("EXIF PhotographicSensitivity")
    if iso:
        try:
            out["iso"] = int(str(iso).split()[0].strip("[],"))
        except ValueError:
            pass
    out["exposure_bias"] = _num(tags.get("EXIF ExposureBiasValue"))
    return out
