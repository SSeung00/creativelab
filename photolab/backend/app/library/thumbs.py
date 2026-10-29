"""Thumbnails and quick previews from the JPEG embedded in each RAW (no RAW decode needed)."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import rawpy
from PIL import Image, ImageOps
from sqlmodel import Session

from app.config import JPEG_EXTS, PREVIEW_DIR, THUMB_DIR, THUMB_SIZE
from app.library.paths import pair_path, photo_path
from app.models import Photo

_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def orient(im: Image.Image, orientation: int) -> Image.Image:
    t = _TRANSPOSE.get(orientation or 1)
    return im.transpose(t) if t else im


def embedded_jpeg(session: Session, photo: Photo) -> tuple[bytes, bool]:
    """(JPEG bytes, already_oriented). Prefers the RAW's embedded preview, else the file itself."""
    path = photo_path(session, photo)
    if path.suffix.lower() in JPEG_EXTS:
        return path.read_bytes(), False
    try:
        with rawpy.imread(str(path)) as raw:
            th = raw.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                return bytes(th.data), False
            buf = io.BytesIO()
            Image.fromarray(th.data).save(buf, "JPEG", quality=90)
            return buf.getvalue(), False
    except Exception:
        pair = pair_path(session, photo)
        if pair and pair.exists():
            return pair.read_bytes(), False
        raise


def _open_oriented(session: Session, photo: Photo, max_side: int) -> Image.Image:
    data, oriented = embedded_jpeg(session, photo)
    im = Image.open(io.BytesIO(data))
    im.draft("RGB", (max_side, max_side))
    im = im.convert("RGB")
    if photo.ext in ("jpg", "jpeg"):
        im = ImageOps.exif_transpose(im)
    elif not oriented:
        im = orient(im, photo.orientation)
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return im


def thumb_file(photo_id: int, rev: int = 0) -> Path:
    sub = THUMB_DIR / f"{photo_id // 1000:04d}"
    return sub / (f"{photo_id}.jpg" if rev == 0 else f"{photo_id}_r{rev}.jpg")


def get_thumb(session: Session, photo: Photo) -> Path:
    """Embedded-preview thumbnail; edited photos get a rendered one (see engine.render)."""
    if photo.edited and photo.edit_rev:
        f = thumb_file(photo.id, photo.edit_rev)
        if f.exists():
            return f
        from app.engine.render import render_thumb

        try:
            return render_thumb(session, photo, f)
        except Exception:
            import traceback

            traceback.print_exc()
    f = thumb_file(photo.id)
    if f.exists():
        return f
    with _lock_for(str(f)):
        if f.exists():
            return f
        im = _open_oriented(session, photo, THUMB_SIZE)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=82, optimize=True)
        tmp.replace(f)
    return f


def get_quick_preview(session: Session, photo: Photo, max_side: int = 2560) -> Path:
    f = PREVIEW_DIR / f"{photo.id // 1000:04d}" / f"{photo.id}_emb.jpg"
    if f.exists():
        return f
    with _lock_for(str(f)):
        if f.exists():
            return f
        im = _open_oriented(session, photo, max_side)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=88)
        tmp.replace(f)
    return f


def prefetch(photo_ids: list[int], ctx=None, workers: int = 6) -> int:
    """Build missing embedded-preview thumbnails in the background."""
    from concurrent.futures import ThreadPoolExecutor

    from app.db import engine

    todo = [pid for pid in photo_ids if not thumb_file(pid).exists()]

    def one(pid: int):
        with Session(engine) as s:
            p = s.get(Photo, pid)
            if p is None:
                return
            try:
                get_thumb(s, p)
            except Exception:
                pass

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(one, todo):
            done += 1
            if ctx:
                ctx.progress(done, len(todo), f"썸네일 만드는 중… {done:,}/{len(todo):,}")
                if ctx.cancelled:
                    break
    return done
