"""Render a photo with its edit parameters on the GPU (preview, zoomed region or full export)."""

from __future__ import annotations

import functools
import hashlib
import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from sqlmodel import Session

from app.config import DATA_DIR
from app.engine import adobe, color
from app.engine import pipeline as P
from app.engine.decode import DEVICE, Decoded, get_decoded
from app.engine.masks import mask_weights
from app.engine.params import full as full_params

_gpu_lock = threading.Lock()

# Strength constants of the tone operators; refined by calibration against Lightroom renders.
TUNE_FILE = DATA_DIR / "tune.json"
TUNE_DEFAULT = {
    # Fitted on 30 untouched a7 IV frames vs Lightroom's rendered previews (rms 0.05 stops).
    "baseline_ev": -0.97,
    "black_offset": 0.00194,
    # Tone operators: start points from a per-tone-band comparison with Lightroom-edited photos
    # (shadows/highlights act mostly below/above mid grey); refined by tools/tune.py.
    "shadows": 1.0,
    "sh_hi": 0.0,
    "sh_lo": -4.0,
    "highlights": 1.1,
    "hl_lo": 0.3,
    "hl_hi": 2.8,
    "contrast": 0.35,
    "whites": 0.9,
    "blacks": 1.6,
    "clarity": 0.45,
    "texture": 0.9,
    "dehaze": 0.9,
}


@functools.cache
def tune() -> dict:
    t = dict(TUNE_DEFAULT)
    if TUNE_FILE.exists():
        try:
            t.update(json.loads(TUNE_FILE.read_text()))
        except Exception:
            pass
    return t


# ----------------------------------------------------------------------------- photo meta

@dataclass
class Meta:
    make: str | None
    model: str | None
    lens: str | None
    focal: float | None
    aperture: float | None
    orientation: int


def meta_of(photo) -> Meta:
    return Meta(photo.make, photo.model, photo.lens, photo.focal_length, photo.aperture, photo.orientation or 1)


# ----------------------------------------------------------------------------- profile

@dataclass
class ProfileSetup:
    dcp: object | None  # CameraProfile
    look: adobe.Look | None
    name: str


def resolve_profile(meta: Meta, name: str) -> ProfileSetup:
    profs = adobe.camera_profiles(meta.make, meta.model)
    if name in profs:
        return ProfileSetup(adobe.load_profile(profs[name]), None, name)
    look = adobe.find_look(name)
    if look is not None:
        base = profs.get(look.base_profile) or profs.get("Adobe Standard")
        return ProfileSetup(adobe.load_profile(base) if base else None, look, name)
    base = profs.get("Adobe Standard")
    return ProfileSetup(adobe.load_profile(base) if base else None, None, "Adobe Standard" if base else name)


def available_profiles(meta: Meta) -> list[dict]:
    out = []
    profs = adobe.camera_profiles(meta.make, meta.model)
    if profs:
        for n, g in adobe.list_looks():
            if g in ("Profiles", "Adobe Raw", "B&W") or (n.startswith("Adobe") and g != "Adaptive"):
                out.append({"name": n, "group": "Adobe Raw" if g in ("Profiles", "Adobe Raw") else g})
        for n in sorted(profs):
            out.append({"name": n, "group": "Adobe Raw" if n == "Adobe Standard" else "카메라 매칭"})
    return out


# ----------------------------------------------------------------------------- colour setup

@dataclass
class ColorPlan:
    cam_to_prophoto: torch.Tensor
    neutral: np.ndarray
    temp: float
    tint: float
    as_shot_temp: float
    as_shot_tint: float
    hsm: P.HSVTable | None
    look_dcp: P.HSVTable | None
    look_xmp: P.HSVTable | None
    tone_lut: torch.Tensor | None  # profile tone curve (linear domain)
    look_curve: torch.Tensor | None  # look's ToneCurvePV2012 (gamma domain)
    look_curve_rgb: list[torch.Tensor | None]
    baseline_ev: float
    monochrome: bool


