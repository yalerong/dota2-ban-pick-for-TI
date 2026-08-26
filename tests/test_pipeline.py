"""Offline tests for normalize / draft-format inference / quality / export on synthetic matches."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from bp.db import connect
from bp.draft_formats import infer_formats, load_format, describe
from bp.export import export_snapshot
from bp.normalize import normalize_match, assign_phases, relative_signature, draft_sequence
from bp.quality import run_checks

# a plausible 24-action CM sequence: (is_pick, team) -- content doesn't matter, consistency does
SEQ = [(0, 0), (0, 1), (0, 0), (0, 1), (0, 0), (0, 1), (0, 0),
       (1, 1), (1, 0), (1, 0), (1, 1),
       (0, 1), (0, 0), (0, 1), (0, 0),
       (1, 0), (1, 1), (1, 1), (1, 0),
       (0, 1), (0, 0), (0, 1),
       (1, 0), (1, 1)]


def make_match(mid: int, start: int, first_team: int = 0, seq=SEQ, r_team=100, d_team=200,
               anon_slot: int | None = None, duration=2400, patch_id=58):
    pb = []
    for i, (is_pick, t) in enumerate(seq):
        team = t if first_team == 0 else 1 - t
        pb.append({"order": i, "is_pick": bool(is_pick), "team": team, "hero_id": 1 + i})
    players = []
    for k in range(10):
        slot = k if k < 5 else 128 + (k - 5)
        acct = None if anon_slot == slot else 1000 + k
        players.append({"account_id": acct, "player_slot": slot, "hero_id": 1 + k, "gold_per_min": 700 - 50 * (k % 5),
                        "lane_role": (k % 5) + 1, "name": f"p{k}"})
    return {"match_id": mid, "start_time": start, "duration": duration, "patch": patch_id, "leagueid": 1,
            "league": {"name": "Test League"}, "radiant_team_id": r_team, "dire_team_id": d_team,
            "radiant_team": {"team_id": r_team, "name": "Rad"}, "dire_team": {"team_id": d_team, "name": "Dire"},
            "radiant_win": True, "picks_bans": pb, "players": players, "draft_timings": [{"order": 0}]}


@pytest.fixture
def con(tmp_path):
    c = connect(tmp_path / "t.sqlite")
    c.execute("INSERT INTO patches VALUES (58, '7.41', ?)", (1_700_000_000,))
    c.executemany("INSERT INTO heroes VALUES (?,?,?,?,?)", [(i, f"npc_{i}", f"H{i}", "str", "[]") for i in range(1, 40)])
    return c


def test_phases_and_signature():
    seq = draft_sequence(make_match(1, 1_710_000_000)["picks_bans"])
    ph = assign_phases(seq)
    assert ph[0] == 0 and ph[7] == 1 and ph[-1] == 5
    assert relative_signature(seq) == tuple(SEQ)
    # radiant/dire swapped -> same relative signature
    seq2 = draft_sequence(make_match(2, 1_710_000_000, first_team=1)["picks_bans"])
    assert relative_signature(seq2) == tuple(SEQ)
    assert describe(tuple(SEQ)).startswith("Bb")


def test_normalize_format_check(con):
    base = 1_710_000_000
    for i in range(8):
        normalize_match(con, make_match(10 + i, base + i * 3600, first_team=i % 2))
    # one anomalous match: truncated draft; one with anonymous player; one short
    normalize_match(con, make_match(30, base + 9 * 3600, seq=SEQ[:20]))
    normalize_match(con, make_match(31, base + 10 * 3600, anon_slot=130))
    normalize_match(con, make_match(32, base + 11 * 3600, duration=300))
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM roster_snapshots WHERE match_id=10").fetchone()[0] == 10
    assert con.execute("SELECT position_est FROM roster_snapshots WHERE match_id=10 AND player_slot=0").fetchone()[0] == 1
    assert json.loads(con.execute("SELECT names_json FROM teams WHERE team_id=100").fetchone()[0]) == ["Rad"]

    fmts = infer_formats(con, min_support=5)
    assert fmts[0]["patch"] == "7.41" and fmts[0]["n_actions"] == 24 and fmts[0]["support"] == 10
    assert load_format(con, "7.41") == tuple(SEQ)

    res = run_checks(con)
    flags = {r[0]: json.loads(r[1]) for r in con.execute("SELECT match_id, quality_flags FROM matches")}
    assert "draft_count_mismatch" in flags[30]
    assert flags[31] == ["anonymous_player"]
    assert "short_duration" in flags[32]
    assert flags[10] == []
    assert res["excluded"] == 2 and res["clean"] == 9


def test_export_as_of_is_deterministic_and_leak_free(con, tmp_path):
    base = 1_710_000_000
    for i in range(6):
        normalize_match(con, make_match(10 + i, base + i * 86400))
    con.commit()
    infer_formats(con, min_support=5)
    run_checks(con)
    as_of = datetime.fromtimestamp(base + 3 * 86400, timezone.utc)
    m1 = export_snapshot(con, as_of, tmp_path / "snap")
    m2 = export_snapshot(con, as_of, tmp_path / "snap")
    assert m1["data_version"] == m2["data_version"] and m2.get("reused_existing")
    snap = sqlite3.connect(m1["path"])
    assert snap.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 3
    assert snap.execute("SELECT MAX(start_time) FROM matches").fetchone()[0] < int(as_of.timestamp())
    assert snap.execute("SELECT MAX(last_seen) FROM players").fetchone()[0] < int(as_of.timestamp())
    # a later match changes the hash for a later as_of but not this one
    normalize_match(con, make_match(99, base + 10 * 86400)); con.commit(); run_checks(con)
    m3 = export_snapshot(con, as_of, tmp_path / "snap")
    assert m3["data_version"] == m1["data_version"]
