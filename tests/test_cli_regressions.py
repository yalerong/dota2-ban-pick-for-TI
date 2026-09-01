from __future__ import annotations
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from bp.db import connect


def _insert_match(con, match_id: int, patch: str, start_time: int) -> None:
    con.execute(
        """INSERT INTO matches (
             match_id, patch, patch_id, leagueid, league_name, start_time, duration,
             radiant_team_id, dire_team_id, radiant_win, series_id, series_type,
             has_draft_timings, quality_flags, excluded
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (match_id, patch, 1, 1, "League", start_time, 2400, 100, 200, 1, None, None, 1, "[]", 0),
    )


def test_report_defaults_to_snapshot_as_of_and_modal_patch_before_loading(tmp_path, monkeypatch):
    import bp.__main__ as cli
    import bp.profiles as profiles
    import bp.report as report

    db = tmp_path / "snap.sqlite"
    con = connect(db)
    con.executemany("INSERT INTO teams VALUES (?,?,?,?)", [(100, "Rad", "[]", 1), (200, "Dire", "[]", 1)])
    con.execute("INSERT INTO meta VALUES (?,?)", ("as_of_ts", "2000"))
    _insert_match(con, 1, "7.41", 1000)
    _insert_match(con, 2, "7.41", 1200)
    _insert_match(con, 3, "7.40", 1300)
    _insert_match(con, 4, "7.99", 3000)
    con.commit()

    seen = {}

    class FakeFrames:
        matches = pd.DataFrame({"patch": ["7.41"]})
        heroes = {}

        def team_id(self, name):
            return {"Rad": 100, "Dire": 200}.get(name)

    def fake_load_frames(con_arg, as_of=None, patch=None):
        seen["as_of"] = as_of
        seen["patch"] = patch
        return FakeFrames()

    monkeypatch.setattr(profiles, "load_frames", fake_load_frames)
    monkeypatch.setattr(profiles, "player_hero_stats", lambda fr: {})
    monkeypatch.setattr(profiles, "build_profile", lambda fr, tid, stats: SimpleNamespace(team_id=tid, name=f"T{tid}"))
    monkeypatch.setattr(report, "build_report", lambda *args: "ok")

    cli.cmd_report(
        SimpleNamespace(db=str(db), us="Rad", them="Dire", patch=None, as_of=None, out=str(tmp_path / "r.md"), stdout=False)
    )

    assert seen == {"as_of": 2000, "patch": "7.41"}


def test_report_preserves_explicit_as_of_and_patch(tmp_path, monkeypatch):
    import bp.__main__ as cli
    import bp.profiles as profiles
    import bp.report as report

    db = tmp_path / "snap.sqlite"
    con = connect(db)
    con.executemany("INSERT INTO teams VALUES (?,?,?,?)", [(100, "Rad", "[]", 1), (200, "Dire", "[]", 1)])
    con.execute("INSERT INTO meta VALUES (?,?)", ("as_of_ts", "2000"))
    _insert_match(con, 1, "7.41", 1000)
    con.commit()

    seen = {}

    class FakeFrames:
        matches = pd.DataFrame({"patch": ["7.40"]})
        heroes = {}

        def team_id(self, name):
            return {"Rad": 100, "Dire": 200}.get(name)

    monkeypatch.setattr(profiles, "load_frames", lambda con_arg, as_of=None, patch=None: seen.update(as_of=as_of, patch=patch) or FakeFrames())
    monkeypatch.setattr(profiles, "player_hero_stats", lambda fr: {})
    monkeypatch.setattr(profiles, "build_profile", lambda fr, tid, stats: SimpleNamespace(team_id=tid, name=f"T{tid}"))
    monkeypatch.setattr(report, "build_report", lambda *args: "ok")

    cli.cmd_report(
        SimpleNamespace(
            db=str(db), us="Rad", them="Dire", patch="7.40", as_of="1970-01-01T00:10:00",
            out=str(tmp_path / "r.md"), stdout=False
        )
    )

    assert seen == {"as_of": 600, "patch": "7.40"}


def test_blindtest_uses_inferred_patch_for_snapshot_and_live_query(tmp_path, monkeypatch):
    import bp.blindtest as blindtest

    snap = connect(tmp_path / "snap.sqlite")
    live = connect(tmp_path / "live.sqlite")
    _insert_match(snap, 1, "7.41", 1000)
    _insert_match(snap, 2, "7.41", 1100)
    _insert_match(snap, 3, "7.40", 1200)
    snap.commit()

    seen = {}

    class FakeFrames:
        matches = pd.DataFrame({"start_time": [1000, 1100], "patch": ["7.41", "7.41"]})
        events = pd.DataFrame(columns=["phase", "is_pick", "hero_id", "w"])
        teams = {}

    def fake_load_frames(con_arg, as_of=None, patch=None):
        seen["load_patch"] = patch
        return FakeFrames()

    def fake_read_sql_query(query, con_arg, params=None):
        seen["query"] = query
        seen["params"] = list(params or [])
        return pd.DataFrame(
            columns=["match_id", "patch", "start_time", "radiant_team_id", "dire_team_id", "league_name"]
        )

    monkeypatch.setattr(blindtest, "load_frames", fake_load_frames)
    monkeypatch.setattr(blindtest, "load_format", lambda con_arg, patch: ((0, 0),))
    monkeypatch.setattr(blindtest, "player_hero_stats", lambda fr: {})
    monkeypatch.setattr(blindtest.pd, "read_sql_query", fake_read_sql_query)

    res = blindtest.blind_test(snap, live, as_of=2000)

    assert seen["load_patch"] == "7.41"
    assert "AND patch = ?" in seen["query"]
    assert seen["params"] == [2000, "7.41"]
    assert res["patch"] == "7.41"


def test_blindtest_preserves_a_fixed_test_match_order(tmp_path, monkeypatch):
    import bp.blindtest as blindtest

    snap = connect(tmp_path / "snap.sqlite")
    live = connect(tmp_path / "live.sqlite")
    _insert_match(snap, 1, "7.41", 1000)
    _insert_match(live, 11, "7.41", 2100)
    _insert_match(live, 22, "7.41", 2200)
    for mid in (11, 22):                     # clean matches always carry exactly len(fmt) draft events
        live.execute("INSERT INTO draft_events (match_id, order_no, team_side, is_pick, hero_id, phase) VALUES (?,0,0,0,7,0)", (mid,))
    snap.commit()
    live.commit()

    class FakeFrames:
        matches = pd.DataFrame({"start_time": [1000], "patch": ["7.41"]})
        events = pd.DataFrame(columns=["phase", "is_pick", "hero_id", "w"])
        teams = {100: "Rad", 200: "Dire"}
        heroes = {7: "H7"}

    monkeypatch.setattr(blindtest, "load_frames", lambda *args, **kwargs: FakeFrames())
    monkeypatch.setattr(blindtest, "load_format", lambda *args, **kwargs: ((0, 0),))
    monkeypatch.setattr(blindtest, "player_hero_stats", lambda fr: {})
    # this test covers order/validation only, not scoring
    monkeypatch.setattr(blindtest, "build_profile", lambda fr, tid, stats=None: object())
    monkeypatch.setattr(blindtest, "candidates", lambda *args, **kwargs: [])

    res = blindtest.blind_test(snap, live, as_of=2000, patch="7.41", test_match_ids=[22, 11])

    assert res["test_match_ids"] == [22, 11]
    assert res["test_set_hash"]

    with pytest.raises(ValueError, match="evaluated in full"):   # --max must never silently shrink a fixed set
        blindtest.blind_test(snap, live, as_of=2000, patch="7.41", test_match_ids=[22, 11], max_matches=1)
    assert blindtest.blind_test(snap, live, as_of=2000, patch="7.41", test_match_ids=[22, 11], max_matches=2)["test_match_ids"] == [22, 11]
    with pytest.raises(ValueError, match="duplicates"):
        blindtest.blind_test(snap, live, as_of=2000, patch="7.41", test_match_ids=[11, 11])
    with pytest.raises(ValueError, match="unavailable or outside filters"):
        blindtest.blind_test(snap, live, as_of=2000, patch="7.41", test_match_ids=[33])

    live.execute("UPDATE matches SET radiant_team_id=300 WHERE match_id=22")
    live.commit()
    with pytest.raises(ValueError, match="teams absent from snapshot"):
        blindtest.blind_test(snap, live, as_of=2000, patch="7.41", test_match_ids=[22])


def test_blindtest_cli_context_actions_and_markdown_labels(tmp_path, monkeypatch, capsys):
    import bp.__main__ as cli
    import bp.blindtest as blindtest

    snap_path = tmp_path / "snap.sqlite"
    live_path = tmp_path / "live.sqlite"
    snap = connect(snap_path)
    connect(live_path).close()
    snap.execute("INSERT INTO meta VALUES (?,?)", ("as_of_ts", "2000"))
    snap.commit()
    snap.close()

    seen = {}

    def fake_blind_test(*args, **kwargs):
        mode = blindtest.context_mode(kwargs["context"], kwargs["context_actions"])
        seen["context"] = kwargs["context"]
        seen["context_actions"] = kwargs["context_actions"]
        return {
            "as_of": 2000,
            "patch": "7.41",
            "context": bool(mode["actions"]),
            "context_mode": mode,
            "test_matches": 0,
            "test_set_hash": "empty",
            "steps": 0,
            "overall": {"model": {"n": 0}, "baseline": {"n": 0}},
            "bans": {"model": {"n": 0}, "baseline": {"n": 0}},
            "picks": {"model": {"n": 0}, "baseline": {"n": 0}},
            "by_phase": {},
        }

    monkeypatch.setattr(blindtest, "blind_test", fake_blind_test)

    cli.main(["--db", str(live_path), "blindtest", "--snapshot", str(snap_path), "--context-actions", "ban"])
    out = capsys.readouterr().out
    assert seen == {"context": True, "context_actions": ("ban",)}
    assert "context terms BAN ONLY" in out
    assert "Machine context_mode: `ban`" in out

    cli.main([
        "--db", str(live_path), "blindtest", "--snapshot", str(snap_path), "--context-actions", "pick",
        "--no-context",
    ])
    out = capsys.readouterr().out
    assert seen == {"context": False, "context_actions": ("pick",)}
    assert "context terms OFF" in out
    assert "Machine context_mode: `none`" in out


class FakeLineupFrames:
    """Minimal Frames stand-in: 60 clean 5v5 matches on ten heroes, enough to fit the lineup stacker."""
    roles: dict = {}
    cfg: dict = {}

    def __init__(self, as_of=None):
        self.as_of = as_of
        ms, ro = [], []
        for i in range(60):
            ms.append({"match_id": i, "radiant_win": i % 2, "start_time": 2_000_000_000 + i})
            for sd, hs in ((0, (1, 3, 5, 7, 9)), (1, (2, 4, 6, 8, 10))):
                ro += [{"match_id": i, "side": sd, "hero_id": h} for h in hs]
        self.matches = pd.DataFrame(ms)
        self.roster = pd.DataFrame(ro)

    def hero(self, h):
        return f"H{h}"

    def hero_id(self, x):
        return int(x)


def _lineup_db(tmp_path) -> str:
    db = tmp_path / "bp.sqlite"
    con = connect(db)
    _insert_match(con, 5, "7.41", 2_000_000_000)
    con.executemany("INSERT INTO roster_snapshots (match_id, team_id, account_id, player_slot, side, hero_id) VALUES (?,?,?,?,?,?)",
                    [(5, 100 if sd == 0 else 200, -1, sd * 5 + i, sd, h)
                     for sd, hs in ((0, (1, 3, 5, 7, 9)), (1, (2, 4, 6, 8, 10))) for i, h in enumerate(hs)])
    con.commit()
    return str(db)


def test_lineup_match_caps_explicit_as_of_at_the_match_start(tmp_path, monkeypatch):
    import bp.__main__ as cli
    import bp.profiles as profiles

    seen = {}

    def fake_load_frames(con_arg, as_of=None, patch=None):
        seen["as_of"] = as_of
        return FakeLineupFrames(as_of)

    monkeypatch.setattr(profiles, "load_frames", fake_load_frames)
    cli.cmd_lineup(SimpleNamespace(db=_lineup_db(tmp_path), match=5, radiant=None, dire=None, swap=[],
                                   patch=None, as_of="2035-01-01", k=None, json=False))
    assert seen["as_of"] == 2_000_000_000      # capped at the match start, not the later explicit --as-of


def test_lineup_json_writes_a_single_json_document(tmp_path, monkeypatch, capsys):
    import bp.__main__ as cli
    import bp.profiles as profiles

    monkeypatch.setattr(profiles, "load_frames", lambda *args, **kw: FakeLineupFrames(2_000_000_000))
    cli.cmd_lineup(SimpleNamespace(db=_lineup_db(tmp_path), match=5, radiant=None, dire=None, swap=[],
                                   patch=None, as_of=None, k=None, json=True))
    doc = json.loads(capsys.readouterr().out)  # the whole stdout must parse as one JSON document
    assert 0 < doc["p_radiant"] < 1 and doc["n_train"] == 60


def test_lineup_eval_rejects_duplicate_fixed_test_ids(tmp_path, monkeypatch):
    import bp.__main__ as cli
    import bp.profiles as profiles
    import sqlite3

    snap = tmp_path / "snap.sqlite"
    s = sqlite3.connect(snap)
    s.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    s.execute("INSERT INTO meta VALUES ('as_of_ts', '0')")
    s.commit(); s.close()
    fixed = tmp_path / "fixed.json"
    fixed.write_text('{"match_ids": [7, 7, 8]}', encoding="utf-8")
    monkeypatch.setattr(profiles, "load_frames", lambda *args, **kw: FakeLineupFrames())
    a = SimpleNamespace(snapshot=str(snap), db=str(tmp_path / "live.sqlite"), patch="7.41", k=None,
                        test_matches=str(fixed), until=None, out=None)
    with pytest.raises(SystemExit, match="duplicate"):
        cli.cmd_lineup_eval(a)
