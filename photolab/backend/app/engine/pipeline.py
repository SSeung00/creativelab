"""GPU develop pipeline (PyTorch). Image tensors are (3,H,W) float32 unless noted.

Stage order (scene-referred until the tone curve, like ACR):
  geometry + lens  ->  white balance + camera matrix (linear ProPhoto D50)  ->  profile hue/sat map
  ->  dehaze  ->  exposure / tone (highlights, shadows, whites, blacks, contrast) + clarity/texture
  ->  look table  ->  profile tone curve  ->  point curves  ->  colour (HSL, vibrance, grading)
  ->  output colour space  ->  vignette / grain  ->  noise reduction + sharpening (output-referred)

Local tone operators use a fast guided filter whose coefficients are computed on a small
"analysis" render of the whole frame, so a zoomed-in region renders exactly like the same
area of the full image.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn.functional as F

from app.engine.dcp import HSVMap

PROPHOTO_Y = (0.2880402, 0.7118741, 0.0000857)
MID_GREY = 0.18


# ----------------------------------------------------------------------------- helpers

def smoothstep(e0: float, e1: float, x: torch.Tensor) -> torch.Tensor:
    t = ((x - e0) / (e1 - e0)).clamp(0, 1)
    return t * t * (3 - 2 * t)


def luma(rgb: torch.Tensor) -> torch.Tensor:
    return PROPHOTO_Y[0] * rgb[0] + PROPHOTO_Y[1] * rgb[1] + PROPHOTO_Y[2] * rgb[2]


def srgb_encode(x: torch.Tensor) -> torch.Tensor:
    x = x.clamp(min=0)
    return torch.where(x <= 0.0031308, x * 12.92, 1.055 * x.clamp(min=1e-12).pow(1 / 2.4) - 0.055)


def srgb_decode(x: torch.Tensor) -> torch.Tensor:
    return torch.where(x <= 0.04045, x / 12.92, ((x.clamp(min=0) + 0.055) / 1.055).pow(2.4))


def gamma_encode(x: torch.Tensor, g: float) -> torch.Tensor:
    return x.clamp(min=0).pow(1.0 / g)


def lut1d(x: torch.Tensor, lut: torch.Tensor) -> torch.Tensor:
    """Evaluate a uniformly sampled curve on [0,1] (clamped)."""
    n = lut.numel() - 1
    p = x.clamp(0, 1) * n
    i0 = p.floor().clamp(max=n - 1).long()
    f = p - i0
    return lut[i0] * (1 - f) + lut[i0 + 1] * f


def mat3(rgb: torch.Tensor, m: np.ndarray | torch.Tensor) -> torch.Tensor:
    if not isinstance(m, torch.Tensor):
        m = torch.tensor(m, dtype=rgb.dtype, device=rgb.device)
    return torch.einsum("ij,jhw->ihw", m, rgb)


def box(x: torch.Tensor, r: int) -> torch.Tensor:
    """Mean filter of radius r on (C,H,W) or (H,W)."""
    if r <= 0:
        return x
    squeeze = x.dim() == 2
    t = x.unsqueeze(0) if squeeze else x
    t = F.avg_pool2d(t.unsqueeze(0), 2 * r + 1, stride=1, padding=r, count_include_pad=False).squeeze(0)
    return t.squeeze(0) if squeeze else t


def gaussian(x: torch.Tensor, sigma: float) -> torch.Tensor:
    """Separable Gaussian blur on (C,H,W)."""
    if sigma < 0.3:
        return x
    r = max(1, int(math.ceil(sigma * 3)))
    k = torch.arange(-r, r + 1, device=x.device, dtype=x.dtype)
    k = torch.exp(-(k * k) / (2 * sigma * sigma))
    k = k / k.sum()
    c = x.shape[0]
    t = x.unsqueeze(0)
    t = F.conv2d(F.pad(t, (r, r, 0, 0), mode="replicate"), k.view(1, 1, 1, -1).expand(c, 1, 1, -1), groups=c)
    t = F.conv2d(F.pad(t, (0, 0, r, r), mode="replicate"), k.view(1, 1, -1, 1).expand(c, 1, -1, 1), groups=c)
    return t.squeeze(0)


def guided_coeffs(guide: torch.Tensor, r: int, eps: float) -> tuple[torch.Tensor, torch.Tensor]:
    """Self-guided filter coefficients (a, b) on a (H,W) map: smoothed = a*guide + b."""
    m = box(guide, r)
    v = box(guide * guide, r) - m * m
    a = v / (v + eps)
    b = m - a * m
    return box(a, r), box(b, r)


# ----------------------------------------------------------------------------- HSV (DNG flavour)

def rgb_to_hsv(rgb: torch.Tensor):
    r, g, b = rgb[0], rgb[1], rgb[2]
    mx = torch.maximum(torch.maximum(r, g), b)
    mn = torch.minimum(torch.minimum(r, g), b)
    d = mx - mn
    s = torch.where(mx > 0, d / mx.clamp(min=1e-12), torch.zeros_like(mx))
    dd = d.clamp(min=1e-12)
    h = torch.where(r == mx, (g - b) / dd, torch.where(g == mx, 2 + (b - r) / dd, 4 + (r - g) / dd))
    h = torch.where(d > 0, h, torch.zeros_like(h))
    h = torch.where(h < 0, h + 6, h)
    return h, s, mx


def hsv_to_rgb(h: torch.Tensor, s: torch.Tensor, v: torch.Tensor) -> torch.Tensor:
    h = torch.remainder(h, 6.0)
    i = h.floor()
    f = h - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    i = i.long()
    r = torch.stack([v, q, p, p, t, v])
    g = torch.stack([t, v, v, q, p, p])
    b = torch.stack([p, p, t, v, v, q])
    idx = i.unsqueeze(0).clamp(0, 5)
    return torch.cat([r.gather(0, idx), g.gather(0, idx), b.gather(0, idx)], 0)


@dataclass
class HSVTable:
    data: torch.Tensor  # (V*Hd*S, 3)
    hue_divs: int
    sat_divs: int
    val_divs: int
    srgb_vals: bool


def hsv_table(m: HSVMap, m2: HSVMap | None, weight: float, device) -> HSVTable:
    d = m.data if m2 is None else m.data * weight + m2.data * (1 - weight)
    t = torch.tensor(np.ascontiguousarray(d).reshape(-1, 3), dtype=torch.float32, device=device)
    return HSVTable(t, m.hue_divs, m.sat_divs, m.val_divs, m.srgb_encoded_vals)


def apply_hsv_table(rgb: torch.Tensor, tab: HSVTable, amount: float = 1.0) -> torch.Tensor:
    h, s, v = rgb_to_hsv(rgb.clamp(min=0))
    hd, sd, vd = tab.hue_divs, tab.sat_divs, tab.val_divs
    hs = h * (hd / 6.0)
    h0 = hs.floor()
    hf = hs - h0
    h0 = torch.remainder(h0, hd).long()
    h1 = torch.remainder(h0 + 1, hd)
    ss = s.clamp(0, 1) * (sd - 1)
    s0 = ss.floor().clamp(max=sd - 2).long()
    sf = ss - s0
    s1 = s0 + 1

    def at(vi, hi, si):
        return tab.data[(vi * hd + hi) * sd + si]  # (H,W,3)

    hf_ = hf.unsqueeze(-1)
    sf_ = sf.unsqueeze(-1)

    def plane(vi):
        a = at(vi, h0, s0) * (1 - hf_) + at(vi, h1, s0) * hf_
        b = at(vi, h0, s1) * (1 - hf_) + at(vi, h1, s1) * hf_
        return a * (1 - sf_) + b * sf_

    if vd > 1:
        vv = srgb_encode(v.clamp(0, 1)) if tab.srgb_vals else v.clamp(0, 1)
        vs = vv * (vd - 1)
        v0 = vs.floor().clamp(max=vd - 2).long()
        vf = (vs - v0).unsqueeze(-1)
        val = plane(v0) * (1 - vf) + plane(v0 + 1) * vf
    else:
        val = plane(torch.zeros_like(h0))
    hue_shift, sat_scale, val_scale = val[..., 0], val[..., 1], val[..., 2]
    if amount != 1.0:
        hue_shift = hue_shift * amount
        sat_scale = 1 + (sat_scale - 1) * amount
        val_scale = 1 + (val_scale - 1) * amount
    h = h + hue_shift * (6.0 / 360.0)
    s = (s * sat_scale).clamp(0, 1)
    v = v * val_scale
    return hsv_to_rgb(h, s, v)


def rgb_tone(rgb: torch.Tensor, lut: torch.Tensor) -> torch.Tensor:
    """Adobe's hue-preserving RGB tone curve: curve on max & min, middle interpolated."""
    srt, idx = torch.sort(rgb.clamp(0, 1), dim=0)
    lo, mid, hi = srt[0], srt[1], srt[2]
    lo2 = lut1d(lo, lut)
    hi2 = lut1d(hi, lut)
    rng = (hi - lo)
    mid2 = torch.where(rng > 1e-7, lo2 + (hi2 - lo2) * (mid - lo) / rng.clamp(min=1e-7), lut1d(mid, lut))
    out = torch.empty_like(rgb)
    out.scatter_(0, idx, torch.stack([lo2, mid2, hi2]))
    return out


