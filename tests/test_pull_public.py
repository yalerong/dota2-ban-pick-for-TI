"""scripts/pull_public.py: row parsing, patch tagging, cursor/resume, and stop conditions - with a fake OpenDota."""
from __future__ import annotations
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("pull_public", Path(__file__).resolve().parents[1] / "scripts" / "pull_public.py")
pp = importlib.util.module_from_spec(spec); spec.loader.exec_module(pp)

PATCHES = [("7.40", 1_000_000), ("7.41", 2_000_000)]


class FakeOD:
    """Pages of 100 rows, newest first, match ids descending from 10_000; start_time falls 100s per row."""
    def __init__(self, n_rows=350, bad_every=50):
        self.rows = []
        for i in range(n_rows):
            mid = 10_000 - i
            rad, dire = [1, 2, 3, 4, 5], [6, 7, 8, 9, 10]
            if bad_every and i % bad_every == 7:
                dire = "6,7,8,9"                                   # 4 heroes -> skipped
            elif i % 2:
                rad, dire = "1,2,3,4,5", "6,7,8,9,10"              # old string format
            self.rows.append({"match_id": mid, "match_seq_num": mid * 2, "start_time": 2_050_000 - 100 * i, "duration": 2000,
                              "radiant_win": bool(i % 3), "lobby_type": 7 if i % 4 else 0, "game_mode": 22 if i % 5 else 23,
                              "avg_rank_tier": 75, "num_rank_tier": 10, "cluster": 155, "radiant_team": rad, "dire_team": dire})
        self.calls = []
        self.fail_after = None

    def public_matches_page(self, less_than_match_id=None, min_rank=None, max_rank=None):
        self.calls.append((less_than_match_id, min_rank))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise pp.DailyBudgetExceeded("budget")
        rows = [r for r in self.rows if less_than_match_id is None or r["match_id"] < less_than_match_id]
        return rows[:100]

    def budget_left(self):
        return 999


def test_row_parsing_and_patch_tagging():
    assert pp._heroes("1,2,3,4,5") == [1, 2, 3, 4, 5] and pp._heroes([5, 4, 3, 2, 1]) == [5, 4, 3, 2, 1]
    assert pp._heroes("1,2,3,4") is None and pp._heroes([1, 1, 2, 3, 4]) is None and pp._heroes(None) is None
    assert pp._patch_for(2_500_000, PATCHES) == "7.41" and pp._patch_for(1_500_000, PATCHES) == "7.40" and pp._patch_for(5, PATCHES) is None


def test_pull_walks_pages_resumes_from_cursor_and_skips_bad_rows(tmp_path):
    con = pp.open_db(tmp_path / "public.sqlite")
    od = FakeOD()
    added = pp.pull(con, od, PATCHES, min_rank=70, until=0, target=None, max_pages=2)
    assert added == 200 - 4                                              # rows 7, 57, 107, 157 are malformed
    st = pp.status(con)
    assert st["rows"] == 196 and st["cursor"] == 10_000 - 199 and st["pages"] == 2 and st["skipped_bad_rows"] == 4
    assert od.calls == [(None, 70), (10_000 - 99, 70)]
    assert st["by_patch"] == {"7.41": 196}
    # resume: the next run continues below the stored cursor and reaches the end of data
    od2 = FakeOD()
    pp.pull(con, od2, PATCHES, min_rank=70, until=0, target=None, max_pages=None)
    assert od2.calls[0] == (10_000 - 199, 70)
    assert pp.status(con)["rows"] == 350 - 7
    # a different min_rank on the same DB is refused (would mix populations)
    with pytest.raises(SystemExit):
        pp.pull(con, FakeOD(), PATCHES, min_rank=80, until=0, target=None, max_pages=1)


def test_pull_stops_at_target_and_at_until(tmp_path):
    con = pp.open_db(tmp_path / "a.sqlite")
    pp.pull(con, FakeOD(), PATCHES, min_rank=70, until=0, target=150, max_pages=None)
    assert 150 <= pp.status(con)["rows"] < 250                           # stops after the page that crosses the target
    con2 = pp.open_db(tmp_path / "b.sqlite")
    od = FakeOD()
    pp.pull(con2, od, PATCHES, min_rank=70, until=2_050_000 - 100 * 120, target=None, max_pages=None)
    assert len(od.calls) == 2                                            # second page contains rows older than --until


def test_budget_exhaustion_keeps_committed_rows(tmp_path):
    con = pp.open_db(tmp_path / "c.sqlite")
    od = FakeOD(); od.fail_after = 1
    with pytest.raises(pp.DailyBudgetExceeded):
        pp.pull(con, od, PATCHES, min_rank=70, until=0, target=None, max_pages=None)
    assert pp.status(con)["rows"] == 98 and pp.status(con)["cursor"] == 10_000 - 99
