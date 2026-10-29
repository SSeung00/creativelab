"""Reader for DNG Camera Profiles (.dcp) — the colour profiles Lightroom/ACR use.

A .dcp is a little-endian TIFF ("IIRC" magic) holding the DNG profile tags: colour/forward
matrices for two calibration illuminants, an optional HSV hue/sat map, an optional look table
and an optional tone curve. See the DNG 1.6 specification, chapter 6 ("Mapping Camera Color
Space to CIE XYZ Space").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

TAG_UNIQUE_CAMERA_MODEL = 50708
TAG_COLOR_MATRIX_1 = 50721
TAG_COLOR_MATRIX_2 = 50722
TAG_CALIB_ILLUMINANT_1 = 50778
TAG_CALIB_ILLUMINANT_2 = 50779
TAG_PROFILE_NAME = 50936
TAG_HSM_DIMS = 50937
TAG_HSM_DATA_1 = 50938
TAG_HSM_DATA_2 = 50939
TAG_TONE_CURVE = 50940
TAG_EMBED_POLICY = 50941
TAG_COPYRIGHT = 50942
TAG_FORWARD_MATRIX_1 = 50964
TAG_FORWARD_MATRIX_2 = 50965
TAG_LOOK_DIMS = 50981
TAG_LOOK_DATA = 50982
TAG_HSM_ENCODING = 51107
TAG_LOOK_ENCODING = 51108
TAG_BASELINE_EXPOSURE_OFFSET = 51109
TAG_DEFAULT_BLACK_RENDER = 51110

# TIFF type -> (struct code, size)
_TYPES = {
    1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 7: ("B", 1),
    8: ("h", 2), 9: ("i", 4), 10: ("ii", 8), 11: ("f", 4), 12: ("d", 8),
}

# EXIF LightSource codes -> correlated colour temperature (K)
ILLUMINANT_CCT = {
    1: 5500.0, 2: 4200.0, 3: 2850.0, 4: 5500.0, 9: 5500.0, 10: 6500.0, 11: 7500.0,
    12: 6400.0, 13: 5000.0, 14: 4150.0, 15: 3450.0, 17: 2856.0, 18: 4874.0, 19: 6774.0,
    20: 5503.0, 21: 6504.0, 22: 7504.0, 23: 5003.0, 24: 3200.0,
}


@dataclass
class HSVMap:
    hue_divs: int
    sat_divs: int
    val_divs: int
    data: np.ndarray  # (val, hue, sat, 3) -> (hue shift deg, sat scale, val scale)
    srgb_encoded_vals: bool = False


@dataclass
class CameraProfile:
    name: str
    camera_model: str
    color_matrix_1: np.ndarray | None = None
    color_matrix_2: np.ndarray | None = None
    forward_matrix_1: np.ndarray | None = None
    forward_matrix_2: np.ndarray | None = None
    illuminant_1: int = 17
    illuminant_2: int = 21
    hue_sat_map_1: HSVMap | None = None
    hue_sat_map_2: HSVMap | None = None
    look_table: HSVMap | None = None
    tone_curve: np.ndarray | None = None  # (N, 2) points in [0,1]
    baseline_exposure_offset: float = 0.0
    default_black_render: int = 0  # 0 = auto, 1 = none
    path: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def cct_1(self) -> float:
        return ILLUMINANT_CCT.get(self.illuminant_1, 2856.0)

    @property
    def cct_2(self) -> float:
        return ILLUMINANT_CCT.get(self.illuminant_2, 6504.0)


def _read_tags(buf: bytes) -> dict[int, object]:
    bo = buf[:2]
    if bo == b"II":
        end = "<"
    elif bo == b"MM":
        end = ">"
    else:
        raise ValueError("not a TIFF/DCP file")
    (magic,) = struct.unpack(end + "H", buf[2:4])
    if magic not in (42, 0x4352, 0x4F52, 0x5352):  # TIFF, DCP 'RC', 'RO', 'RS'
        raise ValueError(f"unexpected magic {magic:#x}")
    (ifd,) = struct.unpack(end + "I", buf[4:8])
    (count,) = struct.unpack(end + "H", buf[ifd:ifd + 2])
    tags: dict[int, object] = {}
    for i in range(count):
        off = ifd + 2 + i * 12
        tag, typ, n = struct.unpack(end + "HHI", buf[off:off + 8])
        if typ not in _TYPES:
            continue
        code, size = _TYPES[typ]
        total = size * n
        data_off = off + 8 if total <= 4 else struct.unpack(end + "I", buf[off + 8:off + 12])[0]
        raw = buf[data_off:data_off + total]
        if typ == 2:
            tags[tag] = raw.rstrip(b"\0").decode("latin-1")
        elif typ in (5, 10):
            vals = struct.unpack(end + code[0] * (2 * n), raw)
            tags[tag] = [vals[2 * k] / vals[2 * k + 1] if vals[2 * k + 1] else 0.0 for k in range(n)]
        elif typ in (1, 7) and tag not in (TAG_EMBED_POLICY,):
            tags[tag] = raw
        else:
            tags[tag] = list(struct.unpack(end + code * n, raw))
    return tags


def _mat(v) -> np.ndarray | None:
    return None if v is None else np.array(v, dtype=np.float64).reshape(3, 3)


def _hsv(dims, data, encoding) -> HSVMap | None:
    if dims is None or data is None:
        return None
    hue, sat, val = (int(d) for d in dims)
    val = max(val, 1)
    arr = np.array(data, dtype=np.float32).reshape(val, hue, sat, 3)
    return HSVMap(hue, sat, val, arr, srgb_encoded_vals=bool(encoding and encoding[0] == 1))


def load_dcp(path: str | Path) -> CameraProfile:
    buf = Path(path).read_bytes()
    t = _read_tags(buf)
    prof = CameraProfile(
        name=str(t.get(TAG_PROFILE_NAME, Path(path).stem)),
        camera_model=str(t.get(TAG_UNIQUE_CAMERA_MODEL, "")),
        color_matrix_1=_mat(t.get(TAG_COLOR_MATRIX_1)),
        color_matrix_2=_mat(t.get(TAG_COLOR_MATRIX_2)),
        forward_matrix_1=_mat(t.get(TAG_FORWARD_MATRIX_1)),
        forward_matrix_2=_mat(t.get(TAG_FORWARD_MATRIX_2)),
        illuminant_1=int((t.get(TAG_CALIB_ILLUMINANT_1) or [17])[0]),
        illuminant_2=int((t.get(TAG_CALIB_ILLUMINANT_2) or [21])[0]),
        path=str(path),
    )
    enc = t.get(TAG_HSM_ENCODING)
    prof.hue_sat_map_1 = _hsv(t.get(TAG_HSM_DIMS), t.get(TAG_HSM_DATA_1), enc)
    prof.hue_sat_map_2 = _hsv(t.get(TAG_HSM_DIMS), t.get(TAG_HSM_DATA_2), enc)
    prof.look_table = _hsv(t.get(TAG_LOOK_DIMS), t.get(TAG_LOOK_DATA), t.get(TAG_LOOK_ENCODING))
    tc = t.get(TAG_TONE_CURVE)
    if tc:
        prof.tone_curve = np.array(tc, dtype=np.float32).reshape(-1, 2)
    beo = t.get(TAG_BASELINE_EXPOSURE_OFFSET)
    if beo:
        prof.baseline_exposure_offset = float(beo[0])
    dbr = t.get(TAG_DEFAULT_BLACK_RENDER)
    if dbr:
        prof.default_black_render = int(dbr[0])
    return prof