def _auto_wb_neutral(dec: Decoded) -> np.ndarray:
    """Grey-world on mid tones of the preview (robust-ish 'Auto')."""
    x = dec.preview
    y = x.mean(0)
    m = (y > y.quantile(0.2)) & (y < y.quantile(0.98)) & (x.max(0).values < 0.95)
    means = torch.stack([x[c][m].mean() for c in range(3)]).double().cpu().numpy()
    n = means / means.max()
    return n


def color_plan(dec: Decoded, meta: Meta, prm: dict, prof: ProfileSetup) -> ColorPlan:
    dcp = prof.dcp
    if dcp is None or not dec.is_raw:
        m = torch.tensor(color.XYZ_D50_TO_PROPHOTO @ np.linalg.inv(color.XYZ_D50_TO_SRGB), dtype=torch.float32, device=DEVICE)
        return ColorPlan(m, np.ones(3), 6500, 0, 6500, 0, None, None, None, None, None, [None] * 3, 0.0, False)
    cal = color.neutral_cal(meta.model)
    as_x, as_y = color.camera_neutral_to_xy(dcp, dec.cam_neutral, cal)
    as_t, as_ti = color.xy_to_temp_tint(as_x, as_y)
    if prm["wb"] == "custom" and prm.get("temp"):
        x, y = color.temp_tint_to_xy(float(prm["temp"]), float(prm.get("tint") or 0))
    elif prm["wb"] == "auto":
        x, y = color.camera_neutral_to_xy(dcp, _auto_wb_neutral(dec), cal)
    else:
        x, y = as_x, as_y
    cs = color.setup_color(dcp, x, y, cal)
    hsm = None
    if dcp.hue_sat_map_1 is not None:
        hsm = P.hsv_table(dcp.hue_sat_map_1, dcp.hue_sat_map_2, cs.weight, DEVICE)
    look_dcp = P.hsv_table(dcp.look_table, None, 1.0, DEVICE) if dcp.look_table is not None else None
    look_xmp = None
    look_curve = None
    look_rgb: list[torch.Tensor | None] = [None, None, None]
    if prof.look is not None:
        if prof.look.look_table is not None:
            look_xmp = P.hsv_table(prof.look.look_table, None, 1.0, DEVICE)
        if prof.look.tone_curve and len(prof.look.tone_curve) > 2:
            look_curve = torch.tensor(P.spline_lut(prof.look.tone_curve), device=DEVICE)
        for i, ch in enumerate(("red", "green", "blue")):
            pts = prof.look.tone_curve_rgb.get(ch)
            if pts and len(pts) > 2:
                look_rgb[i] = torch.tensor(P.spline_lut(pts), device=DEVICE)
    if dcp.tone_curve is not None:
        tc = dcp.tone_curve
        grid = np.linspace(0, 1, 1024)
        tone = np.interp(grid, tc[:, 0], tc[:, 1]).astype(np.float32)
    else:
        tone = adobe.acr3_curve()
    return ColorPlan(
        cam_to_prophoto=torch.tensor(cs.camera_to_prophoto, dtype=torch.float32, device=DEVICE),
        neutral=cs.neutral,
        temp=cs.temp, tint=cs.tint, as_shot_temp=as_t, as_shot_tint=as_ti,
        hsm=hsm, look_dcp=look_dcp, look_xmp=look_xmp,
        tone_lut=torch.tensor(tone, device=DEVICE),
        look_curve=look_curve, look_curve_rgb=look_rgb,
        baseline_ev=dcp.baseline_exposure_offset,
        monochrome=bool(prof.look and prof.look.monochrome) or "BW" in prof.name.split(),
    )


# ----------------------------------------------------------------------------- geometry

_ORIENT = {
    1: ((1, 0), (0, 1)), 2: ((-1, 0), (0, 1)), 3: ((-1, 0), (0, -1)), 4: ((1, 0), (0, -1)),
    5: ((0, 1), (1, 0)), 6: ((0, 1), (-1, 0)), 7: ((0, -1), (-1, 0)), 8: ((0, -1), (1, 0)),
}


