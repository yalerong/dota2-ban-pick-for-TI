from __future__ import annotations

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
    snap.commit()
    live.commit()

    class FakeFrames:
        matches = pd.DataFrame({"start_time": [1000], "patch": ["7.41"]})
        events = pd.DataFrame(columns=["phase", "is_pick", "hero_id", "w"])
        teams = {100: "Rad", 200: "Dire"}

    monkeypatch.setattr(blindtest, "load_frames", lambda *args, **kwargs: FakeFrames())
    monkeypatch.setattr(blindtest, "load_format", lambda *args, **kwargs: ((0, 0),))
    monkeypatch.setattr(blindtest, "player_hero_stats", lambda fr: {})

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
