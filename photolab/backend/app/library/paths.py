from pathlib import Path

from sqlmodel import Session

from app.models import Photo, Root

_root_cache: dict[int, str] = {}


def root_path(session: Session, root_id: int) -> Path:
    p = _root_cache.get(root_id)
    if p is None:
        root = session.get(Root, root_id)
        if root is None:
            raise KeyError(root_id)
        p = _root_cache[root_id] = root.path
    return Path(p)


def photo_path(session: Session, photo: Photo) -> Path:
    base = root_path(session, photo.root_id)
    return base / photo.folder / photo.filename if photo.folder else base / photo.filename


def pair_path(session: Session, photo: Photo) -> Path | None:
    if not photo.pair_jpg:
        return None
    base = root_path(session, photo.root_id)
    return base / photo.folder / photo.pair_jpg if photo.folder else base / photo.pair_jpg


def forget_root(root_id: int) -> None:
    _root_cache.pop(root_id, None)
