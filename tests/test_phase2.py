"""Phase 2 on a synthetic league: two teams with distinct signature heroes, deterministic drafts."""
from __future__ import annotations
import random

import pytest

from bp.db import connect
from bp.draft_formats import infer_formats, load_format
from bp.draft_state import DraftState
from bp.normalize import normalize_match
from bp.profiles import (load_frames, player_hero_stats, build_profile, ban_pressure, response_edges, meta_ban_rates, signature_scores,
                         lookup_response, current_roster)
from bp.quality import run_checks
from bp.recommend import candidates
from bp.report import build_report
from tests.test_pipeline import SEQ

TEAM_A, TEAM_B = 100, 200
A_ACCTS = [1001, 1002, 1003, 1004, 1005]
B_ACCTS = [2001, 2002, 2003, 2004, 2005]
A_SIG = {1001: 30, 1002: 31, 1003: 32, 1004: 33, 1005: 34}   # each A player's signature hero
B_SIG = {2001: 40, 2002: 41, 2003: 42, 2004: 43, 2005: 44}
FIRST_BAN_VS_A = 30     # everybody first-bans hero 30 against team A
B_FIRST_BAN = 44 + 1    # team B always opens with ban 45 when first


def synth_match(mid, start, a_side, first_team, rng: random.Random, a_wins=True):
    """Team A on side a_side (0 radiant). `first_team` = side that acts first."""
    fmt = SEQ
    used = set()
    pb = []
    a_picks = list(A_SIG.values()); b_picks = list(B_SIG.values())
    for i, (is_pick, rel) in enumerate(fmt):
        side = rel if first_team == 0 else 1 - rel
        is_a = side == a_side
        if is_pick:
            pool = a_picks if is_a else b_picks
            h = next(x for x in pool if x not in used) if any(x not in used for x in pool) else next(x for x in range(50, 120) if x not in used)
        else:
            if i == 0 and not is_a and rng.random() < 0.9:
                h = FIRST_BAN_VS_A
            elif i == 0 and is_a:
                h = 60
            elif not is_a and side == (first_team) and i == 0:
                h = B_FIRST_BAN
            else:
                h = rng.choice([x for x in range(50, 120) if x not in used])
        used.add(h)
        pb.append({"order": i, "is_pick": bool(is_pick), "team": side, "hero_id": h})
    players = []

    def assign(picked: list[int], sig: dict) -> list[int]:
        """Each player gets their signature hero if it was picked, else a leftover filler."""
        left = [h for h in picked if h not in sig.values()]
        return [sig[a] if sig[a] in picked else left.pop(0) for a in sig]

    a_heroes = assign([x["hero_id"] for x in pb if x["is_pick"] and x["team"] == a_side], A_SIG)
    b_heroes = assign([x["hero_id"] for x in pb if x["is_pick"] and x["team"] != a_side], B_SIG)
    for k in range(10):
        side = 0 if k < 5 else 1
        slot = k if k < 5 else 128 + (k - 5)
        is_a = side == a_side
        accts = A_ACCTS if is_a else B_ACCTS
        heroes = a_heroes if is_a else b_heroes
        j = k % 5
        players.append({"account_id": accts[j], "player_slot": slot, "hero_id": heroes[j], "gold_per_min": 700 - 60 * j,
                        "lane_role": [1, 2, 3, 3, 1][j], "name": f"p{accts[j]}"})
    radiant_win = (a_side == 0) == a_wins
    r_team, d_team = (TEAM_A, TEAM_B) if a_side == 0 else (TEAM_B, TEAM_A)
    return {"match_id": mid, "start_time": start, "duration": 2400, "patch": 58, "leagueid": 1, "league": {"name": "L"},
            "radiant_team_id": r_team, "dire_team_id": d_team, "radiant_team": {"team_id": r_team, "name": f"T{r_team}"},
            "dire_team": {"team_id": d_team, "name": f"T{d_team}"}, "radiant_win": radiant_win, "picks_bans": pb,
            "players": players, "draft_timings": []}