# ----------------------------------------------------------------------------- curves

def spline_lut(points: list, n: int = 1024) -> np.ndarray:
    """Natural cubic spline through Lightroom-style (0..255) points -> LUT on [0,1] (cached)."""
    key = tuple((float(x), float(y)) for x, y in points)
    return _spline_lut(key, n).copy()


@functools.lru_cache(maxsize=256)
def _spline_lut(pts_key: tuple, n: int) -> np.ndarray:
    pts = sorted((x / 255.0, y / 255.0) for x, y in pts_key)
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    grid = np.linspace(0, 1, n)
    if len(pts) == 2:
        return np.clip(np.interp(grid, xs, ys), 0, 1).astype(np.float32)
    k = len(xs)
    h = np.diff(xs)
    h[h == 0] = 1e-6
    a = np.zeros((k, k))
    rhs = np.zeros(k)
    a[0, 0] = a[-1, -1] = 1
    for i in range(1, k - 1):
        a[i, i - 1] = h[i - 1]
        a[i, i] = 2 * (h[i - 1] + h[i])
        a[i, i + 1] = h[i]
        rhs[i] = 3 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1])
    c = np.linalg.solve(a, rhs)
    i = np.clip(np.searchsorted(xs, grid) - 1, 0, k - 2)
    dx = grid - xs[i]
    b = (ys[i + 1] - ys[i]) / h[i] - h[i] * (2 * c[i] + c[i + 1]) / 3
    d = (c[i + 1] - c[i]) / (3 * h[i])
    out = ys[i] + b * dx + c[i] * dx * dx + d * dx ** 3
    out[grid <= xs[0]] = ys[0]
    out[grid >= xs[-1]] = ys[-1]
    return np.clip(out, 0, 1).astype(np.float32)


