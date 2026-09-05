"""Whole-project review follow-ups: key redaction, .env quotes, vectorised positions, position_fit / their_next_pick,
ladder (public) pair counts in the lineup + context tables, and the pickle cache."""
from __future__ import annotations
import logging
import pickle
import random

import numpy as np
import pandas as pd
import pytest
import requests

from bp import cache, lineup as L, public
from bp.context import build_context
from bp.draft_state import DraftState
from bp.draft_formats import load_format
from bp.opendota import OpenDota
from bp.profiles import (TeamProfile, apply_overrides, assign_positions, build_profile, load_config, load_frames,
                         lookup_next_pick, lookup_response, player_hero_stats, response_edges)
from bp.recommend import candidates
from tests.test_phase2 import A_ACCTS, A_SIG, TEAM_A, TEAM_B   # the `league` fixture is exported by conftest.py


# ---------------------------------------------------------------- 8: the API key never reaches a log line
def test_network_errors_are_logged_without_the_api_key(tmp_path, monkeypatch, caplog):
    class Boom:
        headers = {}

        def get(self, url, params=None, timeout=None):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}?api_key={params['api_key']}&x=1")

    monkeypatch.setattr("bp.opendota.time.sleep", lambda s: None)
    c = OpenDota(api_key="SECRET-KEY-123", raw_dir=tmp_path, per_min=100000)
    c.session = Boom()
    with caplog.at_level(logging.WARNING, logger="bp.opendota"), pytest.raises(RuntimeError, match="gave up"):
        c._get("/matches/1", retries=2)
    assert caplog.text.count("api_key=***") == 2 and "SECRET-KEY-123" not in caplog.text


# ---------------------------------------------------------------- 10: KEY="value" in .env
def test_dotenv_strips_matching_quotes(tmp_path, monkeypatch):
    from bp.config import _load_dotenv
    (tmp_path / ".env").write_text('A_Q="abc"\nB_Q=\'x y\'\nC_Q=plain\nD_Q="unbalanced\n', encoding="utf-8")
    for k in ("A_Q", "B_Q", "C_Q", "D_Q"):
        monkeypatch.delenv(k, raising=False)
    _load_dotenv(tmp_path / ".env")
    import os
    assert (os.environ["A_Q"], os.environ["B_Q"], os.environ["C_Q"], os.environ["D_Q"]) == ("abc", "x y", "plain", '"unbalanced')


# ---------------------------------------------------------------- 6: vectorised assign_positions == the old per-group loop
def _assign_positions_reference(roster: pd.DataFrame) -> pd.Series:
    pos = pd.Series(np.nan, index=roster.index)
    for _, g in roster.groupby(["match_id", "side"], sort=False):
        g = g.sort_values("gpm", ascending=False)
        assigned: dict = {}
        free = {1, 2, 3, 4, 5}

        def take(idx, p):
            assigned[idx] = p
            free.discard(p)

        mids = g[g.lane_role == 2]
        if len(mids):
            take(mids.index[0], 2)
        for lane, top, rest in ((1, 1, 5), (3, 3, 4)):
            rows = g[(g.lane_role == lane) & (~g.index.isin(assigned))]
            for i, idx in enumerate(rows.index):
                p = top if i == 0 else rest
                if p in free:
                    take(idx, p)
        for idx in g.index:
            if idx not in assigned and free:
                take(idx, min(free))
        for idx, p in assigned.items():
            pos[idx] = p
    return pos.astype(int)


def test_assign_positions_matches_the_reference_loop():
    rng = random.Random(3)
    rows = []
    for mid in range(400):
        for side in (0, 1):
            n = rng.choice([5, 5, 5, 4, 3])
            gpms = rng.sample(range(200, 900), n)          # distinct GPM: the reference's unstable tie order is undefined
            for gpm in gpms:
                rows.append({"match_id": mid, "side": side, "lane_role": rng.choice([1, 1, 2, 3, 3, 4, 5, None]), "gpm": gpm})
    ro = pd.DataFrame(rows).sample(frac=1, random_state=1)   # shuffled row order, non-monotonic index
    ro["lane_role"] = ro.lane_role.astype(float)
    got, ref = assign_positions(ro), _assign_positions_reference(ro)
    assert got.index.equals(ro.index) and (got == ref).all()
    assert assign_positions(ro.iloc[0:0]).dtype.kind == "i"


