"""Phase 2 on a synthetic league: two teams with distinct signature heroes, deterministic drafts."""
from __future__ import annotations
import random
from dataclasses import replace
from importlib import resources

import pandas as pd
import pytest

from bp.db import connect
from bp.config import CONFIG
from bp.draft_formats import infer_formats, load_format
from bp.draft_state import DraftState
from bp.normalize import normalize_match
from bp.profiles import (load_frames, player_hero_stats, build_profile, ban_pressure, response_edges, meta_ban_rates, signature_scores,
                         lookup_response, current_roster, load_config, TeamProfile)
from bp.quality import run_checks
from bp.recommend import candidates
from bp.report import build_report, _lists_section
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
    assert fr.team_id(str(TEAM_A)) == TEAM_A
    assert fr.team_id("999999") is None
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


def test_current_roster_keeps_one_player_per_position_after_substitution(league):
    fr = load_frames(league, patch="7.41")
    latest = fr.roster[(fr.roster.team_id == TEAM_A)].sort_values("start_time", ascending=False).head(5)
    sub_rows = latest.copy()
    sub_rows["account_id"] = 1999
    sub_rows["pos"] = 1
    sub_rows["gpm"] = sub_rows["gpm"] + 1
    fr2 = replace(fr, roster=pd.concat([fr.roster, sub_rows], ignore_index=True), players={**fr.players, 1999: "sub1999"})
    roster = current_roster(fr2, TEAM_A)
    by_acct = fr2.roster[fr2.roster.account_id.isin(roster)].groupby("account_id").pos.agg(lambda s: int(s.mode().iloc[0]))
    assert len(roster) == 5
    assert sorted(by_acct.tolist()) == [1, 2, 3, 4, 5]


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
    assert lookup_response(edges, ((0, 1, 60), (0, 0, 61), (0, 1, 62), (0, 0, 63)))
    assert lookup_response(edges, ((1, 0, 125),))
    # pick turn: A's recommended pick is one of its own signature heroes
    for h in (60, 61, 62, 63, 64, 65, 66):
        st.apply(h)
    assert st.next_is_pick and st.next_team == 1     # B picks first in SEQ
    st.apply(70)
    cs = candidates(fr, st, A, B, k=3)
    assert cs[0].action == "pick" and cs[0].hero_id in A_SIG.values()


def _profile(team_id: int, stats=None, ban_pressure_df=None, hero_stats=None) -> TeamProfile:
    empty_stats = pd.DataFrame(columns=["account_id", "hero_id", "signature", "games"])
    empty_bp = pd.DataFrame(columns=["hero_id", "phase0_rate", "bans", "games", "phase0", "match_ids", "targeted_lift"])
    empty_hs = pd.DataFrame(columns=["hero_id", "pick_rate", "win_lift", "picks", "wins", "wr", "match_ids"])
    return TeamProfile(team_id, f"T{team_id}", [], 0, stats if stats is not None else empty_stats,
                       ban_pressure_df if ban_pressure_df is not None else empty_bp, pd.DataFrame(),
                       hero_stats if hero_stats is not None else empty_hs,
                       {"counters": {}, "match_ids": {}, "games_w_by_first": {}, "games": 0},
                       pd.DataFrame(columns=["count", "lift"]), {})


def test_candidate_evidence_drives_samples_match_ids_and_tie_order(league):
    fr = load_frames(league, patch="7.41")
    fr.heroes.update({98: "Hero98", 99: "Hero99"})
    us = _profile(
        TEAM_A,
        ban_pressure_df=pd.DataFrame([{"hero_id": 99, "phase0_rate": 0.4, "bans": 4, "games": 10, "phase0": 4,
                                       "match_ids": (9004, 9003), "targeted_lift": 0.2}]),
        hero_stats=pd.DataFrame([{"hero_id": 98, "pick_rate": 0.2, "win_lift": 0.1, "picks": 2, "wins": 1,
                                  "wr": 0.55, "match_ids": (8002, 8001)}]),
    )
    them = _profile(TEAM_B)
    st = DraftState(((1, 0),), frozenset({98, 99}))
    by_hero = {c.hero_id: c for c in candidates(fr, st, us, them, k=2, context=False)}
    assert by_hero[99].samples == 4 and by_hero[99].confidence == pytest.approx(4 / 9)
    assert by_hero[99].match_ids == [9004, 9003]
    assert by_hero[98].samples == 2 and by_hero[98].match_ids == [8002, 8001]

    tied = candidates(fr, DraftState(((1, 0),), frozenset({3, 1, 2})), _profile(TEAM_A), _profile(TEAM_B),
                      k=3, context=False)
    assert [c.hero_id for c in tied] == [1, 2, 3]


