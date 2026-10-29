"""Lightroom develop settings -> PhotoLab params.

Sources: the Lua-serialised `text` column of a Lightroom Classic catalog
(Adobe_imageDevelopSettings) or crs:* attributes from an XMP sidecar.
"""

from __future__ import annotations

import re
from typing import Any

from app.engine.params import DEFAULTS, HSL_BANDS, full

# ----------------------------------------------------------------------------- Lua table parser


class _Lua:
    def __init__(self, s: str):
        self.s = s
        self.i = 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n,;":
            self.i += 1

    def value(self) -> Any:
        self.ws()
        c = self.s[self.i]
        if c == "{":
            return self.table()
        if c == '"':
            return self.string()
        m = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?").match(self.s, self.i)
        if m:
            self.i = m.end()
            t = m.group(0)
            return float(t) if any(ch in t for ch in ".eE") else int(t)
        for lit, val in (("true", True), ("false", False), ("nil", None)):
            if self.s.startswith(lit, self.i):
                self.i += len(lit)
                return val
        raise ValueError(f"bad Lua at {self.i}: {self.s[self.i:self.i + 20]!r}")

    def string(self) -> str:
        self.i += 1
        out = []
        while self.s[self.i] != '"':
            if self.s[self.i] == "\\":
                self.i += 1
                out.append({"n": "\n", "t": "\t"}.get(self.s[self.i], self.s[self.i]))
            else:
                out.append(self.s[self.i])
            self.i += 1
        self.i += 1
        return "".join(out)

    def table(self):
        self.i += 1
        d: dict = {}
        arr: list = []
        while True:
            self.ws()
            if self.s[self.i] == "}":
                self.i += 1
                break
            m = re.compile(r"([A-Za-z_]\w*)\s*=").match(self.s, self.i)
            if m:
                self.i = m.end()
                d[m.group(1)] = self.value()
                continue
            m = re.compile(r'\[\s*"([^"]*)"\s*\]\s*=').match(self.s, self.i)
            if m:
                self.i = m.end()
                d[m.group(1)] = self.value()
                continue
            arr.append(self.value())
        if d and arr:
            d["_array"] = arr
            return d
        return d if d else arr


def parse_lr_settings(text: str | None) -> dict:
    if not text:
        return {}
    s = text.strip()
    if s.startswith("s ="):
        s = s[3:].strip()
    try:
        v = _Lua(s).value()
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


# ----------------------------------------------------------------------------- mapping

_BANDS_LR = ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]


def _pairs(v) -> list[list[float]] | None:
    if isinstance(v, list) and len(v) >= 4:
        return [[float(v[i]), float(v[i + 1])] for i in range(0, len(v) - 1, 2)]
    if isinstance(v, list) and v and isinstance(v[0], str):  # XMP "x, y"
        return [[float(a), float(b)] for a, b in (p.split(",") for p in v)]
    return None