@dataclass
class Geometry:
    A: np.ndarray  # oriented(centered px) -> sensor(centered px)
    W: int  # sensor dims
    H: int
    OW: int  # oriented dims
    OH: int
    crop: dict
    angle: float
    lens_k: tuple | None
    lens_zoom: float
    vig: tuple | None
    vig_amount: float
    fx: float  # normalised focal length (fraction of max dim)
    transform: dict

    @property
    def crop_px(self) -> tuple[int, int]:
        c = self.crop
        return (max(1, round((c["right"] - c["left"]) * self.OW)), max(1, round((c["bottom"] - c["top"]) * self.OH)))


def orientation_matrix(exif: int, rot90: int, flip: bool) -> np.ndarray:
    a = np.array(_ORIENT.get(exif or 1, _ORIENT[1]), dtype=np.float64)
    r = np.array(_ORIENT[6], dtype=np.float64)
    for _ in range(rot90 % 4):
        a = a @ r
    if flip:
        a = a @ np.array(_ORIENT[2], dtype=np.float64)
    return a


def _distort(pts: np.ndarray, k, fxD: float) -> np.ndarray:
    r2 = (pts ** 2).sum(-1, keepdims=True) / (fxD * fxD)
    return pts * (1 + k[0] * r2 + k[1] * r2 ** 2 + k[2] * r2 ** 3)


def geometry_for(dec: Decoded, meta: Meta, prm: dict) -> Geometry:
    A = orientation_matrix(meta.orientation, prm.get("rotate90", 0), prm.get("flip", False))
    W, H = dec.width, dec.height
    swap = abs(A[0, 1]) > 0.5
    OW, OH = (H, W) if swap else (W, H)
    lens = prm["lens"]
    k = None
    zoom = 1.0
    vig = None
    fx = 1.0
    if lens.get("profile") and dec.is_raw:
        lp = adobe.find_lens_profile(meta.lens, meta.make)
        if lp is not None:
            kk, _scale, vig = adobe.lens_params(lp, meta.focal, meta.aperture)
            fx = (meta.focal or 50) / 36.0
            amt = lens.get("distortion", 100) / 100.0
            k = tuple(float(v) * amt for v in kk)
    md = lens.get("manual_distortion", 0)
    if md:
        k = tuple(k or (0.0, 0.0, 0.0))
        k = (k[0] - md / 100.0 * 0.15, k[1], k[2])
    if k is not None:
        D = max(W, H)
        fxD = fx * D
        # Largest zoom such that the whole output frame samples inside the sensor.
        n = 64
        t = np.linspace(-0.5, 0.5, n)
        border = np.concatenate([
            np.stack([t * W, np.full(n, -H / 2)], -1), np.stack([t * W, np.full(n, H / 2)], -1),
            np.stack([np.full(n, -W / 2), t * H], -1), np.stack([np.full(n, W / 2), t * H], -1),
        ])
        lo, hi = 0.6, 1.4
        for _ in range(40):
            z = (lo + hi) / 2
            d = _distort(border * z, k, fxD)
            ok = (np.abs(d[:, 0]) <= W / 2 + 0.5).all() and (np.abs(d[:, 1]) <= H / 2 + 0.5).all()
            lo, hi = (z, hi) if ok else (lo, z)
        zoom = lo
    return Geometry(
        A=A, W=W, H=H, OW=OW, OH=OH, crop=prm["crop"], angle=float(prm["crop"].get("angle", 0) or 0),
        lens_k=k, lens_zoom=zoom, vig=vig, vig_amount=lens.get("vignetting", 100) / 100.0 if lens.get("profile") else 0.0,
        fx=fx, transform=prm["transform"],
    )


