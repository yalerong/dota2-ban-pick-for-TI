"""Static metadata from dotaconstants (P1-05): heroes and patches."""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import CONFIG

log = logging.getLogger(__name__)
RAW = "https://raw.githubusercontent.com/odota/dotaconstants/master/build/"


def _fetch(name: str, raw_dir: Path | None = None, offline: bool = False):
    d = (raw_dir or CONFIG.raw_dir) / "constants"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if not offline:
        try:
            r = requests.get(RAW + name, timeout=60)
            r.raise_for_status()
            p.write_text(r.text, encoding="utf-8")
        except requests.RequestException as e:
            log.warning("constants %s: %s; using cached copy", name, e)
    if not p.exists():
        raise RuntimeError(f"no cached copy of {name}")
    return json.loads(p.read_text(encoding="utf-8"))


def _iso_to_ts(s) -> int:
    if isinstance(s, (int, float)):
        return int(s)
    return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())


def load_constants(con: sqlite3.Connection, raw_dir: Path | None = None, offline: bool = False) -> dict:
    heroes = _fetch("heroes.json", raw_dir, offline)
    patches = _fetch("patch.json", raw_dir, offline)
    con.executemany(
        "INSERT OR REPLACE INTO heroes (hero_id, name, localized_name, primary_attr, roles) VALUES (?,?,?,?,?)",
        [(h["id"], h["name"], h["localized_name"], h.get("primary_attr"), json.dumps(h.get("roles", [])))
         for h in heroes.values()])
    rows = []
    for i, p in enumerate(patches):
        rows.append((p.get("id", i), p["name"], _iso_to_ts(p["date"])))
    con.executemany("INSERT OR REPLACE INTO patches (patch_id, name, release_time) VALUES (?,?,?)", rows)
    con.commit()
    return {"heroes": len(heroes), "patches": len(rows)}


def patch_for_time(con: sqlite3.Connection, ts: int) -> str | None:
    r = con.execute("SELECT name FROM patches WHERE release_time <= ? ORDER BY release_time DESC LIMIT 1",
                    (ts,)).fetchone()
    return r[0] if r else None