def parametric_lut(p: dict, n: int = 1024) -> np.ndarray | None:
    amts = [p.get("shadows", 0), p.get("darks", 0), p.get("lights", 0), p.get("highlights", 0)]
    if not any(amts):
        return None
    s1 = p.get("split_shadows", 25) / 100
    s2 = p.get("split_midtones", 50) / 100
    s3 = p.get("split_highlights", 75) / 100
    x = np.linspace(0, 1, n)
    # Each region gets a smooth bump peaking inside it; amplitude ~ Lightroom's +-100 range.
    centers = [s1 / 2, (s1 + s2) / 2, (s2 + s3) / 2, (s3 + 1) / 2]
    widths = [s1, s2 - s1, s3 - s2, 1 - s3]
    y = x.copy()
    for a, c, w in zip(amts, centers, widths, strict=True):
        if a:
            w = max(w, 0.05) * 1.1
            bump = np.exp(-0.5 * ((x - c) / (w * 0.6)) ** 2) * np.sin(np.pi * x)
            y += a / 100.0 * 0.12 * bump
    return np.clip(y, 0, 1).astype(np.float32)


# ----------------------------------------------------------------------------- tone (scene-referred)

@dataclass
class ToneParams:
    exposure: torch.Tensor | float = 0.0  # stops
    contrast: torch.Tensor | float = 0.0  # -1..1
    highlights: torch.Tensor | float = 0.0
    shadows: torch.Tensor | float = 0.0
    whites: torch.Tensor | float = 0.0
    blacks: torch.Tensor | float = 0.0
    clarity: torch.Tensor | float = 0.0
    texture: torch.Tensor | float = 0.0