def lr_to_params(lr: dict) -> dict:
    p = full({})

    def g(key, default=None):
        v = lr.get(key, default)
        return default if v is None else v

    look = lr.get("Look")
    if isinstance(look, dict) and look.get("Name"):
        p["profile"] = look["Name"]
        if "Amount" in look:
            p["profile_amount"] = round(float(look["Amount"]) * 100)
    elif lr.get("CameraProfile"):
        p["profile"] = lr["CameraProfile"]
    wb = g("WhiteBalance", "As Shot")
    if wb not in ("As Shot",) and lr.get("Temperature"):
        p["wb"] = "custom"
        p["temp"] = float(lr["Temperature"])
        p["tint"] = float(lr.get("Tint", 0))
    for ours, theirs in (("exposure", "Exposure2012"), ("contrast", "Contrast2012"),
                         ("highlights", "Highlights2012"), ("shadows", "Shadows2012"),
                         ("whites", "Whites2012"), ("blacks", "Blacks2012"), ("texture", "Texture"),
                         ("clarity", "Clarity2012"), ("dehaze", "Dehaze"), ("vibrance", "Vibrance"),
                         ("saturation", "Saturation")):
        if theirs in lr and lr[theirs] is not None:
            p[ours] = float(lr[theirs]) if ours == "exposure" else round(float(lr[theirs]))
    # Curves
    for ours, theirs in (("curve", "ToneCurvePV2012"), ("curve_r", "ToneCurvePV2012Red"),
                         ("curve_g", "ToneCurvePV2012Green"), ("curve_b", "ToneCurvePV2012Blue")):
        pts = _pairs(lr.get(theirs))
        if pts:
            p[ours] = pts
    par = p["parametric"]
    for ours, theirs in (("shadows", "ParametricShadows"), ("darks", "ParametricDarks"),
                         ("lights", "ParametricLights"), ("highlights", "ParametricHighlights"),
                         ("split_shadows", "ParametricShadowSplit"),
                         ("split_midtones", "ParametricMidtoneSplit"),
                         ("split_highlights", "ParametricHighlightSplit")):
        if theirs in lr:
            par[ours] = float(lr[theirs])
    # HSL / B&W
    for i, band in enumerate(_BANDS_LR):
        p["hsl"]["hue"][i] = float(lr.get(f"HueAdjustment{band}", 0) or 0)
        p["hsl"]["sat"][i] = float(lr.get(f"SaturationAdjustment{band}", 0) or 0)
        p["hsl"]["lum"][i] = float(lr.get(f"LuminanceAdjustment{band}", 0) or 0)
        p["bw_mix"][i] = float(lr.get(f"GrayMixer{band}", 0) or 0)
    p["bw"] = bool(lr.get("ConvertToGrayscale", False))
    # Colour grading
    gr = p["grading"]
    gr["shadows"] = {"hue": g("SplitToningShadowHue", 0), "sat": g("SplitToningShadowSaturation", 0),
                     "lum": g("ColorGradeShadowLum", 0)}
    gr["highlights"] = {"hue": g("SplitToningHighlightHue", 0), "sat": g("SplitToningHighlightSaturation", 0),
                        "lum": g("ColorGradeHighlightLum", 0)}
    gr["midtones"] = {"hue": g("ColorGradeMidtoneHue", 0), "sat": g("ColorGradeMidtoneSat", 0),
                      "lum": g("ColorGradeMidtoneLum", 0)}
    gr["global"] = {"hue": g("ColorGradeGlobalHue", 0), "sat": g("ColorGradeGlobalSat", 0),
                    "lum": g("ColorGradeGlobalLum", 0)}
    gr["blending"] = g("ColorGradeBlending", 50)
    gr["balance"] = g("SplitToningBalance", 0)
    # Detail
    p["sharpen"] = {"amount": g("Sharpness", 40), "radius": g("SharpenRadius", 1.0),
                    "detail": g("SharpenDetail", 25), "masking": g("SharpenEdgeMasking", 0)}
    p["nr"] = {"luminance": g("LuminanceSmoothing", 0), "lum_detail": g("LuminanceNoiseReductionDetail", 50),
               "lum_contrast": g("LuminanceNoiseReductionContrast", 0), "color": g("ColorNoiseReduction", 25),
               "color_detail": g("ColorNoiseReductionDetail", 50),
               "color_smooth": g("ColorNoiseReductionSmoothness", 50)}
    # Optics
    p["lens"] = {"profile": bool(g("LensProfileEnable", 0)),
                 "distortion": g("LensProfileDistortionScale", 100),
                 "vignetting": g("LensProfileVignettingScale", 100),
                 "ca": bool(g("AutoLateralCA", 0)),
                 "manual_distortion": g("LensManualDistortionAmount", 0)}
    # Geometry
    if g("HasCrop", False) or any(k in lr for k in ("CropLeft", "CropRight")):
        p["crop"] = {"left": float(g("CropLeft", 0)), "top": float(g("CropTop", 0)),
                     "right": float(g("CropRight", 1)), "bottom": float(g("CropBottom", 1)),
                     "angle": float(g("CropAngle", 0)), "aspect": None}
    p["transform"] = {"vertical": g("PerspectiveVertical", 0), "horizontal": g("PerspectiveHorizontal", 0),
                      "rotate": g("PerspectiveRotate", 0.0), "aspect": g("PerspectiveAspect", 0),
                      "scale": g("PerspectiveScale", 100), "x": g("PerspectiveX", 0.0),
                      "y": g("PerspectiveY", 0.0)}
    # Effects
    p["vignette"] = {"amount": g("PostCropVignetteAmount", 0), "midpoint": g("PostCropVignetteMidpoint", 50),
                     "roundness": g("PostCropVignetteRoundness", 0), "feather": g("PostCropVignetteFeather", 50),
                     "highlights": g("PostCropVignetteHighlightContrast", 0)}
    p["grain"] = {"amount": g("GrainAmount", 0), "size": g("GrainSize", 25), "roughness": g("GrainFrequency", 50)}
    p["masks"] = _lr_masks(lr)
    return p


