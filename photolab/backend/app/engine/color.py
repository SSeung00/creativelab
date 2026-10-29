"""DNG colour math: white balance <-> temperature/tint and camera RGB -> linear ProPhoto (D50).

Follows the DNG specification (chapter 6) and the reference SDK's behaviour, so the
Temperature/Tint numbers mean the same thing as in Lightroom.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.engine.dcp import CameraProfile

D50_XY = (0.3457, 0.3585)
D65_XY = (0.3127, 0.3290)

# Robertson isotemperature lines: (mired, u, v, slope)
_TEMP_TABLE = [
    (0, 0.18006, 0.26352, -0.24341), (10, 0.18066, 0.26589, -0.25479),
    (20, 0.18133, 0.26846, -0.26876), (30, 0.18208, 0.27119, -0.28539),
    (40, 0.18293, 0.27407, -0.30470), (50, 0.18388, 0.27709, -0.32675),
    (60, 0.18494, 0.28021, -0.35156), (70, 0.18611, 0.28342, -0.37915),
    (80, 0.18740, 0.28668, -0.40955), (90, 0.18880, 0.28997, -0.44278),
    (100, 0.19032, 0.29326, -0.47888), (125, 0.19462, 0.30141, -0.58204),
    (150, 0.19962, 0.30921, -0.70471), (175, 0.20525, 0.31647, -0.84901),
    (200, 0.21142, 0.32312, -1.0182), (225, 0.21807, 0.32909, -1.2168),
    (250, 0.22511, 0.33439, -1.4512), (275, 0.23247, 0.33904, -1.7298),
    (300, 0.24010, 0.34308, -2.0637), (325, 0.24702, 0.34655, -2.4681),
    (350, 0.25591, 0.34951, -2.9641), (375, 0.26400, 0.35200, -3.5814),
    (400, 0.27218, 0.35407, -4.3633), (425, 0.28039, 0.35577, -5.3762),
    (450, 0.28863, 0.35714, -6.7262), (475, 0.29685, 0.35823, -8.5955),
    (500, 0.30505, 0.35907, -11.324), (525, 0.31320, 0.35968, -15.628),
    (550, 0.32129, 0.36011, -23.325), (575, 0.32931, 0.36038, -40.770),
    (600, 0.33724, 0.36051, -116.45),
]
_TINT_SCALE = -3000.0

XYZ_D50_TO_PROPHOTO = np.array([
    [1.3459433, -0.2556075, -0.0511118],
    [-0.5445989, 1.5081673, 0.0205351],
    [0.0, 0.0, 1.2118128],
])
PROPHOTO_TO_XYZ_D50 = np.linalg.inv(XYZ_D50_TO_PROPHOTO)
# XYZ(D50) -> linear output spaces (D65 ones Bradford-adapted)
XYZ_D50_TO_SRGB = np.array([
    [3.1338561, -1.6168667, -0.4906146],
    [-0.9787684, 1.9161415, 0.0334540],
    [0.0719453, -0.2289914, 1.4052427],
])
XYZ_D50_TO_ADOBERGB = np.array([
    [1.9624274, -0.6105343, -0.3413404],
    [-0.9787684, 1.9161415, 0.0334540],
    [0.0286869, -0.1406752, 1.3487655],
])
XYZ_D50_TO_DISPLAYP3 = np.array([
    [2.4041798, -0.9898708, -0.3976839],
    [-0.8422397, 1.7988475, 0.0160531],
    [0.0482595, -0.0974542, 1.2740537],
])
OUTPUT_SPACES = {
    "srgb": XYZ_D50_TO_SRGB,
    "adobergb": XYZ_D50_TO_ADOBERGB,
    "p3": XYZ_D50_TO_DISPLAYP3,
    "prophoto": XYZ_D50_TO_PROPHOTO,
}


def xy_to_xyz(x: float, y: float) -> np.ndarray:
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def xyz_to_xy(xyz: np.ndarray) -> tuple[float, float]:
    s = float(xyz.sum())
    if s <= 0:
        return D50_XY
    return float(xyz[0] / s), float(xyz[1] / s)


def xy_to_temp_tint(x: float, y: float) -> tuple[float, float]:
    u = 2.0 * x / (1.5 - x + 6.0 * y)
    v = 3.0 * y / (1.5 - x + 6.0 * y)
    last_dt = last_du = last_dv = 0.0
    for i in range(1, 31):
        r1, u1, v1, t1 = _TEMP_TABLE[i]
        du = 1.0 / math.sqrt(1.0 + t1 * t1)
        dv = t1 * du
        uu, vv = u - u1, v - v1
        dt = -uu * dv + vv * du
        if dt <= 0 or i == 30:
            if dt > 0:
                dt = 0.0
            dt = -dt
            f = 0.0 if i == 1 else dt / (last_dt + dt)
            r0, u0, v0, _ = _TEMP_TABLE[i - 1]
            temp = 1.0e6 / (r0 * f + r1 * (1.0 - f))
            uu = u - (u0 * f + u1 * (1.0 - f))
            vv = v - (v0 * f + v1 * (1.0 - f))
            du = du * (1.0 - f) + last_du * f
            dv = dv * (1.0 - f) + last_dv * f
            ln = math.hypot(du, dv)
            du, dv = du / ln, dv / ln
            tint = (uu * du + vv * dv) * _TINT_SCALE
            return temp, tint
        last_dt, last_du, last_dv = dt, du, dv
    return 6500.0, 0.0


def temp_tint_to_xy(temp: float, tint: float) -> tuple[float, float]:
    r = 1.0e6 / max(temp, 1000.0)
    offset = tint * (1.0 / _TINT_SCALE)
    u = v = 0.0
    for i in range(30):
        if r < _TEMP_TABLE[i + 1][0] or i == 29:
            r0, u0, v0, t0 = _TEMP_TABLE[i]
            r1, u1, v1, t1 = _TEMP_TABLE[i + 1]
            f = (r1 - r) / (r1 - r0)
            u = u0 * f + u1 * (1.0 - f)
            v = v0 * f + v1 * (1.0 - f)
            n1 = math.hypot(1.0, t0)
            a1, b1 = 1.0 / n1, t0 / n1
            n2 = math.hypot(1.0, t1)
            a2, b2 = 1.0 / n2, t1 / n2
            a3, b3 = a1 * f + a2 * (1.0 - f), b1 * f + b2 * (1.0 - f)
            n3 = math.hypot(a3, b3)
            u += a3 / n3 * offset
            v += b3 / n3 * offset
            break
    x = 1.5 * u / (u - 4.0 * v + 2.0)
    y = v / (u - 4.0 * v + 2.0)
    return x, y


def _cct_weight(prof: CameraProfile, x: float, y: float) -> float:
    """Weight of calibration illuminant 1 (DNG: linear in inverse temperature)."""
    temp, _ = xy_to_temp_tint(x, y)
    t1, t2 = prof.cct_1, prof.cct_2
    if t1 == t2:
        return 1.0
    lo, hi = min(t1, t2), max(t1, t2)
    if temp <= lo:
        g_lo = 1.0
    elif temp >= hi:
        g_lo = 0.0
    else:
        g_lo = (1.0 / temp - 1.0 / hi) / (1.0 / lo - 1.0 / hi)
    return g_lo if t1 <= t2 else 1.0 - g_lo


def _interp(m1, m2, g):
    if m1 is None:
        return m2
    if m2 is None:
        return m1
    return g * m1 + (1.0 - g) * m2


def color_matrix(prof: CameraProfile, x: float, y: float) -> np.ndarray:
    """XYZ -> camera matrix for a white point."""
    return _interp(prof.color_matrix_1, prof.color_matrix_2, _cct_weight(prof, x, y))


def neutral_to_xy(prof: CameraProfile, neutral: np.ndarray) -> tuple[float, float]:
    """Find the white point whose camera response is `neutral` (iterative, as in the DNG SDK)."""
    x, y = D50_XY
    for i in range(30):
        cm = color_matrix(prof, x, y)
        nx, ny = xyz_to_xy(np.linalg.solve(cm, neutral))
        if abs(nx - x) + abs(ny - y) < 1e-7:
            return nx, ny
        if i == 29:  # oscillating: settle in the middle
            return (nx + x) / 2, (ny + y) / 2
        x, y = nx, ny
    return x, y


def xy_to_neutral(prof: CameraProfile, x: float, y: float) -> np.ndarray:
    n = color_matrix(prof, x, y) @ xy_to_xyz(x, y)
    return n / n.max()


def _bradford(src_xy, dst_xy) -> np.ndarray:
    b = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    s = b @ xy_to_xyz(*src_xy)
    d = b @ xy_to_xyz(*dst_xy)
    return np.linalg.inv(b) @ np.diag(d / s) @ b


@dataclass
class ColorSetup:
    camera_to_prophoto: np.ndarray  # raw camera RGB -> linear ProPhoto, white balance included
    neutral: np.ndarray  # camera neutral (max 1)
    weight: float  # weight of illuminant-1 data (HueSatMap1 vs 2)
    temp: float
    tint: float


# Lightroom reads As Shot white balance through a per-camera calibration (like a DNG
# CameraCalibration diagonal). Measured against the user's catalog: reference neutral =
# camera neutral * factor. It only changes which Temperature/Tint a neutral is called,
# not the rendered colours (forward matrices cancel it).
NEUTRAL_CAL = {
    "ILCE-7M4": np.array([0.98699, 1.0, 1.02176]),
}


def neutral_cal(model: str | None) -> np.ndarray:
    return NEUTRAL_CAL.get((model or "").strip(), np.ones(3))


def camera_neutral_to_xy(prof: CameraProfile, cam_neutral: np.ndarray, cal: np.ndarray) -> tuple[float, float]:
    n = cam_neutral * cal
    return neutral_to_xy(prof, n / n.max())


def setup_color(prof: CameraProfile, x: float, y: float, cal: np.ndarray | None = None) -> ColorSetup:
    """Camera -> ProPhoto (D50) matrix for white point xy, per DNG spec 6.3."""
    g = _cct_weight(prof, x, y)
    neutral = xy_to_neutral(prof, x, y)
    if cal is not None:
        neutral = neutral / cal
        neutral = neutral / neutral.max()
    fm = _interp(prof.forward_matrix_1, prof.forward_matrix_2, g)
    if fm is not None:
        # Forward matrices map white-balanced camera RGB to XYZ D50; (1,1,1) must land on D50.
        d50 = xy_to_xyz(*D50_XY)
        fm = np.diag(d50 / (fm @ np.ones(3))) @ fm
        cam_to_xyz = fm @ np.diag(1.0 / neutral)
    else:
        cm = color_matrix(prof, x, y)
        cam_to_xyz = _bradford((x, y), D50_XY) @ np.linalg.inv(cm)
        cam_to_xyz = cam_to_xyz / (cam_to_xyz @ neutral)[1]
    temp, tint = xy_to_temp_tint(x, y)
    return ColorSetup(XYZ_D50_TO_PROPHOTO @ cam_to_xyz, neutral, g, temp, tint)
