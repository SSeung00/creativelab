"""AI model files: downloaded once (from the authors' official releases) into data/models.

Only weights are downloaded; no remote code is executed.
"""

from __future__ import annotations

import os
import threading
import urllib.request
from pathlib import Path

from app.config import DATA_DIR

MODEL_DIR = DATA_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
HF_DIR = MODEL_DIR / "hf"
os.environ.setdefault("HF_HOME", str(HF_DIR))

FILES = {
    # Subject (salient object) segmentation — IS-Net general use, via the rembg project releases.
    "isnet": ("isnet-general-use.onnx", "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx"),
    # Real-image denoiser by the SCUNet authors (KAIR releases).
    "scunet": ("scunet_color_real_psnr.pth", "https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_psnr.pth"),
    # Face landmarks + blink blendshapes (Google MediaPipe).
    "face": ("face_landmarker.task", "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"),
}
SEGFORMER = "nvidia/segformer-b5-finetuned-ade-640-640"  # native transformers architecture

_lock = threading.Lock()


def path(key: str, progress=None) -> Path:
    name, url = FILES[key]
    dest = MODEL_DIR / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    with _lock:
        if dest.exists():
            return dest
        tmp = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": "PhotoLab"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        tmp.replace(dest)
    return dest


def status() -> dict:
    out = {}
    for key, (name, _url) in FILES.items():
        f = MODEL_DIR / name
        out[key] = {"ready": f.exists(), "mb": round(f.stat().st_size / 1e6, 1) if f.exists() else None}
    out["segformer"] = {"ready": any(HF_DIR.rglob("*.safetensors")) if HF_DIR.exists() else False}
    return {"models": out}
