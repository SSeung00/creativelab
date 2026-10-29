"""Read-only access to the Adobe Camera Raw support files installed on this PC.

- Camera profiles (.dcp): Adobe Standard + camera matching (Camera ST, VV, ...).
- Look profiles (.xmp): Adobe Color / Vivid / Landscape ... = a base DCP + look table + tone curve.
- Lens profiles (.lcp): distortion + vignetting models per focal length / aperture.

Everything is cached in memory; nothing is written to the Adobe folders.
"""

from __future__ import annotations

import functools
import html
import json
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.config import ADOBE_CR_DIR
from app.engine.dcp import CameraProfile, HSVMap, load_dcp

DATA = Path(__file__).parent / "data"

# ---------- default ACR tone curve (used when an Adobe profile carries none) ----------


@functools.cache
def acr3_curve() -> np.ndarray:
    return np.array(json.loads((DATA / "acr3_curve.json").read_text()), dtype=np.float32)


# ---------- camera profiles ----------

def unique_model(make: str | None, model: str | None) -> str:
    make = (make or "").strip().upper()
    model = (model or "").strip()
    pretty = {"SONY": "Sony", "NIKON CORPORATION": "Nikon", "CANON": "Canon", "FUJIFILM": "Fujifilm"}.get(make, make.title())
    return f"{pretty} {model}".strip()


@functools.cache
def _dcp_files() -> list[str]:
    base = ADOBE_CR_DIR / "CameraProfiles"
    return [str(p) for p in base.rglob("*.dcp")] if base.exists() else []


@functools.cache
def load_profile(path: str) -> CameraProfile:
    return load_dcp(path)


@functools.cache
def _camera_profiles(unique: str) -> tuple[tuple[str, str], ...]:
    # DCP file names carry the camera name ("Sony ILCE-7M4 Camera ST.dcp"): filter on that,
    # then confirm with the profile's UniqueCameraModel tag.
    model = unique.split(" ", 1)[-1].lower()
    out = []
    for p in _dcp_files():
        if model not in Path(p).name.lower():
            continue
        try:
            prof = load_profile(p)
        except Exception:
            continue
        if prof.camera_model == unique:
            out.append((prof.name, p))
    return tuple(sorted(out))


def camera_profiles(make: str | None, model: str | None) -> dict[str, str]:
    """Profile name -> DCP path for a camera."""
    return dict(_camera_profiles(unique_model(make, model)))


# ---------- look profiles (Adobe Color etc.) ----------

