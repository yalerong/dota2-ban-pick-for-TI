"""Normalize raw match payloads into matches / draft_events / players / teams / roster_snapshots (P1-07)."""
from __future__ import annotations
import json
import logging
import sqlite3

from .opendota import OpenDota

log = logging.getLogger(__name__)


def draft_sequence(picks_bans: list[dict]) -> list[tuple[int, int, int, int]]:
    """-> [(order, team_side, is_pick, hero_id)] sorted by order."""
    seq = sorted(picks_bans, key=lambda x: x["order"])
    return [(int(x["order"]), int(x["team"]), int(bool(x["is_pick"])), int(x["hero_id"])) for x in seq]


def assign_phases(seq: list[tuple[int, int, int, int]]) -> list[int]:
    """Phase = index of the run of consecutive same-is_pick actions (0-based)."""
    phases, cur, prev = [], -1, None
    for _, _, is_pick, _ in seq:
        if is_pick != prev:
            cur += 1
            prev = is_pick
        phases.append(cur)
    return phases


def relative_signature(seq: list[tuple[int, int, int, int]]) -> tuple[tuple[int, int], ...]:
    """(is_pick, team_relative_to_first_actor) per step; radiant/dire symmetric."""
    if not seq:
        return ()
    first = seq[0][1]
    return tuple((is_pick, 0 if team == first else 1) for _, team, is_pick, _ in seq)


def _patch_name(con: sqlite3.Connection, patch_id, start_time: int) -> tuple[str | None, int | None]:
    if patch_id is not None:
        r = con.execute("SELECT name FROM patches WHERE patch_id=?", (patch_id,)).fetchone()
        if r:
            return r[0], int(patch_id)
    r = con.execute("SELECT patch_id, name FROM patches WHERE release_time <= ? ORDER BY release_time DESC LIMIT 1",
                    (start_time,)).fetchone()
    return (r[1], r[0]) if r else (None, None)


def normalize_match(con: sqlite3.Connection, m: dict, index_row: sqlite3.Row | None = None) -> None:
    mid = int(m["match_id"])
    start_time = int(m.get("start_time") or 0)
    patch_name, patch_id = _patch_name(con, m.get("patch"), start_time)
    league = m.get("league") or {}
    r_team = m.get("radiant_team_id") or (m.get("radiant_team") or {}).get("team_id")
    d_team = m.get("dire_team_id") or (m.get("dire_team") or {}).get("team_id")
    if index_row is not None:
        r_team = r_team or index_row["radiant_team_id"]
        d_team = d_team or index_row["dire_team_id"]
    con.execute(
        """INSERT OR REPLACE INTO matches (match_id, patch, patch_id, leagueid, league_name, start_time, duration,
           radiant_team_id, dire_team_id, radiant_win, series_id, series_type, has_draft_timings)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mid, patch_name, patch_id, m.get("leagueid"), league.get("name") or (index_row["league_name"] if index_row else None),
         start_time, m.get("duration"), r_team, d_team,
         None if m.get("radiant_win") is None else int(bool(m["radiant_win"])),
         m.get("series_id"), m.get("series_type"), int(bool(m.get("draft_timings")))))

    seq = draft_sequence(m.get("picks_bans") or [])
    phases = assign_phases(seq)
    con.execute("DELETE FROM draft_events WHERE match_id=?", (mid,))
    con.executemany("INSERT INTO draft_events (match_id, order_no, team_side, is_pick, hero_id, phase) VALUES (?,?,?,?,?,?)",
                    [(mid, o, t, p, h, ph) for (o, t, p, h), ph in zip(seq, phases)])

    # teams
    for tid, name in ((r_team, (m.get("radiant_team") or {}).get("name") or (index_row["radiant_name"] if index_row else None)),
                      (d_team, (m.get("dire_team") or {}).get("name") or (index_row["dire_name"] if index_row else None))):
        if not tid:
            continue
        row = con.execute("SELECT name, names_json, last_seen FROM teams WHERE team_id=?", (tid,)).fetchone()
        names = set(json.loads(row["names_json"])) if row else set()
        if name:
            names.add(name)
        last_seen = max(start_time, row["last_seen"] or 0) if row else start_time
        cur_name = name if (row is None or start_time >= (row["last_seen"] or 0)) and name else (row["name"] if row else name)
        con.execute("INSERT OR REPLACE INTO teams (team_id, name, names_json, last_seen) VALUES (?,?,?,?)",
                    (tid, cur_name, json.dumps(sorted(names), ensure_ascii=False), last_seen))

    # roster snapshots + players
    con.execute("DELETE FROM roster_snapshots WHERE match_id=?", (mid,))
    players = m.get("players") or []
    by_side: dict[int, list[dict]] = {0: [], 1: []}
    for p in players:
        slot = int(p.get("player_slot", 0))
        by_side[0 if slot < 128 else 1].append(p)
    for side, plist in by_side.items():
        team_id = r_team if side == 0 else d_team
        ranked = sorted(plist, key=lambda p: -(p.get("gold_per_min") or 0))
        pos = {id(p): i + 1 for i, p in enumerate(ranked)}
        for p in plist:
            acct = p.get("account_id")
            pname = p.get("name") or p.get("personaname")
            con.execute(
                """INSERT OR REPLACE INTO roster_snapshots
                   (match_id, team_id, account_id, player_slot, side, hero_id, lane_role, gpm, position_est, player_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (mid, team_id, acct, int(p.get("player_slot", 0)), side, p.get("hero_id"),
                 p.get("lane_role"), p.get("gold_per_min"), pos[id(p)], pname))
            if acct:
                row = con.execute("SELECT name, last_seen FROM players WHERE account_id=?", (acct,)).fetchone()
                if row is None or start_time >= (row["last_seen"] or 0):
                    con.execute("INSERT OR REPLACE INTO players (account_id, name, last_seen) VALUES (?,?,?)",
                                (acct, pname or (row["name"] if row else None), max(start_time, row["last_seen"] or 0) if row else start_time))


def normalize_all(con: sqlite3.Connection, client: OpenDota, rebuild: bool = False) -> dict:
    q = "SELECT * FROM match_index WHERE detail_status='ok'"
    if not rebuild:
        q += " AND match_id NOT IN (SELECT match_id FROM matches)"
    rows = con.execute(q + " ORDER BY start_time").fetchall()
    done = missing_raw = 0
    for i, r in enumerate(rows, 1):
        payload = client.read_cache("/matches", str(r["match_id"]))
        if payload is None:
            missing_raw += 1
            continue
        normalize_match(con, payload, r)
        done += 1
        if i % 200 == 0:
            con.commit()
            log.info("normalized %d/%d", i, len(rows))
    con.commit()
    return {"normalized": done, "missing_raw": missing_raw}
