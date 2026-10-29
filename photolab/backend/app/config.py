import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PHOTOLAB_DATA_DIR") or ROOT_DIR / "data")
DB_PATH = DATA_DIR / "photolab.db"
CACHE_DIR = Path(os.environ.get("PHOTOLAB_CACHE_DIR") or DATA_DIR / "cache")
THUMB_DIR = CACHE_DIR / "thumbs"
PREVIEW_DIR = CACHE_DIR / "previews"
EXPORT_DIR = Path(os.environ.get("PHOTOLAB_EXPORT_DIR") or DATA_DIR / "exports")

FRONTEND_DIST = (ROOT_DIR / "frontend" / "dist").resolve()

PORT = int(os.environ.get("PHOTOLAB_PORT") or 8002)
FRONTEND_ORIGINS = ["http://localhost:5175", "http://127.0.0.1:5175"]

# Adobe Camera Raw support files (camera/lens profiles, looks) from Lightroom / ACR / DNG Converter, read only.
# Without Adobe installed (e.g. on Linux), copy a private archive of that folder to data/adobe-camera-raw.
# Never commit these files: they are Adobe's, licensed for personal use only.
def _adobe_dir() -> Path:
    if env := os.environ.get("PHOTOLAB_ADOBE_DIR"):
        return Path(env)
    installed = Path(r"C:\ProgramData\Adobe\CameraRaw")
    return installed if installed.is_dir() else DATA_DIR / "adobe-camera-raw"


ADOBE_CR_DIR = _adobe_dir()

RAW_EXTS = {".arw", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2"}
JPEG_EXTS = {".jpg", ".jpeg"}
IMAGE_EXTS = RAW_EXTS | JPEG_EXTS | {".tif", ".tiff", ".png", ".heic", ".hif"}

THUMB_SIZE = 480
for d in (DATA_DIR, CACHE_DIR, THUMB_DIR, PREVIEW_DIR, EXPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)