def sample_grid(geo: Geometry, out_w: int, out_h: int, region=(0.0, 0.0, 1.0, 1.0)):
    """Grid (1,h,w,2) into the sensor image, vignette gain (h,w), and crop-normalised coords."""
    x0, y0, x1, y1 = region
    dev = DEVICE
    u = x0 + (torch.arange(out_w, device=dev, dtype=torch.float32) + 0.5) / out_w * (x1 - x0)
    v = y0 + (torch.arange(out_h, device=dev, dtype=torch.float32) + 0.5) / out_h * (y1 - y0)
    vv, uu = torch.meshgrid(v, u, indexing="ij")
    c = geo.crop
    X = (c["left"] + uu * (c["right"] - c["left"]) - 0.5) * geo.OW
    Y = (c["top"] + vv * (c["bottom"] - c["top"]) - 0.5) * geo.OH
    # straighten
    if geo.angle:
        a = math.radians(geo.angle)
        ca, sa = math.cos(a), math.sin(a)
        X, Y = ca * X - sa * Y, sa * X + ca * Y
    # manual transform: rotate / scale / offset / keystone
    tr = geo.transform
    if tr.get("rotate"):
        a = math.radians(tr["rotate"])
        ca, sa = math.cos(a), math.sin(a)
        X, Y = ca * X - sa * Y, sa * X + ca * Y
    sc = (tr.get("scale", 100) or 100) / 100.0
    if sc != 1:
        X, Y = X / sc, Y / sc
    if tr.get("x") or tr.get("y"):
        X = X - tr.get("x", 0) / 100.0 * geo.OW * 0.5
        Y = Y - tr.get("y", 0) / 100.0 * geo.OH * 0.5
    kv = (tr.get("vertical", 0) or 0) / 100.0 * 0.6
    kh = (tr.get("horizontal", 0) or 0) / 100.0 * 0.6
    if kv or kh:
        # simple keystone homography around the centre
        den = 1 + kv * (Y / geo.OH) + kh * (X / geo.OW)
        X, Y = X / den, Y / den
        if tr.get("aspect"):
            asp = 1 + tr["aspect"] / 100.0 * 0.5
            X = X / asp
    A = geo.A
    SX = A[0, 0] * X + A[0, 1] * Y
    SY = A[1, 0] * X + A[1, 1] * Y
    vig_gain = None
    D = max(geo.W, geo.H)
    if geo.lens_k is not None:
        z = geo.lens_zoom
        SX, SY = SX * z, SY * z
        fxD = geo.fx * D
        r2 = (SX * SX + SY * SY) / (fxD * fxD)
        k1, k2, k3 = geo.lens_k
        f = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
        SX, SY = SX * f, SY * f
    if geo.vig is not None and geo.vig_amount:
        fxD = geo.fx * D
        r2 = (SX * SX + SY * SY) / (fxD * fxD)
        a1, a2, a3 = geo.vig
        vgain = 1 + a1 * r2 + a2 * r2 * r2 + a3 * r2 * r2 * r2
        vig_gain = vgain.clamp(min=0.05).pow(-geo.vig_amount)
    grid = torch.stack([SX / (geo.W / 2), SY / (geo.H / 2)], -1).unsqueeze(0)
    return grid, vig_gain, uu, vv


def _sample(img: torch.Tensor, grid: torch.Tensor) -> torch.Tensor:
    return F.grid_sample(img.unsqueeze(0).float(), grid, mode="bilinear", padding_mode="zeros",
                         align_corners=False).squeeze(0)


# ----------------------------------------------------------------------------- analysis (local operators)

@dataclass
class Analysis:
    base_a: torch.Tensor  # guided coefficients over the full crop (low res), for shadows/highlights
    base_b: torch.Tensor
    wide_a: torch.Tensor  # for clarity
    wide_b: torch.Tensor
    dark: torch.Tensor  # dehaze transmission estimate (low res, crop space)
    airlight: torch.Tensor  # (3,)
    hist_ref: float


_analysis_cache: dict[str, Analysis] = {}