_ENC = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?`'|()[]{}@%$#"
_DEC = {c: i for i, c in enumerate(_ENC)}


def decode_big_table(s: str) -> bytes:
    """Adobe 'big table' string -> raw bytes (base-85 variant + zlib, as in the DNG SDK)."""
    s = "".join(ch for ch in s if ch in _DEC)
    out = bytearray()
    for i in range(0, len(s), 5):
        v, mul = 0, 1
        for ch in s[i:i + 5]:
            v += _DEC[ch] * mul
            mul *= 85
        out += struct.pack("<I", v & 0xFFFFFFFF)
    size = struct.unpack("<I", out[:4])[0]
    data = zlib.decompress(bytes(out[4:]))
    return data[:size]


def _parse_look_table(data: bytes) -> HSVMap | None:
    ints = struct.unpack("<5I", data[:20])
    hue, sat, val = ints[2], ints[3], ints[4]
    n = hue * sat * val * 3
    if hue == 0 or sat == 0 or 20 + n * 4 > len(data):
        return None
    arr = np.frombuffer(data[20:20 + n * 4], dtype="<f4").reshape(val, hue, sat, 3).copy()
    rest = data[20 + n * 4:]
    enc = struct.unpack("<I", rest[:4])[0] if len(rest) >= 4 else 0
    return HSVMap(hue, sat, val, arr, srgb_encoded_vals=enc == 1)


def _parse_rgb_table(data: bytes):
    """3D RGB LUT of the creative profiles (Artistic, Modern, ...).

    Layout: type, version, dims(3), size, then size^3 x 3 int16 deltas from identity.
    The exact semantics are not public; these profiles are not offered until verified.
    """
    try:
        _typ, _ver, dims, size = struct.unpack("<4I", data[:16])
        if dims != 3 or not 2 <= size <= 64:
            return None
        n = size ** 3 * 3
        deltas = np.frombuffer(data[16:16 + n * 2], dtype="<i2").astype(np.float32) / 65535.0
        grid = np.linspace(0, 1, size, dtype=np.float32)
        ident = np.stack(np.meshgrid(grid, grid, grid, indexing="ij"), -1)
        return size, ident + deltas.reshape(size, size, size, 3)
    except Exception:
        return None


@dataclass
class Look:
    name: str
    group: str
    base_profile: str  # usually "Adobe Standard"
    path: str
    look_table: HSVMap | None = None
    rgb_table: tuple | None = None
    tone_curve: list[tuple[float, float]] | None = None  # ToneCurvePV2012 (0..255)
    tone_curve_rgb: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    settings: dict[str, float] = field(default_factory=dict)  # other crs:* numeric parameters
    amount_supported: bool = False
    monochrome: bool = False
    camera_restriction: str = ""


_NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
}
_CRS = "{" + _NS["crs"] + "}"


def _seq_points(desc: ET.Element, tag: str) -> list[tuple[float, float]] | None:
    el = desc.find(f"crs:{tag}", _NS)
    if el is None:
        return None
    pts = []
    for li in el.iter("{" + _NS["rdf"] + "}li"):
        a, b = (li.text or "0,0").split(",")
        pts.append((float(a), float(b)))
    return pts or None


def _alt_text(desc: ET.Element, tag: str) -> str | None:
    el = desc.find(f"crs:{tag}", _NS)
    if el is None:
        return desc.get(_CRS + tag)
    li = el.find(".//rdf:li", _NS)
    return li.text if li is not None else None


@functools.cache
def load_look(path: str) -> Look | None:
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    desc = root.find(".//rdf:Description", _NS)
    if desc is None or desc.get(_CRS + "PresetType") != "Look":
        return None
    name = _alt_text(desc, "Name") or Path(path).stem
    group = _alt_text(desc, "Group") or Path(path).parent.name
    look = Look(
        name=name,
        group=group,
        base_profile=desc.get(_CRS + "CameraProfile") or "Adobe Standard",
        path=path,
        amount_supported=desc.get(_CRS + "SupportsAmount") == "True",
        monochrome=desc.get(_CRS + "ConvertToGrayscale") == "True",
        camera_restriction=desc.get(_CRS + "CameraModelRestriction") or "",
    )
    tables = {k[len(_CRS) + 6:]: v for k, v in desc.attrib.items() if k.startswith(_CRS + "Table_")}
    lt = desc.get(_CRS + "LookTable")
    if lt and lt in tables:
        look.look_table = _parse_look_table(decode_big_table(tables[lt]))
    rt = desc.get(_CRS + "RGBTable")
    if rt and rt in tables:
        look.rgb_table = _parse_rgb_table(decode_big_table(tables[rt]))
    look.tone_curve = _seq_points(desc, "ToneCurvePV2012")
    for ch in ("Red", "Green", "Blue"):
        pts = _seq_points(desc, f"ToneCurvePV2012{ch}")
        if pts:
            look.tone_curve_rgb[ch.lower()] = pts
    skip = {"Version", "ProcessVersion", "UUID", "LookTable", "RGBTable", "CameraProfile", "PresetType"}
    for k, v in desc.attrib.items():
        if not k.startswith(_CRS):
            continue
        key = k[len(_CRS):]
        if key in skip or key.startswith("Table_"):
            continue
        try:
            look.settings[key] = float(v)
        except ValueError:
            pass
    return look


@functools.cache
def _look_index() -> dict[str, tuple[str, str]]:
    """Look name -> (group, path) for generic looks we can render (no RGB-table creative looks yet).

    Only the XMP header is scanned here; tables are decoded on first use.
    """
    base = ADOBE_CR_DIR / "Settings" / "Adobe" / "Profiles"
    out: dict[str, tuple[str, str]] = {}
    if not base.exists():
        return out
    for p in sorted(base.rglob("*.xmp")):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if 'crs:PresetType="Look"' not in text or "crs:RGBTable=" in text:
            continue
        if re.search(r'crs:CameraModelRestriction="[^"]+"', text):
            continue
        m = re.search(r"<crs:Name>\s*<rdf:Alt>\s*<rdf:li[^>]*>([^<]*)<", text)
        g = re.search(r"<crs:Group>\s*<rdf:Alt>\s*<rdf:li[^>]*>([^<]*)<", text)
        name = html.unescape(m.group(1)) if m else p.stem
        out.setdefault(name, (html.unescape(g.group(1)) if g else p.parent.name, str(p)))
    return out


def list_looks() -> list[tuple[str, str]]:
    """[(name, group)] of selectable looks."""
    return [(n, g) for n, (g, _p) in _look_index().items()]


def find_look(name: str) -> Look | None:
    entry = _look_index().get(name)
    return load_look(entry[1]) if entry else None


# ---------- lens profiles ----------

@dataclass
class LensSample:
    focal: float
    aperture_value: float  # APEX Av = 2*log2(N)
    focus: float
    k: tuple[float, float, float]
    scale: float
    vignette: tuple[float, float, float] | None


@dataclass
class LensProfile:
    name: str
    path: str
    samples: list[LensSample]


_ST = "{http://ns.adobe.com/photoshop/1.0/camera-profile}"


@functools.cache
def load_lcp(path: str) -> LensProfile | None:
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    samples = []
    name = ""
    for d in root.iter("{" + _NS["rdf"] + "}Description"):
        if d.get(_ST + "FocalLength") is None:
            continue
        if d.get(_ST + "CameraRawProfile") == "False":
            continue
        name = d.get(_ST + "ProfileName") or name
        pm = d.find(_ST + "PerspectiveModel")
        if pm is None:
            continue
        # Attributes sit on the element, or on a nested rdf:Description when it has children.
        inner = pm.find("{" + _NS["rdf"] + "}Description")
        if pm.get(_ST + "RadialDistortParam1") is None and inner is not None:
            pm = inner
        k = tuple(float(pm.get(_ST + f"RadialDistortParam{i}") or 0) for i in (1, 2, 3))
        vm = pm.find(".//" + _ST + "VignetteModel")
        vig = None
        if vm is not None:
            vig = tuple(float(vm.get(_ST + f"VignetteModelParam{i}") or 0) for i in (1, 2, 3))
        samples.append(LensSample(
            focal=float(d.get(_ST + "FocalLength")),
            aperture_value=float(d.get(_ST + "ApertureValue") or 0),
            focus=float(d.get(_ST + "FocusDistance") or 10000),
            k=k,  # type: ignore[arg-type]
            scale=float(pm.get(_ST + "ScaleFactor") or 1),
            vignette=vig,  # type: ignore[arg-type]
        ))
    return LensProfile(name, path, samples) if samples else None


@functools.cache
def _lcp_files() -> list[str]:
    base = ADOBE_CR_DIR / "LensProfiles"
    return [str(p) for p in base.rglob("*.lcp")] if base.exists() else []


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", s.lower()).strip()


@functools.cache
def find_lens_profile(lens: str | None, make: str | None = None) -> LensProfile | None:
    """Adobe RAW lens profile for an EXIF lens name (file names carry the lens name)."""
    if not lens:
        return None
    key = _norm(lens)
    raw_files = [p for p in _lcp_files() if p.lower().endswith("raw.lcp")]
    cands = [p for p in raw_files if key in _norm(Path(p).stem)]
    if make:
        own = [p for p in cands if Path(p).name.upper().startswith(make.strip().upper())]
        cands = own or cands
    for p in sorted(cands, key=len):
        head = Path(p).read_text(encoding="utf-8", errors="ignore")[:6000]
        m = re.search(r'stCamera:Lens="([^"]*)"', head)
        if m and _norm(m.group(1)) == key:
            return load_lcp(p)
    return load_lcp(sorted(cands, key=len)[0]) if cands else None


def lens_params(prof: LensProfile, focal: float | None, aperture: float | None):
    """Interpolated (k1,k2,k3, scale, vignette params or None) for a shot."""
    import math

    f = focal or prof.samples[0].focal
    av = 2 * math.log2(aperture) if aperture else None

    def interp_focal(items, getter):
        fs = sorted({s.focal for s in items})
        lo = max([x for x in fs if x <= f], default=fs[0])
        hi = min([x for x in fs if x >= f], default=fs[-1])
        t = 0.0 if hi == lo else (1 / f - 1 / lo) / (1 / hi - 1 / lo)

        def at(fl):
            cand = [s for s in items if s.focal == fl]
            # nearest focus distance = the farthest (landscape) one; nearest aperture
            far = max(c.focus for c in cand)
            cand = [c for c in cand if c.focus == far]
            if av is not None:
                cand.sort(key=lambda c: abs(c.aperture_value - av))
                # interpolate between the two nearest apertures
                if len(cand) > 1 and (cand[0].aperture_value - av) * (cand[1].aperture_value - av) < 0:
                    a0, a1 = cand[0], cand[1]
                    w = (av - a0.aperture_value) / (a1.aperture_value - a0.aperture_value)
                    return np.array(getter(a0)) * (1 - w) + np.array(getter(a1)) * w
            return np.array(getter(cand[0]))

        return at(lo) * (1 - t) + at(hi) * t

    dist = interp_focal(prof.samples, lambda s: (*s.k, s.scale))
    vig_items = [s for s in prof.samples if s.vignette]
    vig = interp_focal(vig_items, lambda s: s.vignette) if vig_items else None
    return tuple(dist[:3]), float(dist[3]), (tuple(vig) if vig is not None else None)