def test_report_dedupes_protect_steal_and_habits_show_match_ids(league):
    fr = load_frames(league, patch="7.41")
    fr.players.update({3001: "a", 3002: "b", 4001: "c", 4002: "d"})
    us = _profile(
        TEAM_A,
        stats=pd.DataFrame([
            {"account_id": 3001, "hero_id": 30, "signature": 0.9, "games": 3},
            {"account_id": 3002, "hero_id": 30, "signature": 0.8, "games": 3},
            {"account_id": 3002, "hero_id": 40, "signature": 0.5, "games": 3},
        ]),
        ban_pressure_df=pd.DataFrame([{"hero_id": 30, "targeted_lift": 0.3}]),
    )
    them = _profile(
        TEAM_B,
        stats=pd.DataFrame([
            {"account_id": 4001, "hero_id": 40, "signature": 0.7, "games": 3},
            {"account_id": 4002, "hero_id": 40, "signature": 0.6, "games": 3},
        ]),
    )
    lines = _lists_section(fr, us, them)
    assert next(x for x in lines if x.startswith("**Protect")).count("Hero30") == 1
    assert next(x for x in lines if x.startswith("**Steal")).count("Hero40") == 1

    stats = player_hero_stats(fr)
    A, B = build_profile(fr, TEAM_A, stats), build_profile(fr, TEAM_B, stats)
    md = build_report(fr, A, B, load_format(league, "7.41"), "7.41", "testver")
    habit_lines = [x for x in md.splitlines() if x.startswith("- phase-1")]
    habit_ids = {str(mid) for by_hero in A.habits["match_ids"].values()
                 for mids in by_hero.values() for mid in mids}
    assert habit_lines and any(any(mid in line for mid in habit_ids) for line in habit_lines)


def test_default_scoring_config_loads_from_package_resource():
    cfg = load_config()
    assert cfg["evidence"]["top_k"] == 5
    assert resources.files("bp").joinpath("scoring.yaml").read_text(encoding="utf-8") == (
        CONFIG.root / "config" / "scoring.yaml").read_text(encoding="utf-8")


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


def test_context_actions_can_apply_only_to_bans(league):
    fr = load_frames(league, patch="7.41")
    stats = player_hero_stats(fr)
    A, B = build_profile(fr, TEAM_A, stats), build_profile(fr, TEAM_B, stats)

    ban_state = DraftState(((1, 0), (1, 1), (0, 0)), frozenset(fr.heroes))
    ban_state.apply(31).apply(41)
    ban_only = candidates(fr, ban_state, A, B, k=5, context_actions={"ban"})
    ban_off = candidates(fr, ban_state, A, B, k=5, context=False)
    assert "counter" in ban_only[0].components
    assert "counter" not in ban_off[0].components

    pick_state = DraftState(((1, 0), (1, 1), (1, 0)), frozenset(fr.heroes))
    pick_state.apply(31).apply(41)
    pick_ban_only = candidates(fr, pick_state, A, B, k=5, context_actions={"ban"})
    pick_off = candidates(fr, pick_state, A, B, k=5, context=False)
    assert [(c.hero_id, c.score, c.components) for c in pick_ban_only] == [
        (c.hero_id, c.score, c.components) for c in pick_off
    ]


def test_h5_page_builds(league, tmp_path):
    import json
    import re
    from bp.h5 import ladder_payload, matchup_payload, render, md_to_html
    fr = load_frames(league, patch="7.41")
    stats = player_hero_stats(fr)
    A, B = build_profile(fr, TEAM_A, stats), build_profile(fr, TEAM_B, stats)
    fmt = load_format(league, "7.41")
    md = build_report(fr, A, B, fmt, "7.41", "testver")
    lad = ladder_payload(fr, league, fmt, download_icons=False)
    page = render(lad, [matchup_payload(fr, A, B, md)], {"patch": "7.41", "matches": 1, "as_of": "x", "data_version": "testver"})
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1).replace(r"<\/", "</"))
    assert len(data["ladder"]["cm_seq"]) == len(fmt) and data["ladder"]["counter"] and data["ladder"]["synergy"]
    assert {t["id"] for t in data["teams"]} == {TEAM_A, TEAM_B}
    assert 'id="us-team"' in page and 'id="them-team"' in page and 'id="swap-teams"' in page
    assert "const esc =" in page and "离线包至少需要 2 支战队" in page
    m = data["matchups"][0]
    assert m["us"]["name"] == A.name and m["them"]["roster"] and str(FIRST_BAN_VS_A) in m["us"]["sig"]
    assert "<details" in m["report_html"]
    assert "opendota.com/matches/8960762254" in md_to_html("- x — 8960762254")   # real-looking ids become links
    # markdown subset round-trips (nested lists close properly)
    h = md_to_html("## S\n\n- a\n  - b\n- c\n")
    assert h.count("<ul>") == h.count("</ul>") == 2 and h.count("<li>") == 3


def test_h5_md_inline_rules():
    from bp.h5 import md_to_html
    # underscores inside player names must not become italics
    h = md_to_html("- **not_me** (pos 4) and **some_name** here")
    assert "<i>" not in h and "<b>not_me</b>" in h
    assert "<i>no roster found</i>" in md_to_html("_no roster found_")
    # only ids in the report's "— id, id" list format become match links; bare numbers (account ids) do not
    assert "opendota.com" not in md_to_html("- player 123456789 played 30 games")
    h = md_to_html("- x — 8960762254, 8960762255")
    assert h.count("opendota.com/matches/") == 2


def test_blindtest_fixed_set_errors_on_event_count_mismatch(league):
    from bp.blindtest import blind_test
    as_of = 1_710_000_000 + 10 * 86400
    mid = league.execute("SELECT match_id FROM matches WHERE excluded=0 AND start_time >= ? LIMIT 1", (as_of,)).fetchone()[0]
    league.execute("INSERT INTO draft_events (match_id, order_no, team_side, is_pick, hero_id, phase) VALUES (?,99,0,1,1,9)", (mid,))
    league.commit()
    with pytest.raises(ValueError, match="draft events"):
        blind_test(league, league, as_of=as_of, patch="7.41", test_match_ids=[mid])