# ---------------------------------------------------------------- 3 / 4: new scoring components, off by default
def test_position_fit_is_off_by_default_and_binds_picked_heroes_to_their_players(league):
    fr = load_frames(league, patch="7.41")
    stats = player_hero_stats(fr)
    A, B = build_profile(fr, TEAM_A, stats), build_profile(fr, TEAM_B, stats)
    assert A.open_positions([]) == A_ACCTS
    # picking 1001's signature hero removes 1001 from the open positions; an unknown hero binds nobody
    assert A.open_positions([A_SIG[1001], 999]) == [a for a in A_ACCTS if a != 1001]
    st = DraftState(load_format(league, "7.41"), frozenset(fr.heroes))
    for h in (60, 61, 62, 63, 64, 65, 66, 70):
        st.apply(h)
    assert st.next_is_pick and st.next_team == 0
    off = {c.hero_id: c for c in candidates(fr, st, A, B, k=200, context=False)}
    assert "position_fit" not in off[A_SIG[1001]].components
    fr_on = load_frames(league, patch="7.41", cfg=apply_overrides(load_config(), ["pick.position_fit=1.0"]))
    st.apply(A_SIG[1001])          # 1001 has a hero now; A picks again at this step of the format
    assert st.next_is_pick and st.next_team == 0
    on = {c.hero_id: c for c in candidates(fr_on, st, A, B, k=200, context=False)}
    assert on[A_SIG[1002]].components["position_fit"] == 1.0 and any("pool of" in e for e in on[A_SIG[1002]].evidence)
    assert 1001 not in A.open_positions(st.picks(0))
    pool = A.pool()
    only_1001 = [h for h in pool[1001] if h in st.legal() and not any(h in pool[a] for a in A_ACCTS if a != 1001)]
    assert only_1001 and all(on[h].components["position_fit"] == 0.0 for h in only_1001)   # 1001 is already served
    assert on[125].components["position_fit"] == 0.0                  # nobody on the roster plays it


def test_their_next_pick_uses_pick_only_edges(league):
    fr = load_frames(league, patch="7.41")
    edges = response_edges(fr, TEAM_B)
    picks = lookup_next_pick(edges, ())
    assert picks and all(h in set(A_SIG.values()) | set(range(40, 120)) for h, _, _ in picks)
    assert {h for h, _, _ in picks} <= {h for h, _, _ in lookup_response(edges, ())}
    ban_targets = {h for h, _, _ in lookup_next_pick(edges, ((0, 1, 60),))}
    assert ban_targets and not ban_targets & {60}
    stats = player_hero_stats(fr)
    A, B = build_profile(fr, TEAM_A, stats), build_profile(fr, TEAM_B, stats)
    st = DraftState(load_format(league, "7.41"), frozenset(fr.heroes))
    assert "their_next_pick" not in candidates(fr, st, A, B, k=1, context=False)[0].components
    fr_on = load_frames(league, patch="7.41", cfg=apply_overrides(load_config(), ["ban.their_next_pick=1.0"]))
    top = candidates(fr_on, st, A, B, k=5, context=False)
    assert top[0].action == "ban" and 0 < top[0].components["their_next_pick"] <= 1
    assert any("picked" in e and "next" in e for e in top[0].evidence)


def test_apply_overrides_rejects_unknown_paths():
    cfg = load_config()
    out = apply_overrides(cfg, ["pick.position_fit=1.5", "context.counter=0"])
    assert out["pick"]["position_fit"] == 1.5 and out["context"]["counter"] == 0 and cfg["pick"]["position_fit"] == 0.0
    for bad in ("pick.nope=1", "nope.x=1", "pick=1", "pick.position_fit"):
        with pytest.raises(ValueError):
            apply_overrides(cfg, [bad])


# ---------------------------------------------------------------- 2: ladder pair counts
def _ladder(n: int, seed: int):
    rng = random.Random(seed)
    rad, dire, win = [], [], []
    for _ in range(n):
        ten = rng.sample(range(1, 40), 10)
        rad.append(sorted(ten[:5])); dire.append(sorted(ten[5:])); win.append(int(rng.random() < 0.55))
    return np.array(rad), np.array(dire), np.array(win)


