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
from bp.sync import sync_index, sync_matches

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


class IndexClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def pro_matches_page(self, last_id=None):
        self.calls.append(last_id)
        return self.pages.pop(0) if self.pages else []


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


def test_snapshot_recomputes_formats_and_quality_from_included_matches(con, tmp_path):
    base = 1_710_000_000
    for i in range(6):
        normalize_match(con, make_match(40 + i, base + i * 86400))
    con.commit()
    infer_formats(con, min_support=5)
    run_checks(con)
    assert con.execute("SELECT COUNT(*) FROM draft_formats").fetchone()[0] == 1
    assert con.execute("SELECT SUM(excluded) FROM matches").fetchone()[0] == 0

    as_of = datetime.fromtimestamp(base + 4 * 86400, timezone.utc)
    meta = export_snapshot(con, as_of, tmp_path / "snap")
    snap = sqlite3.connect(meta["path"])
    flags = [json.loads(r[0]) for r in snap.execute("SELECT quality_flags FROM matches ORDER BY match_id")]

    assert snap.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 4
    assert snap.execute("SELECT COUNT(*) FROM draft_formats").fetchone()[0] == 0
    assert all("no_format_for_patch" in f for f in flags)
    assert snap.execute("SELECT SUM(excluded) FROM matches").fetchone()[0] == 4


def test_snapshot_uses_only_pre_cutoff_name_evidence(con, tmp_path):
    base = 1_710_000_000
    con.execute(
        """INSERT INTO match_index (match_id, start_time, duration, leagueid, league_name, radiant_team_id,
           dire_team_id, radiant_name, dire_name, radiant_win) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (70, base, 2400, 1, "Test League", 100, 200, "Old Rad", "Dire", 1))
    old = make_match(70, base)
    old["radiant_team"]["name"] = "Old Rad"
    normalize_match(con, old, con.execute("SELECT * FROM match_index WHERE match_id=70").fetchone())
    con.commit()
    as_of = datetime.fromtimestamp(base + 86400, timezone.utc)
    before = export_snapshot(con, as_of, tmp_path / "snap")

    con.execute(
        """INSERT INTO match_index (match_id, start_time, duration, leagueid, league_name, radiant_team_id,
           dire_team_id, radiant_name, dire_name, radiant_win) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (71, base + 10 * 86400, 2400, 1, "Test League", 100, 200, "Future Rad", "Dire", 1))
    future = make_match(71, base + 10 * 86400)
    future["radiant_team"]["name"] = "Future Rad"
    future["players"][0]["name"] = "Future Player"
    normalize_match(con, future, con.execute("SELECT * FROM match_index WHERE match_id=71").fetchone())
    con.commit()

    after = export_snapshot(con, as_of, tmp_path / "snap")
    snap = sqlite3.connect(after["path"])
    team = snap.execute("SELECT name, names_json, last_seen FROM teams WHERE team_id=100").fetchone()
    player = snap.execute("SELECT name, last_seen FROM players WHERE account_id=1000").fetchone()

    assert after["data_version"] == before["data_version"]
    assert team == ("Old Rad", '["Old Rad"]', base)
    assert player == ("p0", base)


def test_sync_index_skips_rows_before_since(con):
    page = [
        {"match_id": 90, "start_time": 300, "radiant_win": True},
        {"match_id": 91, "start_time": 200, "radiant_win": False},
        {"match_id": 92, "start_time": 100, "radiant_win": True},
    ]
    res = sync_index(IndexClient([page]), con, since_ts=200, full=True)
    ids = [r[0] for r in con.execute("SELECT match_id FROM match_index ORDER BY match_id")]

    assert res == {"pages": 1, "inserted": 2}
    assert ids == [90, 91]


def test_duplicate_hero_is_excluded(con):
    base = 1_710_000_000
    for i in range(5):
        normalize_match(con, make_match(100 + i, base + i * 3600))
    duplicate = make_match(120, base + 6 * 3600)
    duplicate["picks_bans"][1]["hero_id"] = duplicate["picks_bans"][0]["hero_id"]
    normalize_match(con, duplicate)
    con.commit()
    infer_formats(con, min_support=5)
    run_checks(con)
    flags, excluded = con.execute("SELECT quality_flags, excluded FROM matches WHERE match_id=120").fetchone()

    assert "duplicate_hero" in json.loads(flags)
    assert excluded == 1


def test_connect_migrates_player_name_observations(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute(
        """CREATE TABLE roster_snapshots (
           match_id INTEGER, team_id INTEGER, account_id INTEGER, player_slot INTEGER, side INTEGER,
           hero_id INTEGER, lane_role INTEGER, gpm INTEGER, position_est INTEGER,
           PRIMARY KEY (match_id, player_slot))""")
    old.commit()
    old.close()

    migrated = connect(path)
    columns = {r[1] for r in migrated.execute("PRAGMA table_info(roster_snapshots)")}
    assert "player_name" in columns


def test_packaged_scoring_yaml_matches_the_config_copy():
    from importlib import resources
    from bp.config import CONFIG
    packed = resources.files("bp").joinpath("scoring.yaml").read_bytes()
    assert (CONFIG.root / "config" / "scoring.yaml").read_bytes() == packed


class DetailClient:
    def __init__(self, results):                     # match_id -> payload | exception
        self.results = results

    def match(self, mid):
        r = self.results[mid]
        if isinstance(r, Exception):
            raise r
        return r


def test_sync_matches_marks_statuses_and_stays_resumable(con):
    for mid in (1, 2, 3, 4):
        con.execute("INSERT INTO match_index (match_id, start_time) VALUES (?,?)", (mid, 1000))
    client = DetailClient({1: {"match_id": 1, "picks_bans": [{}]},
                           2: {"match_id": 2, "picks_bans": []},
                           3: None,
                           4: RuntimeError("network down")})
    st = sync_matches(client, con)
    assert st["ok"] == 1 and st["missing_picks_bans"] == 1 and st["not_found"] == 1 and st["error"] == 1
    assert st["remaining"] == 1                      # only the failed match stays pending (retryable)
    assert con.execute("SELECT detail_status FROM match_index WHERE match_id=4").fetchone()[0] is None


def test_sync_matches_stops_at_daily_budget(con):
    from bp.opendota import DailyBudgetExceeded
    for mid in (1, 2, 3):
        con.execute("INSERT INTO match_index (match_id, start_time) VALUES (?,?)", (mid, 1000))
    client = DetailClient({1: {"match_id": 1, "picks_bans": [{}]},
                           2: DailyBudgetExceeded("budget"),
                           3: {"match_id": 3, "picks_bans": [{}]}})
    st = sync_matches(client, con)
    assert st["budget_stop"] is True and st["ok"] == 1 and st["remaining"] == 2
    assert con.execute("SELECT detail_status FROM match_index WHERE match_id=3").fetchone()[0] is None