@pytest.fixture
def league(tmp_path):
    con = connect(tmp_path / "l.sqlite")
    con.execute("INSERT INTO patches VALUES (58, '7.41', ?)", (1_700_000_000,))
    con.executemany("INSERT INTO heroes VALUES (?,?,?,?,?)", [(i, f"npc_{i}", f"Hero{i}", "str", "[]") for i in range(1, 130)])
    rng = random.Random(7)
    base = 1_710_000_000
    for i in range(30):
        normalize_match(con, synth_match(1000 + i, base + i * 86400, a_side=i % 2, first_team=(i // 2) % 2, rng=rng,
                                         a_wins=(i % 3 != 0)))
    con.commit()
    infer_formats(con, min_support=5)
    run_checks(con)
    return con


def test_profiles_and_signatures(league):
    fr = load_frames(league, patch="7.41")
    assert len(fr.matches) == 30 and fr.roster.pos.isin([1, 2, 3, 4, 5]).all()
    assert current_roster(fr, TEAM_A) == A_ACCTS
    stats = player_hero_stats(fr)
    A = build_profile(fr, TEAM_A, stats)
    # every A player's top signature is their scripted hero
    for acct, hero in A_SIG.items():
        top = A.stats[A.stats.account_id == acct].iloc[0]
        assert int(top.hero_id) == hero
    # ban pressure: hero 30 is first-banned against A almost always
    bp = ban_pressure(fr, TEAM_A)
    assert int(bp.iloc[0].hero_id) == FIRST_BAN_VS_A and bp.iloc[0].phase0_rate > 0.35
    # every match in the league is A vs B, so the ban rate vs A equals the patch-wide rate: nothing is "targeted"
    assert bp.iloc[0].global_phase0_rate == pytest.approx(bp.iloc[0].phase0_rate)
    assert bp.iloc[0].targeted_lift == pytest.approx(0.0)
    assert (A.stats.targeted_ban_pressure.abs() < 1e-9).all() and A.stats.tag.notna().all()
    # regression: a roster hero absent from the team's ban table must keep its patch-wide rate (not collapse to 0)
    gp0, _ = meta_ban_rates(fr)
    st = signature_scores(stats[stats.account_id.isin(A_ACCTS)], fr, bp[bp.hero_id != FIRST_BAN_VS_A])
    row = st[st.hero_id == FIRST_BAN_VS_A].iloc[0]
    assert row.meta_ban_rate == pytest.approx(gp0[FIRST_BAN_VS_A]) and row.meta_ban_rate > 0.35
    assert row.ban_rate_vs_team == 0 and row.targeted_ban_pressure == 0 and "meta" in row.tag


def test_candidates_and_edges(league):
    fr = load_frames(league, patch="7.41")
    stats = player_hero_stats(fr)
    A, B = build_profile(fr, TEAM_A, stats), build_profile(fr, TEAM_B, stats)
    fmt = load_format(league, "7.41")
    st = DraftState(fmt, frozenset(fr.heroes))
    # A acts first: recommended first ban is one of B's signature heroes
    top = candidates(fr, st, A, B, k=5)
    assert top and top[0].action == "ban" and top[0].hero_id in B_SIG.values()
    assert top[0].evidence and top[0].match_ids and not top[0].insufficient
    # a hero nobody plays scores 0 and is flagged insufficient
    st2 = DraftState(fmt, frozenset(fr.heroes))
    all_c = candidates(fr, st2, A, B, k=200)
    zero = [c for c in all_c if c.hero_id == 125]
    assert zero and zero[0].score == 0 and zero[0].insufficient
    # response edges: after A opens with ban 60, B's next ban is looked up
    edges = response_edges(fr, TEAM_B)
    resp = lookup_response(edges, ((0, 1, 60),))
    assert resp and all(n >= 1 for _, _, n in resp)
    # pick turn: A's recommended pick is one of its own signature heroes
    for h in (60, 61, 62, 63, 64, 65, 66):
        st.apply(h)
    assert st.next_is_pick and st.next_team == 1     # B picks first in SEQ
    st.apply(70)
    cs = candidates(fr, st, A, B, k=3)
    assert cs[0].action == "pick" and cs[0].hero_id in A_SIG.values()


def test_as_of_excludes_future_and_report_builds(league):
    fr_all = load_frames(league, patch="7.41")
    cut = int(fr_all.matches.start_time.sort_values().iloc[10])
    fr = load_frames(league, as_of=cut, patch="7.41")
    assert len(fr.matches) == 10 and fr.matches.start_time.max() < cut
    stats = player_hero_stats(fr)
    A, B = build_profile(fr, TEAM_A, stats), build_profile(fr, TEAM_B, stats)
    md = build_report(fr, A, B, load_format(league, "7.41"), "7.41", "testver")
    assert "## Rosters" in md and "## Targeted bans" in md and "## Opening recommendations" in md
    assert "Hero30" in md and "testver" in md


def test_context_terms(league):
    from bp.context import build_context
    fr = load_frames(league, patch="7.41")
    ctx = build_context(fr)
    # team A wins 2/3 of games; its signature heroes counter B's and synergize with each other
    eff, det = ctx.counter_vs(31, [41, 42])
    assert eff > 0 and all(n >= 5 for _, _, n in det)
    assert ctx.counter[(41, 31)][0] == pytest.approx(-ctx.counter[(31, 41)][0], abs=0.05)
    syn, _ = ctx.synergy_with(31, [32, 33])
    assert syn > 0
    # unseen pair is exactly zero and has no samples
    assert ctx.counter.get((31, 125), (0.0, 0)) == (0.0, 0)
    # gap: with no roles data every hero fills nothing
    assert ctx.gap_fill(31, [32])[0] == 0.0
    # context changes the pick ranking once picks exist and can be switched off
    stats = player_hero_stats(fr)
    A, B = build_profile(fr, TEAM_A, stats), build_profile(fr, TEAM_B, stats)
    st = DraftState(load_format(league, "7.41"), frozenset(fr.heroes))
    for h in (60, 61, 62, 63, 64, 65, 66, 70):
        st.apply(h)
    on = candidates(fr, st, A, B, k=5)
    off = candidates(fr, st, A, B, k=5, context=False)
    assert "counter" in on[0].components and "counter" not in off[0].components
    assert on[0].samples == off[0].samples  # evidence loop must not clobber the sample count
