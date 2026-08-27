"""Data quality checks (P1-08). Writes matches.quality_flags / excluded."""
from __future__ import annotations
import json
import sqlite3
from collections import Counter, defaultdict

from .draft_formats import load_format
from .normalize import relative_signature

HARD = {"draft_count_mismatch", "draft_format_anomaly", "no_format_for_patch", "unknown_hero",
        "missing_team", "short_duration", "roster_incomplete", "no_result", "duplicate_hero"}
SOFT = {"anonymous_player", "missing_draft_timings"}
MIN_DURATION = 600


def run_checks(con: sqlite3.Connection) -> dict:
    heroes = {r[0] for r in con.execute("SELECT hero_id FROM heroes")}
    formats: dict[str, tuple | None] = {}
    events: dict[int, list] = defaultdict(list)
    for r in con.execute("SELECT match_id, order_no, team_side, is_pick, hero_id FROM draft_events ORDER BY match_id, order_no"):
        events[r[0]].append((r[1], r[2], r[3], r[4]))
    roster: dict[int, list] = defaultdict(list)
    for r in con.execute("SELECT match_id, account_id FROM roster_snapshots"):
        roster[r[0]].append(r[1])

    counts: Counter = Counter()
    n = excluded = 0
    for m in con.execute("SELECT match_id, patch, duration, radiant_team_id, dire_team_id, radiant_win, has_draft_timings FROM matches").fetchall():
        n += 1
        flags = []
        mid = m["match_id"]
        seq = events.get(mid, [])
        if m["patch"] not in formats:
            formats[m["patch"]] = load_format(con, m["patch"]) if m["patch"] else None
        fmt = formats[m["patch"]]
        if fmt is None:
            flags.append("no_format_for_patch")
        else:
            if len(seq) != len(fmt):
                flags.append("draft_count_mismatch")
            elif relative_signature(seq) != fmt:
                flags.append("draft_format_anomaly")
        hero_ids = [h for _, _, _, h in seq]
        if any(h not in heroes for h in hero_ids):
            flags.append("unknown_hero")
        if len(set(hero_ids)) != len(hero_ids):
            flags.append("duplicate_hero")
        if not m["radiant_team_id"] or not m["dire_team_id"]:
            flags.append("missing_team")
        if m["radiant_win"] is None:
            flags.append("no_result")
        if (m["duration"] or 0) < MIN_DURATION:
            flags.append("short_duration")
        if len(roster.get(mid, [])) != 10:
            flags.append("roster_incomplete")
        if any(a is None for a in roster.get(mid, [])):
            flags.append("anonymous_player")
        if not m["has_draft_timings"]:
            flags.append("missing_draft_timings")
        exc = int(any(f in HARD for f in flags))
        excluded += exc
        counts.update(flags)
        con.execute("UPDATE matches SET quality_flags=?, excluded=? WHERE match_id=?", (json.dumps(flags), exc, mid))
    con.commit()
    return {"matches": n, "excluded": excluded, "clean": n - excluded,
            "flags": {k: {"count": v, "hard": k in HARD} for k, v in sorted(counts.items())}}
