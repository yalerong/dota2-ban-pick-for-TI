"""Immutable as-of snapshot with a content-hash data_version (P1-09)."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG
from .db import SCHEMA

PK = {"match_index": "match_id", "heroes": "hero_id", "patches": "patch_id", "draft_formats": "patch",
      "matches": "match_id", "draft_events": "match_id, order_no", "players": "account_id",
      "teams": "team_id", "roster_snapshots": "match_id, player_slot"}


def _copy(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, where: str, args: tuple, h: hashlib._Hash) -> int:
    cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
    rows = src.execute(f"SELECT {','.join(cols)} FROM {table} {where} ORDER BY {PK[table]}", args).fetchall()
    dst.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [tuple(r) for r in rows])
    for r in rows:
        h.update(json.dumps(list(r), ensure_ascii=False, default=str).encode())
        h.update(b"\n")
    return len(rows)


def export_snapshot(con: sqlite3.Connection, as_of: datetime, out_dir: Path | None = None) -> dict:
    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    ts = int(as_of.timestamp())
    out_dir = out_dir or CONFIG.snapshot_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"_building_{ts}.sqlite"
    if tmp.exists():
        tmp.unlink()
    dst = sqlite3.connect(tmp)
    dst.executescript(SCHEMA)
    h = hashlib.sha256()
    counts = {}
    counts["match_index"] = _copy(con, dst, "match_index", "WHERE start_time < ?", (ts,), h)
    counts["matches"] = _copy(con, dst, "matches", "WHERE start_time < ?", (ts,), h)
    sub = "WHERE match_id IN (SELECT match_id FROM matches WHERE start_time < ?)"
    counts["draft_events"] = _copy(con, dst, "draft_events", sub, (ts,), h)
    counts["roster_snapshots"] = _copy(con, dst, "roster_snapshots", sub, (ts,), h)
    for t in ("heroes", "patches", "draft_formats"):
        counts[t] = _copy(con, dst, t, "", (), h)
    # players / teams: only those seen in included matches; last_seen recomputed so nothing after as_of leaks
    players = con.execute("SELECT account_id, name FROM players").fetchall()
    seen_p = {r[0]: r[1] for r in dst.execute(
        "SELECT r.account_id, MAX(m.start_time) FROM roster_snapshots r JOIN matches m ON m.match_id=r.match_id "
        "WHERE r.account_id IS NOT NULL GROUP BY r.account_id")}
    prow = sorted((a, n, seen_p[a]) for a, n in players if a in seen_p)
    dst.executemany("INSERT INTO players (account_id, name, last_seen) VALUES (?,?,?)", prow)
    for r in prow:
        h.update(json.dumps(list(r), ensure_ascii=False).encode() + b"\n")
    counts["players"] = len(prow)
    teams = con.execute("SELECT team_id, name, names_json FROM teams").fetchall()
    seen_t = {r[0]: r[1] for r in dst.execute(
        "SELECT team_id, MAX(start_time) FROM (SELECT radiant_team_id team_id, start_time FROM matches "
        "UNION ALL SELECT dire_team_id, start_time FROM matches) WHERE team_id IS NOT NULL GROUP BY team_id")}
    trow = sorted((t, n, nj, seen_t[t]) for t, n, nj in teams if t in seen_t)
    dst.executemany("INSERT INTO teams (team_id, name, names_json, last_seen) VALUES (?,?,?,?)", trow)
    for r in trow:
        h.update(json.dumps(list(r), ensure_ascii=False).encode() + b"\n")
    counts["teams"] = len(trow)

    max_id = dst.execute("SELECT MAX(match_id) FROM matches").fetchone()[0]
    h.update(f"as_of={ts};max_match_id={max_id}".encode())
    data_version = h.hexdigest()[:16]
    meta = {"data_version": data_version, "as_of": as_of.isoformat(), "as_of_ts": str(ts),
            "max_match_id": str(max_id), "created_at": datetime.now(timezone.utc).isoformat(),
            "counts": json.dumps(counts)}
    dst.executemany("INSERT INTO meta (key, value) VALUES (?,?)", list(meta.items()))
    dst.commit()
    dst.close()
    final = out_dir / f"snapshot_{as_of.strftime('%Y%m%d')}_{data_version}.sqlite"
    if final.exists():
        tmp.unlink()
        meta["reused_existing"] = True
    else:
        tmp.rename(final)
    meta["path"] = str(final)
    meta["counts"] = counts
    return meta
