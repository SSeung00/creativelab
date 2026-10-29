from datetime import datetime

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str | None = None
    updated_at: NaiveDatetime = Field(default_factory=datetime.now)


class Root(SQLModel, table=True):
    """A folder the user added. Photos are referenced in place and never modified."""

    id: int | None = Field(default=None, primary_key=True)
    path: str = Field(unique=True)
    name: str
    added_at: NaiveDatetime = Field(default_factory=datetime.now)
    scanned_at: NaiveDatetime | None = None


class Photo(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    root_id: int = Field(foreign_key="root.id", index=True)
    folder: str = Field(index=True)  # path relative to the root, "/" separated, "" for the root
    filename: str
    ext: str
    size: int = 0
    mtime: float = 0
    pair_jpg: str | None = None  # camera JPEG shot alongside the RAW (same folder)

    capture_time: NaiveDatetime | None = Field(default=None, index=True)
    width: int | None = None
    height: int | None = None
    orientation: int = 1  # EXIF orientation of the original
    make: str | None = None
    model: str | None = None
    lens: str | None = None
    focal_length: float | None = None
    aperture: float | None = None
    shutter: str | None = None
    iso: int | None = None
    exposure_bias: float | None = None

    rating: int = Field(default=0, index=True)  # 0..5
    flag: int = Field(default=0, index=True)  # -1 reject, 0 none, 1 pick
    color_label: str | None = None

    # Virtual copies share the original file; master_id points at the first copy.
    master_id: int | None = Field(default=None, foreign_key="photo.id")
    copy_name: str | None = None

    edited: bool = Field(default=False, index=True)
    edit_rev: int = 0  # bumped on every edit save -> cache key for rendered thumbnails
    lr_imported: bool = False
    missing: bool = False
    added_at: NaiveDatetime = Field(default_factory=datetime.now)


class Edit(SQLModel, table=True):
    photo_id: int = Field(primary_key=True, foreign_key="photo.id")
    params: str  # JSON (see engine/params.py)
    updated_at: NaiveDatetime = Field(default_factory=datetime.now)


class HistoryStep(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    photo_id: int = Field(foreign_key="photo.id", index=True)
    label: str
    params: str
    created_at: NaiveDatetime = Field(default_factory=datetime.now)


class Snapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    photo_id: int = Field(foreign_key="photo.id", index=True)
    name: str
    params: str
    created_at: NaiveDatetime = Field(default_factory=datetime.now)


class Preset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    group: str = "사용자"
    params: str  # partial params JSON: only the keys the preset sets
    created_at: NaiveDatetime = Field(default_factory=datetime.now)


class Job(SQLModel, table=True):
    """Background work (scan, import, export, AI) with progress for the UI."""

    id: int | None = Field(default=None, primary_key=True)
    kind: str
    title: str
    status: str = "queued"  # queued | running | done | error | cancelled
    done: int = 0
    total: int = 0
    message: str | None = None
    result: str | None = None
    created_at: NaiveDatetime = Field(default_factory=datetime.now)
    finished_at: NaiveDatetime | None = None


class AiScore(SQLModel, table=True):
    """Culling helpers computed by the AI job (all from the embedded preview)."""

    photo_id: int = Field(primary_key=True, foreign_key="photo.id")
    sharpness: float = 0.0  # log10 of focus-area Laplacian variance
    sharp_rel: float = 0.0  # relative to the burst/folder median (stops-ish, <0 = softer)
    blurry: bool = Field(default=False, index=True)
    faces: int = 0
    eyes_closed: bool = Field(default=False, index=True)
    clipped_hi: float = 0.0
    clipped_lo: float = 0.0
    group_id: int | None = Field(default=None, index=True)
    group_size: int = 1
    best_in_group: bool = Field(default=False, index=True)
    score: float = 0.0
    scored_at: NaiveDatetime = Field(default_factory=datetime.now)
