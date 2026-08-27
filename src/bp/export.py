"""Immutable as-of snapshot with a content-hash data_version (P1-09)."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG
from .db import SCHEMA

PK = {"match_index": "match_id", "heroes": "hero_id", "patches": "patch_id", "draft_formats": "patch",
      "matches": "match_id", "draft_events": "match_id, order_no", "players": "account_id",
      "teams": "team_id", "roster_snapshots": "match_id, player_slot"}


def _copy(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, where: str, args: tuple) -> int:
    cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
    rows = src.execute(f"SELECT {','.join(cols)} FROM {table} {where} ORDER BY {PK[table]}", args).fetchall()
    dst.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [tuple(r) for r in rows])
    return len(rows)


def _hash_table(con: sqlite3.Connection, table: str, h: hashlib._Hash) -> None:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    rows = con.execute(f"SELECT {','.join(cols)} FROM {table} ORDER BY {PK[table]}").fetchall()
    for r in rows:
        h.update(json.dumps(list(r), ensure_ascii=False, default=str).encode())
        h.update(b"\n")


def export_snapshot(con: sqlite3.Connection, as_of: datetime, out_dir: Path | None = None) -> dict:
    from .draft_formats import infer_formats
    from .quality import run_checks

    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    ts = int(as_of.timestamp())
    out_dir = out_dir or CONFIG.snapshot_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"_building_{ts}.sqlite"
    if tmp.exists():
        tmp.unlink()
    dst = sqlite3.connect(tmp)
    dst.row_factory = sqlite3.Row
    dst.executescript(SCHEMA)
    counts = {}
    counts["match_index"] = _copy(con, dst, "match_index", "WHERE start_time < ?", (ts,))
    counts["matches"] = _copy(con, dst, "matches", "WHERE start_time < ?", (ts,))
    sub = "WHERE match_id IN (SELECT match_id FROM matches WHERE start_time < ?)"
    counts["draft_events"] = _copy(con, dst, "draft_events", sub, (ts,))
    counts["roster_snapshots"] = _copy(con, dst, "roster_snapshots", sub, (ts,))
    for t in ("heroes", "patches"):
        counts[t] = _copy(con, dst, t, "", ())
    # players / teams: only those seen in included matches; last_seen recomputed so nothing after as_of leaks
    seen_p = {r[0]: r[1] for r in dst.execute(
        "SELECT r.account_id, MAX(m.start_time) FROM roster_snapshots r JOIN matches m ON m.match_id=r.match_id "
        "WHERE r.account_id IS NOT NULL GROUP BY r.account_id")}
    latest_player_names = {}
    for row in dst.execute(
        """SELECT r.account_id, r.player_name FROM roster_snapshots r
           JOIN matches m ON m.match_id=r.match_id
           WHERE r.account_id IS NOT NULL AND r.player_name IS NOT NULL
           ORDER BY m.start_time, r.match_id, r.player_slot"""):
        latest_player_names[row["account_id"]] = row["player_name"]
    prow = [(account_id, latest_player_names.get(account_id) or str(account_id), last_seen)
            for account_id, last_seen in sorted(seen_p.items())]
    dst.executemany("INSERT INTO players (account_id, name, last_seen) VALUES (?,?,?)", prow)
    counts["players"] = len(prow)
    seen_t = {r[0]: r[1] for r in dst.execute(
        "SELECT team_id, MAX(start_time) FROM (SELECT radiant_team_id team_id, start_time FROM matches "
        "UNION ALL SELECT dire_team_id, start_time FROM matches) WHERE team_id IS NOT NULL GROUP BY team_id")}
    evidence_names: dict[int, set[str]] = defaultdict(set)
    latest_name: dict[int, tuple[int, str]] = {}
    for row in dst.execute(
        """SELECT match_id, start_time, radiant_team_id team_id, radiant_name name FROM match_index
           WHERE radiant_team_id IS NOT NULL
           UNION ALL
           SELECT match_id, start_time, dire_team_id team_id, dire_name name FROM match_index
           WHERE dire_team_id IS NOT NULL
           ORDER BY start_time, match_id"""):
        if not row["name"]:
            continue
        evidence_names[row["team_id"]].add(row["name"])
        latest_name[row["team_id"]] = (row["start_time"], row["name"])
    trow = []
    for team_id, last_seen in sorted(seen_t.items()):
        if evidence_names.get(team_id):
            name = latest_name[team_id][1]
            names_json = json.dumps(sorted(evidence_names[team_id]), ensure_ascii=False)
        else:
            name = str(team_id)
            names_json = json.dumps([name])
        trow.append((team_id, name, names_json or "[]", last_seen))
    dst.executemany("INSERT INTO teams (team_id, name, names_json, last_seen) VALUES (?,?,?,?)", trow)
    counts["teams"] = len(trow)

    infer_formats(dst)
    run_checks(dst)
    counts["draft_formats"] = dst.execute("SELECT COUNT(*) FROM draft_formats").fetchone()[0]

    max_id = dst.execute("SELECT MAX(match_id) FROM matches").fetchone()[0]
    h = hashlib.sha256()
    for table in ("match_index", "matches", "draft_events", "roster_snapshots", "heroes", "patches",
                  "draft_formats", "players", "teams"):
        _hash_table(dst, table, h)
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