def test_count_pairs_matches_brute_force_and_build_tables_with_extra_equals_concatenation():
    rad, dire, win = _ladder(300, 1)
    pc = public.count_pairs(rad, dire, win)
    assert pc.n_matches == 300 and pc.radiant_wins == int(win.sum())
    a, b = 0, 0
    for r, d, w in zip(rad, dire, win):
        if r[0] < d[0]:
            a, b = int(r[0]), int(d[0]); break
    brute_n = sum(1 for r, d, w in zip(rad, dire, win) if (a in r and b in d) or (b in r and a in d))
    brute_w = sum(int(w if a in r else 1 - w) for r, d, w in zip(rad, dire, win) if (a in r and b in d) or (b in r and a in d))
    assert pc.ctr_n[(a, b)] == brute_n and pc.ctr_w[(a, b)] == brute_w
    s_n = sum(1 for r, d, w in zip(rad, dire, win) if (a in r and b in r) or (a in d and b in d))
    assert pc.syn_n.get((a, b), 0) == s_n
    assert pc.single_n[a] == int((rad == a).sum() + (dire == a).sum())
    # adding the ladder counts at weight 1 is exactly the same as training on pro + ladder matches together
    pro = [L.LineupMatch(i, tuple(r), tuple(d), int(w), i) for i, (r, d, w) in enumerate(zip(*_ladder(120, 2)))]
    lad = [L.LineupMatch(1000 + i, tuple(r), tuple(d), int(w), i) for i, (r, d, w) in enumerate(zip(rad, dire, win))]
    t_extra = L.build_tables(pro, k=30, extra=pc, extra_weight=1.0)
    t_all = L.build_tables(pro + lad, k=30)
    assert t_extra.counter.keys() == t_all.counter.keys() and t_extra.synergy.keys() == t_all.synergy.keys()
    for key, (e, n) in t_all.counter.items():
        assert t_extra.counter[key][0] == pytest.approx(e) and t_extra.counter[key][1] == pytest.approx(n)
    for h, (s, n) in t_all.single.items():
        assert t_extra.single[h][0] == pytest.approx(s) and t_extra.single[h][1] == pytest.approx(n)
    assert t_extra.base_rate != t_all.base_rate          # side advantage stays pro-only
    # weight 0 / no counts: untouched
    t0 = L.build_tables(pro, k=30, extra=pc, extra_weight=0.0)
    assert t0.counter == L.build_tables(pro, k=30).counter


def test_fit_folds_public_counts_in_only_when_weighted():
    pro = [L.LineupMatch(i, tuple(r), tuple(d), int(w), i) for i, (r, d, w) in enumerate(zip(*_ladder(200, 3)))]
    pc = public.count_pairs(*_ladder(2000, 4))
    plain = L.fit(pro, k=30)
    off = L.fit(pro, k=30, extra=pc, cfg={"public_weight": 0.0})
    on = L.fit(pro, k=30, extra=pc, cfg={"public_weight": 0.5})
    assert off.n_public == 0 and np.allclose(off.beta, plain.beta)
    assert on.n_public == 2000 and on.public_weight == 0.5
    key = next(iter(plain.tables.counter))
    assert on.tables.counter[key][1] > plain.tables.counter[key][1]          # ladder games raised the cell count
    md = L.to_markdown(L.evaluate(on, pro[:60]), "v", "7.41", "unit")
    assert "plus 2000 high-MMR ladder matches at weight 0.5" in md


