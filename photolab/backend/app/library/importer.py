"""SD-card import: copy (never move) new photos into dated folders, optionally to a backup too."""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path

from app.config import IMAGE_EXTS
from app.jobs import JobContext
from app.library.exif import read_exif

VIDEO_EXTS = {".mp4", ".mov", ".mts", ".m2ts"}


def find_cards() -> list[dict]:
    """Removable drives that look like camera cards (have a DCIM folder)."""
    out = []
    for d in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{d}:\\")
        try:
            if (root / "DCIM").is_dir():
                n = sum(1 for _ in _files(root / "DCIM", False))
                out.append({"path": str(root), "dcim": str(root / "DCIM"), "count": n})
        except OSError:
            continue
    return out


def _files(src: Path, videos: bool):
    for dirpath, _dirs, files in os.walk(src):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTS or (videos and ext in VIDEO_EXTS):
                yield Path(dirpath) / f


def _date_of(p: Path) -> datetime:
    if p.suffix.lower() not in VIDEO_EXTS:
        try:
            t = read_exif(p).get("capture_time")
            if t:
                return t
        except Exception:
            pass
    return datetime.fromtimestamp(p.stat().st_mtime)


def _same(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    ha, hb = hashlib.blake2b(digest_size=16), hashlib.blake2b(digest_size=16)
    with open(a, "rb") as fa, open(b, "rb") as fb:
        ha.update(fa.read(1 << 20))
        hb.update(fb.read(1 << 20))
    return ha.digest() == hb.digest()


def plan(src: Path, dest_root: Path, pattern: str, videos: bool) -> list[tuple[Path, Path, bool]]:
    """[(source, destination, already_there)]"""
    out = []
    for f in _files(src, videos):
        t = _date_of(f)
        sub = t.strftime(pattern)
        dst = dest_root / sub / f.name
        out.append((f, dst, dst.exists() and _same(f, dst)))
    return out


def run_import(src: str, dest_root: str, pattern: str, backup_root: str | None, videos: bool,
               ctx: JobContext) -> str:
    items = plan(Path(src), Path(dest_root), pattern, videos)
    todo = [(s, d) for s, d, there in items if not there]
    total_bytes = sum(s.stat().st_size for s, _ in todo) or 1
    done_bytes = 0
    copied = 0
    for i, (s, d) in enumerate(todo):
        if ctx.cancelled:
            break
        ctx.progress(i, len(todo), f"{s.name} 복사 중… ({i + 1}/{len(todo)}, {done_bytes / total_bytes:.0%})")
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():  # same name, different file -> keep both
            d = d.with_name(f"{d.stem}_{datetime.now():%H%M%S}{d.suffix}")
        tmp = d.with_suffix(d.suffix + ".part")
        shutil.copy2(s, tmp)
        if tmp.stat().st_size != s.stat().st_size:
            tmp.unlink(missing_ok=True)
            raise OSError(f"복사 확인 실패: {s}")
        tmp.replace(d)
        if backup_root:
            b = Path(backup_root) / d.relative_to(dest_root)
            if not (b.exists() and _same(s, b)):
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, b)
        done_bytes += s.stat().st_size
        copied += 1
    ctx.progress(len(todo), len(todo), "완료", force=True)
    skipped = len(items) - len(todo)
    return f"{copied:,}장 복사" + (f", 이미 있는 {skipped:,}장 건너뜀" if skipped else "")