def tone_gain(L: torch.Tensor, base: torch.Tensor | None, base_wide: torch.Tensor | None,
              detail_fine: torch.Tensor | None, tp: ToneParams, k: dict) -> torch.Tensor:
    """Per-pixel gain in stops. L = log2(Y/0.18), base = edge-aware smooth of L."""
    g = torch.zeros_like(L) + tp.exposure
    Lx = L + tp.exposure
    B = (base if base is not None else L) + tp.exposure
    # Shadows / highlights: act on the smooth base so local detail survives.
    shadow_w = smoothstep(k.get("sh_hi", 1.5), k.get("sh_lo", -3.5), B) * smoothstep(-11.0, -6.0, B)
    g = g + tp.shadows * k["shadows"] * shadow_w
    g = g + tp.highlights * k["highlights"] * smoothstep(k.get("hl_lo", -0.5), k.get("hl_hi", 2.6), B)
    # Contrast around mid grey (soft-saturating in log space).
    cw = k.get("contrast_w", 2.6)
    g = g + tp.contrast * k["contrast"] * cw * torch.tanh((Lx - k.get("contrast_pivot", 0.0)) / cw)
    # Whites / blacks: end points.
    g = g + tp.whites * k["whites"] * smoothstep(k.get("wh_lo", 0.0), k.get("wh_hi", 2.7), Lx)
    g = g + tp.blacks * k["blacks"] * smoothstep(k.get("bl_hi", -1.5), k.get("bl_lo", -6.5), Lx)
    # Clarity: mid-scale local contrast, strongest in mid tones.
    if base_wide is not None:
        mid_w = torch.exp(-((Lx / k.get("clarity_w", 2.8)) ** 2))
        g = g + tp.clarity * k["clarity"] * (L - base_wide).clamp(-2.5, 2.5) * mid_w
    if detail_fine is not None:
        g = g + tp.texture * k["texture"] * detail_fine.clamp(-1.5, 1.5)
    return g


def apply_gain(rgb: torch.Tensor, gain_stops: torch.Tensor) -> torch.Tensor:
    return rgb * torch.exp2(gain_stops).unsqueeze(0)


# ----------------------------------------------------------------------------- colour (display-ish)

HSL_CENTERS = torch.tensor([0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0])


def band_weights(hue_deg: torch.Tensor) -> torch.Tensor:
    """(8,H,W) partition-of-unity weights for Lightroom's 8 colour bands (tent functions)."""
    c = HSL_CENTERS.to(hue_deg.device)
    n = len(c)
    ws = []
    for i in range(n):
        prev_c = c[i - 1] - (360 if i == 0 else 0)
        next_c = c[(i + 1) % n] + (360 if i == n - 1 else 0)
        d = torch.remainder(hue_deg - c[i] + 180, 360) - 180  # signed distance in degrees
        left = (c[i] - prev_c)
        right = (next_c - c[i])
        w = torch.where(d < 0, 1 + d / left, 1 - d / right).clamp(0, 1)
        ws.append(w)
    return torch.stack(ws)


def apply_hsl(rgb_g: torch.Tensor, hsl: dict) -> torch.Tensor:
    hue = torch.tensor(hsl["hue"], dtype=torch.float32, device=rgb_g.device) / 100
    sat = torch.tensor(hsl["sat"], dtype=torch.float32, device=rgb_g.device) / 100
    lum = torch.tensor(hsl["lum"], dtype=torch.float32, device=rgb_g.device) / 100
    if not (hue.any() or sat.any() or lum.any()):
        return rgb_g
    h, s, v = rgb_to_hsv(rgb_g)
    hd = h * 60.0
    w = band_weights(hd)
    # hue: up to +-30 degrees toward the neighbouring band
    dh = (w * hue.view(-1, 1, 1)).sum(0) * 30.0
    ds = (w * sat.view(-1, 1, 1)).sum(0)
    dl = (w * lum.view(-1, 1, 1)).sum(0)
    h2 = (hd + dh) / 60.0
    s2 = (s * (1 + ds)).clamp(0, 1)
    # luminance: scaled by saturation so greys stay put
    v2 = v * torch.exp2(dl * 1.2 * s.clamp(0, 1).sqrt())
    return hsv_to_rgb(h2, s2, v2)