def _lr_masks(lr: dict) -> list[dict]:
    """Lightroom mask groups -> our masks (gradient, radial, brush, AI subject/sky)."""
    out = []
    groups = lr.get("MaskGroupBasedCorrections") or []
    if isinstance(groups, dict):
        groups = groups.get("_array", [])
    keymap = {"LocalExposure2012": ("exposure", 4.0), "LocalContrast2012": ("contrast", 100),
              "LocalHighlights2012": ("highlights", 100), "LocalShadows2012": ("shadows", 100),
              "LocalWhites2012": ("whites", 100), "LocalBlacks2012": ("blacks", 100),
              "LocalClarity2012": ("clarity", 100), "LocalTexture": ("texture", 100),
              "LocalDehaze": ("dehaze", 100), "LocalSaturation": ("saturation", 100),
              "LocalTemperature": ("temp", 100), "LocalTint": ("tint", 100),
              "LocalSharpness": ("sharpness", 100), "LocalLuminanceNoise": ("noise", 100),
              "LocalHue": ("hue", 100)}
    for gi, grp in enumerate(groups):
        if not isinstance(grp, dict):
            continue
        adjust = {}
        for k, (ours, scale) in keymap.items():
            v = grp.get(k)
            if v:
                adjust[ours] = round(float(v) * scale, 3) if ours == "exposure" else round(float(v) * scale)
        comps = []
        cmasks = grp.get("CorrectionMasks") or []
        if isinstance(cmasks, dict):
            cmasks = cmasks.get("_array", [])
        strokes = []
        for cm in cmasks:
            if not isinstance(cm, dict):
                continue
            what = cm.get("What", "")
            mode = {0: "add", 1: "add", 2: "subtract", 3: "intersect"}.get(int(cm.get("MaskBlendMode", 0) or 0), "add")
            if what == "Mask/Gradient":
                comps.append({"type": "linear", "x0": cm.get("ZeroX", 0.5), "y0": cm.get("ZeroY", 0.2),
                              "x1": cm.get("FullX", 0.5), "y1": cm.get("FullY", 0.6), "mode": mode})
                # LR: full effect at Full*, zero at Zero* -> ours is 1 at (x0,y0)
                c = comps[-1]
                c["x0"], c["y0"], c["x1"], c["y1"] = cm.get("FullX", 0.5), cm.get("FullY", 0.6), cm.get("ZeroX", 0.5), cm.get("ZeroY", 0.2)
            elif what == "Mask/CircularGradient":
                l, t, r, b = (float(cm.get(k, d)) for k, d in (("Left", 0.3), ("Top", 0.3), ("Right", 0.7), ("Bottom", 0.7)))
                comps.append({"type": "radial", "cx": (l + r) / 2, "cy": (t + b) / 2, "rx": abs(r - l) / 2,
                              "ry": abs(b - t) / 2, "angle": float(cm.get("Angle", 0)),
                              "feather": float(cm.get("Feather", 0.5)) * 100,
                              "invert": not bool(cm.get("Flipped", False)), "mode": mode})
            elif what == "Mask/Paint":
                d = cm.get("Dabs") or []
                if isinstance(d, dict):
                    d = d.get("_array", [])
                pts = []
                for dab in d:
                    m = re.match(r"d\s+([\d.]+)\s+([\d.]+)", str(dab))
                    if m:
                        pts.append([float(m.group(1)), float(m.group(2))])
                if pts:
                    strokes.append({"pts": pts, "size": float(cm.get("Radius", 0.05)) * 2,
                                    "feather": float(cm.get("Feather", 0.5)) * 100,
                                    "flow": float(cm.get("Flow", 1)) * 100,
                                    "erase": float(cm.get("MaskValue", 1)) == 0})
            elif what == "Mask/Image":
                kind = str(cm.get("MaskSubType", "")).lower()
                comps.append({"type": "ai", "kind": {"0": "subject", "1": "sky", "2": "person"}.get(kind, "subject"),
                              "mode": mode})
        if strokes:
            comps.append({"type": "brush", "strokes": strokes, "mode": "add"})
        if comps and adjust:
            out.append({"id": f"lr{gi}", "name": f"마스크 {gi + 1}", "visible": True, "amount": 100,
                        "components": comps, "adjust": adjust})
    return out


def describe(p: dict) -> list[str]:
    """Short human summary of non-default settings (for import previews)."""
    out = []
    for k in ("exposure", "contrast", "highlights", "shadows", "whites", "blacks", "texture", "clarity",
              "dehaze", "vibrance", "saturation"):
        if p.get(k) != DEFAULTS[k]:
            out.append(f"{k} {p[k]:+g}")
    if p.get("profile") != DEFAULTS["profile"]:
        out.append(f"프로파일 {p['profile']}")
    if p.get("masks"):
        out.append(f"마스크 {len(p['masks'])}개")
    _ = HSL_BANDS
    return out
