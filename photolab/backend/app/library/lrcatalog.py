"""Import ratings, flags, labels and develop settings from a Lightroom Classic catalog.

The catalog is copied to a temp file and opened read-only. Photos are matched by capture
time (to the millisecond) and camera, so renamed/moved copies of the originals still match.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from app.config import DATA_DIR
from app.db import engine
from app.engine.lrimport import lr_to_params, parse_lr_settings
from app.engine.params import diff
from app.jobs import JobContext
from app.models import Edit, HistoryStep, Photo

LABELS = {"Red": "red", "Yellow": "yellow", "Green": "green", "Blue": "blue", "Purple": "purple",
          "빨강": "red", "노랑": "yellow", "초록": "green", "파랑": "blue", "보라": "purple"}


def _copy_catalog(path: Path) -> Path:
    tmp = DATA_DIR / "tmp"
    tmp.mkdir(exist_ok=True)
    dst = tmp / "import.lrcat"
    for suffix in ("", "-wal"):
        s = Path(str(path) + suffix)
        d = Path(str(dst) + suffix)
        if s.exists():
            shutil.copy2(s, d)
        elif d.exists():
            d.unlink()
    return dst


def _key(t: datetime | str | None) -> str | None:
    if t is None:
        return None
    if isinstance(t, str):
        t = t.replace("Z", "")
        try:
            t = datetime.fromisoformat(t[:23])
        except ValueError:
            return None
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}"


def read_catalog(path: Path) -> list[dict]:
    db = _copy_catalog(path)
    c = sqlite3.connect(db)
    rows = c.execute("""
        select i.captureTime, f.baseName, i.rating, i.pick, i.colorLabels, s.text, s.hasDevelopAdjustmentsEx
        from Adobe_images i
        join AgLibraryFile f on f.id_local = i.rootFile
        left join Adobe_imageDevelopSettings s on s.image = i.id_local
        where i.masterImage is null
    """).fetchall()
    c.close()
    return [{"time": r[0], "name": r[1], "rating": int(r[2] or 0), "pick": int(r[3] or 0),
             "label": LABELS.get(r[4] or ""), "text": r[5], "edited": (r[6] or 0) > 0} for r in rows]


def preview(path: Path, root_id: int | None = None) -> dict:
    items = read_catalog(path)
    with Session(engine) as s:
        st = select(Photo.capture_time).where(Photo.master_id == None)  # noqa: E711
        if root_id:
            st = st.where(Photo.root_id == root_id)
        ours = {_key(t) for t in s.exec(st) if t}
    matched = [it for it in items if _key(it["time"]) in ours]
    return {
        "catalog_photos": len(items),
        "matched": len(matched),
        "edited": sum(1 for it in matched if it["edited"]),
        "rated": sum(1 for it in matched if it["rating"]),
        "picked": sum(1 for it in matched if it["pick"] == 1),
        "rejected": sum(1 for it in matched if it["pick"] == -1),
    }


def run_import(path: Path, root_id: int | None, do_edits: bool, do_ratings: bool, overwrite: bool,
               ctx: JobContext) -> str:
    items = read_catalog(path)
    by_time: dict[str, list[dict]] = {}
    for it in items:
        k = _key(it["time"])
        if k:
            by_time.setdefault(k, []).append(it)
    n_edit = n_meta = 0
    with Session(engine) as s:
        st = select(Photo).where(Photo.master_id == None)  # noqa: E711
        if root_id:
            st = st.where(Photo.root_id == root_id)
        photos = list(s.exec(st))
        for i, p in enumerate(photos):
            ctx.progress(i, len(photos), f"라이트룸 설정 옮기는 중… {i + 1:,}/{len(photos):,}")
            cands = by_time.get(_key(p.capture_time) or "", [])
            if not cands:
                continue
            it = cands[0]
            if len(cands) > 1:  # burst within the same millisecond: use the sequence number
                tail = Path(p.filename).stem[-5:]
                it = next((c for c in cands if c["name"].endswith(tail)), cands[0])
            if do_ratings:
                p.rating = it["rating"]
                p.flag = 1 if it["pick"] == 1 else (-1 if it["pick"] == -1 else 0)
                if it["label"]:
                    p.color_label = it["label"]
                n_meta += 1
            if do_edits and it["text"] and it["edited"]:  # untouched LR photos = our defaults
                existing = s.get(Edit, p.id)
                if existing is None or overwrite:
                    prm = lr_to_params(parse_lr_settings(it["text"]))
                    d = diff(prm)
                    if d:
                        txt = json.dumps(d, ensure_ascii=False)
                        if existing:
                            existing.params = txt
                            s.add(existing)
                        else:
                            s.add(Edit(photo_id=p.id, params=txt))
                        s.add(HistoryStep(photo_id=p.id, label="라이트룸에서 가져옴", params=txt))
                        p.edited = True
                        p.edit_rev += 1
                        n_edit += 1
            p.lr_imported = True
            s.add(p)
            if i % 300 == 299:
                s.commit()
        s.commit()
    ctx.progress(1, 1, "완료", force=True)
    return f"편집 {n_edit:,}장, 별점/플래그 {n_meta:,}장 가져옴"