def test_load_public_filters_and_caches(tmp_path):
    import json
    import sqlite3
    db = tmp_path / "public.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE public_matches (match_id INTEGER PRIMARY KEY, match_seq_num INTEGER, start_time INTEGER,
      duration INTEGER, radiant_win INTEGER, lobby_type INTEGER, game_mode INTEGER, avg_rank_tier INTEGER, num_rank_tier INTEGER,
      cluster INTEGER, patch TEXT, radiant TEXT, dire TEXT);""")
    rows = [(1, 0, 100, 0, 1, 7, 22, 70, 10, 0, "7.41", json.dumps([1, 2, 3, 4, 5]), json.dumps([6, 7, 8, 9, 10])),
            (2, 0, 200, 0, 0, 7, 22, 70, 10, 0, "7.41", json.dumps([1, 2, 3, 4, 5]), json.dumps([6, 7, 8, 9, 10])),
            (3, 0, 300, 0, 1, 7, 22, 70, 10, 0, "7.40", json.dumps([1, 2, 3, 4, 5]), json.dumps([6, 7, 8, 9, 10])),
            (4, 0, 150, 0, 1, 0, 1, 70, 10, 0, "7.41", json.dumps([1, 2, 3, 4, 5]), json.dumps([6, 7, 8, 9, 10]))]
    con.executemany("INSERT INTO public_matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    pc = public.load_public(db, "7.41")
    assert pc.n_matches == 2 and pc.ctr_n[(1, 6)] == 2 and pc.ctr_w[(1, 6)] == 1      # ranked AP on the patch only
    assert public.load_public(db, "7.41", as_of=150).n_matches == 1
    assert public.load_public(db, "7.41", ranked_only=False).n_matches == 3
    assert public.load_public(db, "7.99").n_matches == 0
    cached = public.load_public(db, "7.41")
    assert cached.ctr_n == pc.ctr_n and any(p.name.startswith("public_") for p in (tmp_path / "cache").iterdir())
    with pytest.raises(FileNotFoundError):
        public.load_public(tmp_path / "missing.sqlite", "7.41")


def test_context_uses_public_counts_only_with_a_weight(league):
    fr = load_frames(league, patch="7.41")
    fr.public = public.count_pairs(*_ladder(500, 5))
    ctx_off = build_context(fr)
    assert ctx_off.counter.get((1, 6), (0.0, 0)) == (0.0, 0)          # weight 0: ladder pairs invisible
    fr_on = load_frames(league, patch="7.41", cfg=apply_overrides(load_config(), ["context.public_weight=0.5"]))
    fr_on.public = fr.public
    ctx_on = build_context(fr_on)
    a, b = next(iter(fr.public.ctr_n))
    assert ctx_on.counter[(a, b)][1] >= fr.public.ctr_n[(a, b)]
    assert ctx_on.counter[(a, b)][0] == pytest.approx(-ctx_on.counter[(b, a)][0], abs=0.05)


# ---------------------------------------------------------------- 5: pickle cache
def test_cache_roundtrip_and_source_stamp(tmp_path, league):
    fr = load_frames(league, patch="7.41")
    A = build_profile(fr, TEAM_A, player_hero_stats(fr))
    A.sig(30); A.by_hero("hero_stats"); A.pool()                      # populate the un-picklable memo dicts
    cache.put("profile", "k1", A)
    back = cache.get("profile", "k1")
    assert isinstance(back, TeamProfile) and back.sig(30) == A.sig(30) and back.roster == A.roster
    assert cache.get("profile", "nope") is None
    (tmp_path / "cache" / "profile_k1.pkl").write_bytes(b"garbage")
    assert cache.get("profile", "k1") is None                        # corrupt entry -> rebuild, never crash
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    s1 = cache.source_stamp(pkg)
    (pkg / "a.py").write_text("x = 12\n")
    assert cache.source_stamp(pkg) != s1
    cache.ENABLED = False
    cache.put("x", "k", 1)
    assert cache.get("x", "k") is None
    cache.ENABLED = True
    cache.put("x", "k", object())                                    # unpicklable: warning, no entry, no exception
    assert cache.get("x", "k") is None or pickle.loads(pickle.dumps(1)) == 1


def test_cli_report_reuses_cached_frames_and_profiles(tmp_path, league, monkeypatch):
    import bp.__main__ as cli
    import bp.profiles as profiles
    db = tmp_path / "copy.sqlite"
    league.execute("VACUUM INTO ?", (str(db),))
    calls = {"frames": 0, "profiles": 0}
    real_load, real_build = profiles.load_frames, profiles.build_profile

    def counting_load(*a, **kw):
        calls["frames"] += 1; return real_load(*a, **kw)

    def counting_build(*a, **kw):
        calls["profiles"] += 1; return real_build(*a, **kw)

    monkeypatch.setattr(profiles, "load_frames", counting_load)
    monkeypatch.setattr(profiles, "build_profile", counting_build)
    from types import SimpleNamespace
    for _ in range(2):
        a = SimpleNamespace(db=str(db), us=str(TEAM_A), them=str(TEAM_B), patch="7.41", as_of=None,
                            out=str(tmp_path / "r.md"), stdout=False)
        cli.cmd_report(a)
    assert calls == {"frames": 1, "profiles": 2}
    assert "Reference only" in (tmp_path / "r.md").read_text(encoding="utf-8")
    cli.cmd_report(SimpleNamespace(db=str(db), us=str(TEAM_A), them=str(TEAM_B), patch="7.41", as_of=None,
                                   out=str(tmp_path / "r.md"), stdout=False, weight=["pick.position_fit=1.0"]))
    assert calls["frames"] == 2                                       # a weight override is a different cache key
