"""Edit parameters: one JSON document per photo. Names follow Lightroom's panels.

Stored edits only keep what differs from DEFAULTS; `full()` merges them back.
"""

from __future__ import annotations

import copy
import json
from typing import Any

HSL_BANDS = ["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]

DEFAULTS: dict[str, Any] = {
    "version": 1,
    # --- Basic ---
    "profile": "Adobe Color",
    "profile_amount": 100,
    "wb": "as_shot",  # as_shot | custom | auto
    "temp": None,  # Kelvin, used when wb == custom
    "tint": None,
    "exposure": 0.0,
    "contrast": 0,
    "highlights": 0,
    "shadows": 0,
    "whites": 0,
    "blacks": 0,
    "texture": 0,
    "clarity": 0,
    "dehaze": 0,
    "vibrance": 0,
    "saturation": 0,
    # --- Tone curve (points 0..255, Lightroom style) ---
    "curve": [[0, 0], [255, 255]],
    "curve_r": [[0, 0], [255, 255]],
    "curve_g": [[0, 0], [255, 255]],
    "curve_b": [[0, 0], [255, 255]],
    "parametric": {"highlights": 0, "lights": 0, "darks": 0, "shadows": 0,
                   "split_shadows": 25, "split_midtones": 50, "split_highlights": 75},
    # --- Colour mixer ---
    "hsl": {"hue": [0] * 8, "sat": [0] * 8, "lum": [0] * 8},
    "bw": False,
    "bw_mix": [0] * 8,
    # --- Colour grading ---
    "grading": {
        "shadows": {"hue": 0, "sat": 0, "lum": 0},
        "midtones": {"hue": 0, "sat": 0, "lum": 0},
        "highlights": {"hue": 0, "sat": 0, "lum": 0},
        "global": {"hue": 0, "sat": 0, "lum": 0},
        "blending": 50,
        "balance": 0,
    },
    # --- Detail ---
    "sharpen": {"amount": 40, "radius": 1.0, "detail": 25, "masking": 0},
    "nr": {"luminance": 0, "lum_detail": 50, "lum_contrast": 0, "color": 25, "color_detail": 50,
           "color_smooth": 50},
    "ai_denoise": {"enabled": False, "amount": 50},
    # --- Optics ---
    "lens": {"profile": True, "distortion": 100, "vignetting": 100, "ca": True, "manual_distortion": 0},
    # --- Geometry ---
    "transform": {"vertical": 0, "horizontal": 0, "rotate": 0.0, "aspect": 0, "scale": 100,
                  "x": 0.0, "y": 0.0},
    "crop": {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0, "angle": 0.0, "aspect": None},
    "rotate90": 0,  # extra quarter turns clockwise on top of the camera orientation
    "flip": False,
    # --- Effects ---
    "vignette": {"amount": 0, "midpoint": 50, "roundness": 0, "feather": 50, "highlights": 0},
    "grain": {"amount": 0, "size": 25, "roughness": 50},
    # --- Local adjustments ---
    "masks": [],
}

# Adjustments a mask can carry (all default 0).
LOCAL_KEYS = [
    "exposure", "contrast", "highlights", "shadows", "whites", "blacks", "temp", "tint",
    "texture", "clarity", "dehaze", "saturation", "vibrance", "hue", "sharpness", "noise",
]


def _merge(base: Any, over: Any) -> Any:
    """Deep merge that never shares structure with `base` (DEFAULTS must stay pristine)."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = copy.deepcopy(base)
        for k, v in over.items():
            out[k] = _merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def full(stored: dict | str | None) -> dict:
    if isinstance(stored, str):
        stored = json.loads(stored) if stored else {}
    return _merge(DEFAULTS, stored or {})


def diff(params: dict) -> dict:
    """Only the keys that differ from DEFAULTS (keeps stored JSON small)."""

    def d(base, cur):
        if isinstance(base, dict) and isinstance(cur, dict):
            out = {}
            for k, v in cur.items():
                if k not in base:
                    out[k] = v
                    continue
                sub = d(base[k], v)
                if sub is not _SAME:
                    out[k] = sub
            return out if out else _SAME
        return _SAME if base == cur else cur

    r = d(DEFAULTS, params)
    return {} if r is _SAME else r


_SAME = object()


def is_default(params: dict) -> bool:
    return diff(params) == {}


def apply_partial(params: dict, partial: dict) -> dict:
    """Apply a preset / pasted settings (partial dict) on top of params."""
    return _merge(params, partial)


GROUPS = {
    "basic": ["profile", "profile_amount", "exposure", "contrast", "highlights", "shadows", "whites",
              "blacks", "texture", "clarity", "dehaze", "vibrance", "saturation"],
    "wb": ["wb", "temp", "tint"],
    "curve": ["curve", "curve_r", "curve_g", "curve_b", "parametric"],
    "color": ["hsl", "bw", "bw_mix", "grading"],
    "detail": ["sharpen", "nr", "ai_denoise"],
    "optics": ["lens"],
    "geometry": ["transform", "crop", "rotate90", "flip"],
    "effects": ["vignette", "grain"],
    "masks": ["masks"],
}


def pick_groups(params: dict, groups: list[str]) -> dict:
    keys = [k for g in groups for k in GROUPS.get(g, [])]
    return {k: copy.deepcopy(params[k]) for k in keys if k in params}