def apply_bw(rgb_g: torch.Tensor, mix: list) -> torch.Tensor:
    h, s, v = rgb_to_hsv(rgb_g)
    w = band_weights(h * 60.0)
    m = torch.tensor(mix, dtype=torch.float32, device=rgb_g.device) / 100
    y = PROPHOTO_Y[0] * rgb_g[0] + PROPHOTO_Y[1] * rgb_g[1] + PROPHOTO_Y[2] * rgb_g[2]
    y = y * torch.exp2((w * m.view(-1, 1, 1)).sum(0) * 1.5 * s)
    return y.unsqueeze(0).expand(3, -1, -1).clone()


def apply_vibrance_saturation(rgb_g: torch.Tensor, vibrance, saturation) -> torch.Tensor:
    y = (PROPHOTO_Y[0] * rgb_g[0] + PROPHOTO_Y[1] * rgb_g[1] + PROPHOTO_Y[2] * rgb_g[2]).unsqueeze(0)
    chroma = rgb_g - y
    f = 1.0 + saturation
    if isinstance(vibrance, torch.Tensor) or vibrance:
        mx = rgb_g.max(0).values
        mn = rgb_g.min(0).values
        satn = ((mx - mn) / mx.clamp(min=1e-6)).clamp(0, 1)
        # protect skin-ish hues (orange) a bit, like Lightroom's vibrance
        h, _, _ = rgb_to_hsv(rgb_g)
        skin = torch.exp(-(((h * 60.0 - 25.0) / 25.0) ** 2))
        vib = vibrance * (1 - satn) ** 2 * (1 - 0.5 * skin * (vibrance > 0 if not isinstance(vibrance, torch.Tensor) else (vibrance > 0).float()))
        f = f + vib * 1.2
    if isinstance(f, torch.Tensor):
        f = f.unsqueeze(0) if f.dim() == 2 else f
    return y + chroma * f


def hue_rgb(hue_deg: float, device) -> torch.Tensor:
    h = torch.tensor([[hue_deg / 60.0]], device=device)
    one = torch.ones_like(h)
    return hsv_to_rgb(h, one, one).view(3, 1, 1)


def apply_grading(rgb_g: torch.Tensor, gr: dict) -> torch.Tensor:
    regions = ["shadows", "midtones", "highlights", "global"]
    if not any(gr[r]["sat"] or gr[r]["lum"] for r in regions):
        return rgb_g
    y = (PROPHOTO_Y[0] * rgb_g[0] + PROPHOTO_Y[1] * rgb_g[1] + PROPHOTO_Y[2] * rgb_g[2]).clamp(0, 1)
    bal = gr.get("balance", 0) / 100.0
    blend = 0.15 + gr.get("blending", 50) / 100.0 * 0.5
    pivot = 0.5 - bal * 0.3
    ws = smoothstep(pivot + blend * 0.5, pivot - blend, y)
    wh = smoothstep(pivot - blend * 0.5, pivot + blend, y)
    wm = (1 - ws - wh).clamp(0, 1)
    out = rgb_g
    for name, w in (("shadows", ws), ("midtones", wm), ("highlights", wh), ("global", torch.ones_like(y))):
        r = gr[name]
        if r["sat"]:
            tint = hue_rgb(r["hue"], rgb_g.device)
            tint = tint - (PROPHOTO_Y[0] * tint[0] + PROPHOTO_Y[1] * tint[1] + PROPHOTO_Y[2] * tint[2])
            out = out + w.unsqueeze(0) * tint * (r["sat"] / 100.0) * 0.35
        if r["lum"]:
            out = out * torch.exp2(w * r["lum"] / 100.0 * 0.8).unsqueeze(0)
    return out


# ----------------------------------------------------------------------------- detail

def _ycc(rgb: torch.Tensor):
    y = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return y, rgb[2] - y, rgb[0] - y


def _rgb_from_ycc(y, cb, cr):
    b = cb + y
    r = cr + y
    g = (y - 0.299 * r - 0.114 * b) / 0.587
    return torch.stack([r, g, b])


