"""Pickle cache under data/cache so `bp draft` / `bp report` / `bp lineup` open instantly on match day.

Keys always include a stamp of the source files (size + mtime of every .py / .yaml / .html in the package), so any code
or weight change invalidates every entry automatically; the DB identity and file stamp do the same for new data. Disable with
`bp --no-cache ...` or BP_NO_CACHE=1. Entries are plain pickles: delete the directory whenever in doubt.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import pickle
from pathlib import Path

from .config import CONFIG

log = logging.getLogger(__name__)
ENABLED = not os.environ.get("BP_NO_CACHE")
DIR: Path | None = None       # default CONFIG.data_dir / "cache"; tests override


def _dir() -> Path:
    return DIR or (CONFIG.data_dir / "cache")


def file_stamp(path: Path | str | None) -> tuple[str, int | None, int | None] | None:
    """Requested file identity and stamp; None only when no path was requested."""
    if path is None:
        return None
    p = Path(path).resolve()
    identity = os.path.normcase(str(p))
    try:
        st = p.stat()
    except FileNotFoundError:
        return (identity, None, None)
    return (identity, st.st_size, st.st_mtime_ns)


def source_stamp(pkg_dir: Path | None = None) -> str:
    d = pkg_dir or Path(__file__).parent
    parts = sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns)
                   for f in d.iterdir() if f.suffix in (".py", ".yaml", ".html"))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:12]


def key(*parts) -> str:
    return hashlib.sha256(json.dumps([source_stamp(), *parts], default=str, sort_keys=True).encode()).hexdigest()[:20]


def _path(name: str, k: str) -> Path:
    return _dir() / f"{name}_{k}.pkl"


def get(name: str, k: str):
    if not ENABLED:
        return None
    p = _path(name, k)
    if not p.exists():
        return None
    try:
        with p.open("rb") as f:
            return pickle.load(f)
    except Exception as e:      # a half-written or incompatible pickle must never break a command
        log.warning("cache %s unreadable (%s); rebuilding", p.name, e)
        return None


def put(name: str, k: str, obj) -> None:
    if not ENABLED:
        return
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = _path(name, k).with_suffix(".tmp")
    try:
        with tmp.open("wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, _path(name, k))
    except Exception as e:      # best effort: an unpicklable object just is not cached
        log.warning("cache %s not written (%s)", name, e)
        tmp.unlink(missing_ok=True)