def _analysis_key(dec: Decoded, prm: dict) -> str:
    keep = {k: prm[k] for k in ("profile", "wb", "temp", "tint", "lens", "transform", "crop", "rotate90", "flip")}
    return dec.key + "|" + hashlib.md5(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


def _scene_linear(img_cam: torch.Tensor, cp: ColorPlan, vig_gain) -> torch.Tensor:
    if vig_gain is not None:
        img_cam = img_cam * vig_gain.unsqueeze(0)
    # Clipped photosites: pull toward neutral so blown highlights don't turn magenta.
    clip = P.smoothstep(0.92, 0.995, img_cam.max(0).values)
    wb = img_cam / torch.tensor(cp.neutral, dtype=torch.float32, device=img_cam.device).view(3, 1, 1)
    if bool((clip > 0).any()):
        neutral_v = wb.max(0).values.unsqueeze(0).expand(3, -1, -1)
        wb = wb * (1 - clip) + neutral_v * clip
    m = cp.cam_to_prophoto @ torch.diag(torch.tensor(cp.neutral, dtype=torch.float32, device=img_cam.device))
    rgb = P.mat3(wb, m)
    if cp.hsm is not None:
        rgb = P.apply_hsv_table(rgb, cp.hsm)
    return rgb


def analyse(dec: Decoded, geo: Geometry, cp: ColorPlan, prm: dict) -> Analysis:
    key = _analysis_key(dec, prm)
    hit = _analysis_cache.get(key)
    if hit is not None:
        return hit
    cw, ch = geo.crop_px
    s = 640 / max(cw, ch)
    w, h = max(8, round(cw * s)), max(8, round(ch * s))
    grid, vig, _, _ = sample_grid(geo, w, h)
    rgb = _scene_linear(_sample(dec.preview, grid), cp, vig)
    rgb = rgb * (2 ** (tune()["baseline_ev"] + cp.baseline_ev))
    L = torch.log2(P.luma(rgb).clamp(min=1e-5) / P.MID_GREY)
    r = max(2, round(max(w, h) * 0.035))
    ba, bb = P.guided_coeffs(L, r, 0.25)
    wa, wb_ = P.guided_coeffs(L, max(2, round(max(w, h) * 0.06)), 0.5)
    # dehaze: dark channel prior on the low-res frame
    lin = rgb.clamp(min=0)
    y = P.luma(lin)
    flat = y.flatten()
    thr = flat.quantile(0.995) if flat.numel() > 10 else flat.max()
    sel = y >= thr
    A = torch.stack([lin[c][sel].mean() for c in range(3)]).clamp(min=1e-3)
    dark = (lin / A.view(3, 1, 1)).min(0).values
    rr = max(1, round(max(w, h) * 0.012))
    dark = -F.max_pool2d(-dark.unsqueeze(0).unsqueeze(0), 2 * rr + 1, stride=1, padding=rr).squeeze()
    da, db = P.guided_coeffs(dark, rr * 2, 0.01)
    dark = (da * dark + db).clamp(0, 1)
    an = Analysis(ba, bb, wa, wb_, P.box(dark, rr), A, float(y.mean()))
    if len(_analysis_cache) > 32:
        _analysis_cache.clear()
    _analysis_cache[key] = an
    return an


def _sample_map(m: torch.Tensor, uu: torch.Tensor, vv: torch.Tensor) -> torch.Tensor:
    """Sample a crop-space map (H,W) at crop-normalised coords."""
    grid = torch.stack([uu * 2 - 1, vv * 2 - 1], -1).unsqueeze(0)
    return F.grid_sample(m.unsqueeze(0).unsqueeze(0), grid, mode="bilinear", padding_mode="border",
                         align_corners=False).squeeze(0).squeeze(0)


# ----------------------------------------------------------------------------- main render

@dataclass
class RenderResult:
    image: torch.Tensor  # (3,h,w) encoded output space, 0..1
    as_shot_temp: float
    as_shot_tint: float
    temp: float
    tint: float
    crop_px: tuple[int, int]
    histogram: list[list[int]] | None = None
    clipped: tuple[float, float] | None = None
    missing_denoise: bool = False


_dn_preview: dict[int, torch.Tensor] = {}


def _denoised_base(dec: Decoded, full: bool) -> torch.Tensor | None:
    from app.ai.denoise import load_denoised
    from app.engine.decode import _to_preview

    pid = int(dec.key[1:])
    dn = load_denoised(pid)
    if dn is None:
        return None
    if full:
        return dn.float()
    pv = _dn_preview.get(pid)
    if pv is None:
        pv = _to_preview(dn)
        if len(_dn_preview) > 6:
            _dn_preview.pop(next(iter(_dn_preview)))
        _dn_preview[pid] = pv
    return pv


OUTPUT_GAMMA = {"srgb": "srgb", "adobergb": 2.19921875, "p3": "srgb", "prophoto": 1.8}


def render_params(dec: Decoded, meta: Meta, prm: dict, out_w: int, out_h: int,
                  region=(0.0, 0.0, 1.0, 1.0), output_space: str = "srgb",
                  ai_masks: dict | None = None, want_hist: bool = False) -> RenderResult:
    prm = full_params(prm)
    with _gpu_lock, torch.no_grad():
        return _render(dec, meta, prm, out_w, out_h, region, output_space, ai_masks or {}, want_hist)


def _render(dec, meta, prm, out_w, out_h, region, output_space, ai_masks, want_hist) -> RenderResult:
    tn = tune()
    prof = resolve_profile(meta, prm["profile"])
    cp = color_plan(dec, meta, prm, prof)
    geo = geometry_for(dec, meta, prm)
    cw, ch = geo.crop_px
    # output pixels per full-resolution pixel (for radius-based operators)
    px_scale = out_w / max(1e-6, (region[2] - region[0]) * cw)
    use_full = px_scale > 0.92 * (dec.preview.shape[2] / dec.width)
    src = dec.full if use_full else dec.preview
    missing_denoise = False
    if prm["ai_denoise"].get("enabled") and dec.is_raw and dec.key.startswith("p"):
        dn = _denoised_base(dec, use_full)
        if dn is None:
            missing_denoise = True
        else:
            b = min(1.0, prm["ai_denoise"].get("amount", 50) / 70.0)
            src = src.float() * (1 - b) + dn * b
    an = analyse(dec, geo, cp, prm)

    grid, vig, uu, vv = sample_grid(geo, out_w, out_h, region)
    rgb = _scene_linear(_sample(src, grid), cp, vig)

    # ---- masks -> per-pixel local adjustments
    masks = [m for m in prm.get("masks", []) if m.get("visible", True)]
    local = {}
    if masks:
        local = mask_weights(masks, uu, vv, grid, ai_masks)

    def lp(name, glob):
        add = local.get(name)
        return glob if add is None else glob + add

    # ---- dehaze (scene-referred)
    dehaze = lp("dehaze", prm["dehaze"] / 100.0)
    if isinstance(dehaze, torch.Tensor) or dehaze:
        dark = _sample_map(an.dark, uu, vv)
        A = an.airlight.view(3, 1, 1)
        amt = dehaze * tn["dehaze"]
        if isinstance(amt, torch.Tensor):
            amt = amt.unsqueeze(0)
        pos = amt.clamp(min=0) if isinstance(amt, torch.Tensor) else max(amt, 0.0)
        neg = (-amt).clamp(min=0) if isinstance(amt, torch.Tensor) else max(-amt, 0.0)
        t = (1 - 0.95 * pos * dark.unsqueeze(0)).clamp(min=0.12)
        rgb = (rgb - A * (1 - t)) / t
        # Lightroom's dehaze also deepens colour noticeably (skies especially)
        if isinstance(pos, torch.Tensor) or pos:
            y = P.luma(rgb).unsqueeze(0)
            rgb = y + (rgb - y) * (1 + 0.5 * pos)
        rgb = rgb * (1 - neg * 0.6) + A * neg * 0.6 * (1 - dark.unsqueeze(0) * 0.5)
        rgb = rgb.clamp(min=0)

    # ---- exposure & tone
    ev = tn["baseline_ev"] + cp.baseline_ev
    rgb = rgb * (2 ** ev)
    Y = P.luma(rgb).clamp(min=1e-6)
    L = torch.log2(Y / P.MID_GREY)
    tp = P.ToneParams(
        exposure=lp("exposure", float(prm["exposure"])),
        contrast=lp("contrast", prm["contrast"] / 100.0),
        highlights=lp("highlights", prm["highlights"] / 100.0),
        shadows=lp("shadows", prm["shadows"] / 100.0),
        whites=lp("whites", prm["whites"] / 100.0),
        blacks=lp("blacks", prm["blacks"] / 100.0),
        clarity=lp("clarity", prm["clarity"] / 100.0),
        texture=lp("texture", prm["texture"] / 100.0),
    )
    need_base = any(_nz(getattr(tp, n)) for n in ("highlights", "shadows"))
    base = _sample_map(an.base_a, uu, vv) * L + _sample_map(an.base_b, uu, vv) if need_base else None
    base_wide = (_sample_map(an.wide_a, uu, vv) * L + _sample_map(an.wide_b, uu, vv)) if _nz(tp.clarity) else None
    detail_fine = None
    if _nz(tp.texture):
        sig = max(0.8, 6.0 * px_scale)
        detail_fine = L - P.gaussian(L.unsqueeze(0), sig).squeeze(0)
    gain = P.tone_gain(L, base, base_wide, detail_fine, tp, tn)
    rgb = P.apply_gain(rgb, gain)

    # local white balance (temp/tint) as gentle channel gains
    if "temp" in local or "tint" in local:
        t = local.get("temp", 0) * 0.35
        g = local.get("tint", 0) * 0.25
        rgb = rgb * torch.exp2(torch.stack([
            torch.as_tensor(t + g * 0.5, device=rgb.device).expand_as(rgb[0]),
            torch.as_tensor(-g, device=rgb.device).expand_as(rgb[0]),
            torch.as_tensor(-t + g * 0.5, device=rgb.device).expand_as(rgb[0]),
        ]))

    # ACR's default black render: a small black-level subtraction before the tone curve.
    k = tn.get("black_offset", 0.0)
    if k:
        rgb = ((rgb - k) / (1 - k)).clamp(min=0)

    # ---- profile look + tone curve (output-referred from here)
    amount = prm.get("profile_amount", 100) / 100.0
    if cp.look_dcp is not None:
        rgb = P.apply_hsv_table(rgb, cp.look_dcp, amount)
    if cp.look_xmp is not None:
        rgb = P.apply_hsv_table(rgb, cp.look_xmp, amount)
    rgb = P.rgb_tone(rgb, cp.tone_lut)

    # ---- point curves in gamma space (ProPhoto primaries, sRGB transfer, like Lightroom's curve)
    g_rgb = P.srgb_encode(rgb.clamp(0, 1))
    if cp.look_curve is not None:
        g_rgb = P.lut1d(g_rgb, cp.look_curve)
    for i, lut in enumerate(cp.look_curve_rgb):
        if lut is not None:
            g_rgb[i] = P.lut1d(g_rgb[i], lut)
    par = P.parametric_lut(prm["parametric"])
    if par is not None:
        g_rgb = P.lut1d(g_rgb, torch.tensor(par, device=rgb.device))
    if _curve_active(prm["curve"]):
        g_rgb = P.lut1d(g_rgb, _lut_tensor(json.dumps(prm["curve"])))
    for i, k in enumerate(("curve_r", "curve_g", "curve_b")):
        if _curve_active(prm[k]):
            g_rgb[i] = P.lut1d(g_rgb[i], _lut_tensor(json.dumps(prm[k])))

    # ---- colour
    if prm["bw"] or cp.monochrome:
        g_rgb = P.apply_bw(g_rgb, prm["bw_mix"])
    else:
        g_rgb = P.apply_hsl(g_rgb, prm["hsl"])
        vib = lp("vibrance", prm["vibrance"] / 100.0 * tn.get("vibrance_k", 1.0))
        sat = lp("saturation", prm["saturation"] / 100.0 * tn.get("saturation_k", 1.0))
        if _nz(vib) or _nz(sat):
            g_rgb = P.apply_vibrance_saturation(g_rgb, vib, sat)
        if "hue" in local:
            h, s, v = P.rgb_to_hsv(g_rgb.clamp(0, 1))
            g_rgb = P.hsv_to_rgb(h + local["hue"] / 6.0, s, v)
    g_rgb = P.apply_grading(g_rgb, prm["grading"])

    # ---- to output space
    lin = P.srgb_decode(g_rgb.clamp(0, 1.0))
    xyz = P.mat3(lin, color.PROPHOTO_TO_XYZ_D50)
    out_lin = P.mat3(xyz, color.OUTPUT_SPACES.get(output_space, color.XYZ_D50_TO_SRGB))
    nx = (uu - 0.5) * 2
    ny = (vv - 0.5) * 2
    out_lin = P.post_crop_vignette(out_lin.clamp(min=0), prm["vignette"], nx, ny)
    gm = OUTPUT_GAMMA.get(output_space, "srgb")
    out = P.srgb_encode(out_lin.clamp(0, 1)) if gm == "srgb" else P.gamma_encode(out_lin.clamp(0, 1), gm)

    # ---- detail (output-referred)
    out = P.denoise(out, prm["nr"], px_scale)
    sh = dict(prm["sharpen"])
    if "sharpness" in local:
        sh["amount"] = sh.get("amount", 0)  # local sharpness modulates below
    out = P.sharpen(out, sh, px_scale)
    if "noise" in local:
        smooth = P.gaussian(out, 1.5 * max(px_scale, 0.4))
        w = local["noise"].clamp(0, 1).unsqueeze(0)
        out = out * (1 - w) + smooth * w
    out = P.add_grain(out, prm["grain"], px_scale)
    out = out.clamp(0, 1)

    hist = clipped = None
    if want_hist:
        q = (out * 255).round().long().clamp(0, 255)
        hist = [torch.bincount(q[c].flatten(), minlength=256).tolist() for c in range(3)]
        mx = out.max(0).values
        mn = out.min(0).values
        clipped = (float((mx >= 0.999).float().mean()), float((mn <= 0.001).float().mean()))
    return RenderResult(out, cp.as_shot_temp, cp.as_shot_tint, cp.temp, cp.tint, (cw, ch), hist, clipped,
                        missing_denoise)


def _nz(v) -> bool:
    return isinstance(v, torch.Tensor) or bool(v)


def _curve_active(pts) -> bool:
    return not (len(pts) == 2 and list(pts[0]) == [0, 0] and list(pts[1]) == [255, 255])


@functools.lru_cache(maxsize=64)
def _lut_tensor(pts_json: str) -> torch.Tensor:
    return torch.tensor(P.spline_lut(json.loads(pts_json)), device=DEVICE)


# ----------------------------------------------------------------------------- photo-level helpers

def decoded_for(session: Session, photo) -> Decoded:
    from app.library.paths import photo_path

    return get_decoded(photo_path(session, photo), key=f"p{photo.master_id or photo.id}")


def fit_size(cw: int, ch: int, max_w: int, max_h: int) -> tuple[int, int]:
    s = min(max_w / cw, max_h / ch, 1.0)
    return max(1, round(cw * s)), max(1, round(ch * s))


def to_uint8(img: torch.Tensor) -> torch.Tensor:
    return (img * 255.0 + 0.5).clamp(0, 255).to(torch.uint8)


def encode_jpeg(img: torch.Tensor, quality: int = 90) -> bytes:
    from torchvision.io import encode_jpeg as _enc

    u8 = to_uint8(img)
    try:
        return bytes(_enc(u8.contiguous(), quality=quality).cpu().numpy())
    except Exception:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.fromarray(u8.permute(1, 2, 0).cpu().numpy()).save(buf, "JPEG", quality=quality)
        return buf.getvalue()


def load_params(session: Session, photo) -> dict:
    from app.models import Edit

    e = session.get(Edit, photo.id)
    return full_params(e.params if e else None)


def render_thumb(session: Session, photo, dest: Path) -> Path:
    from app.config import THUMB_SIZE
    from app.engine.ai_masks import cached_ai_masks

    dec = decoded_for(session, photo)
    meta = meta_of(photo)
    prm = load_params(session, photo)
    geo = geometry_for(dec, meta, prm)
    w, h = fit_size(*geo.crop_px, THUMB_SIZE, THUMB_SIZE)
    res = render_params(dec, meta, prm, w, h, ai_masks=cached_ai_masks(photo, prm))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(encode_jpeg(res.image, 85))
    tmp.replace(dest)
    return dest