def denoise(rgb: torch.Tensor, nr: dict, px_scale: float) -> torch.Tensor:
    """Output-referred NR: chroma (guided by luma) and luminance (edge-aware). px_scale = output px per full-res px."""
    color = nr.get("color", 0) / 100.0
    lum = nr.get("luminance", 0) / 100.0
    if color <= 0 and lum <= 0:
        return rgb
    y, cb, cr = _ycc(rgb)
    if color > 0:
        r = max(1, int(round((2 + color * 10) * px_scale)))
        eps = 1e-4 + (1 - nr.get("color_detail", 50) / 100.0) * 4e-3
        for ch in (cb, cr):
            m_y = box(y, r)
            m_c = box(ch, r)
            cov = box(y * ch, r) - m_y * m_c
            var = box(y * y, r) - m_y * m_y
            a = cov / (var + eps)
            b = m_c - a * m_y
            ch.copy_(box(a, r) * y + box(b, r))
    if lum > 0:
        r = max(1, int(round((1 + lum * 3) * px_scale)))
        eps = (lum ** 2) * 2e-3 * (1.5 - nr.get("lum_detail", 50) / 100.0)
        a, b = guided_coeffs(y, r, eps)
        y = a * y + b
    return _rgb_from_ycc(y, cb, cr)


def sharpen(rgb: torch.Tensor, sh: dict, px_scale: float) -> torch.Tensor:
    amount = sh.get("amount", 0) / 100.0
    if amount <= 0:
        return rgb
    radius = sh.get("radius", 1.0) * px_scale
    if radius < 0.25:
        return rgb
    y = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]).unsqueeze(0)
    blur = gaussian(y, max(radius, 0.35))
    detail = (y - blur).squeeze(0)
    # 'Detail' slider: low = suppress halos (limit large differences), high = full strength.
    lim = 0.02 + (sh.get("detail", 25) / 100.0) ** 1.5 * 0.25
    detail = lim * torch.tanh(detail / lim)
    masking = sh.get("masking", 0) / 100.0
    if masking > 0:
        gy, gx = torch.gradient(gaussian(y, 1.0 * max(px_scale, 0.5)).squeeze(0))
        mag = torch.sqrt(gx * gx + gy * gy)
        mask = smoothstep(masking * 0.03, masking * 0.03 + 0.02, mag)
        detail = detail * mask
    strength = amount * 1.6 * min(1.0, px_scale ** 0.5)
    return rgb + strength * detail.unsqueeze(0)


# ----------------------------------------------------------------------------- effects

def post_crop_vignette(rgb_lin: torch.Tensor, v: dict, nx: torch.Tensor, ny: torch.Tensor) -> torch.Tensor:
    amt = v.get("amount", 0) / 100.0
    if not amt:
        return rgb_lin
    roundness = v.get("roundness", 0) / 100.0
    mid = v.get("midpoint", 50) / 100.0
    feather = max(v.get("feather", 50) / 100.0, 0.02)
    # elliptical (roundness -1) .. circular (+1) distance
    ax = 1.0
    ay = 1.0 - 0.0 * roundness
    d = torch.sqrt((nx / ax) ** 2 + (ny / ay) ** 2) / math.sqrt(2) * 2
    start = 0.3 + mid * 0.9
    w = smoothstep(start - feather * 0.6, start + feather * 0.4, d)
    g = torch.exp2(w * amt * 2.0).unsqueeze(0) if amt > 0 else torch.exp2(w * amt * 2.5).unsqueeze(0)
    if amt > 0:  # lighten toward white
        return rgb_lin + (1 - rgb_lin.clamp(0, 1)) * (w * amt).unsqueeze(0) * 0.8
    return rgb_lin * g


def add_grain(rgb_g: torch.Tensor, gr: dict, px_scale: float, seed: int = 7) -> torch.Tensor:
    amt = gr.get("amount", 0) / 100.0
    if not amt:
        return rgb_g
    gen = torch.Generator(device=rgb_g.device).manual_seed(seed)
    n = torch.randn((1, *rgb_g.shape[1:]), generator=gen, device=rgb_g.device)
    size = gr.get("size", 25) / 100.0
    n = gaussian(n, 0.3 + size * 2.0 * px_scale)
    n = n / n.std().clamp(min=1e-6)
    y = (0.299 * rgb_g[0] + 0.587 * rgb_g[1] + 0.114 * rgb_g[2]).clamp(0, 1)
    w = 4 * y * (1 - y)
    return rgb_g + n * amt * 0.06 * w.unsqueeze(0)
