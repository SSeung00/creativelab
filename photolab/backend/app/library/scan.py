"""Index photos under a root folder (read only: nothing is written next to the originals)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from app.config import IMAGE_EXTS, JPEG_EXTS, RAW_EXTS
from app.db import engine
from app.jobs import JobContext
from app.library.exif import read_exif
from app.models import Photo, Root

SKIP_DIR_SUFFIXES = (".lrdata", ".lrcat-data")


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and not d.endswith(SKIP_DIR_SUFFIXES) and d not in ("$RECYCLE.BIN",)
        )
        rel = Path(dirpath).relative_to(root).as_posix()
        yield ("" if rel == "." else rel), filenames


def _group(filenames: list[str]) -> list[tuple[str, str | None]]:
    """[(master filename, paired camera JPEG or None)] — RAW wins over a same-named JPEG."""
    by_stem: dict[str, list[str]] = {}
    for f in filenames:
        ext = os.path.splitext(f)[1].lower()
        if ext in IMAGE_EXTS:
            by_stem.setdefault(os.path.splitext(f)[0].lower(), []).append(f)
    out = []
    for files in by_stem.values():
        raws = [f for f in files if os.path.splitext(f)[1].lower() in RAW_EXTS]
        jpgs = [f for f in files if os.path.splitext(f)[1].lower() in JPEG_EXTS]
        if raws:
            out.append((raws[0], jpgs[0] if jpgs else None))
        else:
            out.extend((f, None) for f in files)
    return sorted(out)


def scan_root(root_id: int, ctx: JobContext | None = None) -> str:
    with Session(engine) as s:
        root = s.get(Root, root_id)
        assert root is not None
        base = Path(root.path)
        existing = {(p.folder, p.filename): p for p in s.exec(select(Photo).where(Photo.root_id == root_id, Photo.master_id == None))}  # noqa: E711

    found: list[tuple[str, str, str | None]] = []
    for folder, files in _walk(base):
        for master, pair in _group(files):
            found.append((folder, master, pair))
        if ctx:
            ctx.progress(0, 0, f"폴더 살펴보는 중… {len(found):,}장")

    todo: list[tuple[str, str, str | None, os.stat_result]] = []
    unchanged = 0
    seen = set()
    for folder, name, pair in found:
        seen.add((folder, name))
        full = base / folder / name if folder else base / name
        try:
            st = full.stat()
        except OSError:
            continue
        p = existing.get((folder, name))
        if p and p.size == st.st_size and abs(p.mtime - st.st_mtime) < 1 and p.pair_jpg == pair and not p.missing:
            unchanged += 1
            continue
        todo.append((folder, name, pair, st))

    total = len(todo)
    added = updated = 0

    def meta(item):
        folder, name, _pair, _st = item
        full = base / folder / name if folder else base / name
        try:
            return read_exif(full)
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=8) as pool, Session(engine) as s:
        for i, (item, info) in enumerate(zip(todo, pool.map(meta, todo), strict=True)):
            folder, name, pair, st = item
            p = existing.get((folder, name))
            if p is None:
                p = Photo(root_id=root_id, folder=folder, filename=name, ext=os.path.splitext(name)[1].lower().lstrip("."))
                added += 1
            else:
                p = s.get(Photo, p.id)
                updated += 1
            p.size, p.mtime, p.pair_jpg, p.missing = st.st_size, st.st_mtime, pair, False
            for k, v in info.items():
                setattr(p, k, v)
            s.add(p)
            if i % 200 == 199:
                s.commit()
            if ctx:
                ctx.progress(i + 1, total, f"사진 정보 읽는 중… {i + 1:,}/{total:,}")
                if ctx.cancelled:
                    break
        missing = 0
        for key, p in existing.items():
            if key not in seen and not p.missing:
                row = s.get(Photo, p.id)
                row.missing = True
                s.add(row)
                missing += 1
        root = s.get(Root, root_id)
        root.scanned_at = datetime.now()
        s.add(root)
        s.commit()
    return f"새 사진 {added:,}장, 변경 {updated:,}장, 그대로 {unchanged:,}장" + (f", 사라짐 {missing:,}장" if missing else "")
