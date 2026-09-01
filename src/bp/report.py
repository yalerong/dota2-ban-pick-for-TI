"""Pre-match scouting report in Markdown (P2-09)."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from .draft_state import DraftState
from .profiles import Frames, TeamProfile, team_matches
from .recommend import candidates


def _ts(t: int) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d")


def _ids(ids) -> str:
    return ", ".join(str(int(i)) for i in list(ids)[:5])


def _roster_section(fr: Frames, P: TeamProfile, top_n: int = 6) -> list[str]:
    L = [f"### {P.name} — {P.games} games in window, roster from last {fr.cfg['decay']['roster_window_games']} games", ""]
    if not P.roster:
        return L + ["_no roster found_", ""]
    for acct in P.roster:
        s = P.stats[P.stats.account_id == acct].sort_values("signature", ascending=False)
        if not len(s):
            L.append(f"- **{fr.player(acct)}** — no data"); continue
        pos = int(s.pos.mode().iloc[0]); games = int(s.games.sum()); pool = int((s.games >= 2).sum())
        L.append(f"- **{fr.player(acct)}** (pos {pos}) — {games} games, effective pool {pool} heroes")
        for r in s.head(top_n).itertuples():
            tag = " ★" if r.signature >= 0.4 else ""
            L.append(f"  - {fr.hero(r.hero_id)}: {int(r.games)}g {int(r.wins)}w, smoothed {100*r.wr:.0f}%, lift {r.relative_win_lift:+.2f}, "
                     f"banned vs us {100*r.ban_rate_vs_team:.0f}% (meta {100*r.meta_ban_rate:.0f}%), sig {r.signature:.2f} [{r.tag}]{tag} — {_ids(r.match_ids)}")
    return L + [""]


def _habits_section(fr: Frames, P: TeamProfile) -> list[str]:
    L = [f"### {P.name} — draft habits", ""]
    c = P.habits["counters"]
    ids = P.habits.get("match_ids", {})
    gw = P.habits["games_w_by_first"]
    for first, label in ((1, "when acting first"), (0, "when acting second")):
        g = gw.get(first, 0)
        if g <= 0:
            continue
        L.append(f"**{label}** (weighted games {g:.1f})")
        for phase, kind, title in ((0, 0, "phase-1 bans"), (1, 1, "phase-1 picks"), (2, 0, "phase-2 bans"), (3, 1, "phase-2 picks")):
            cnt = Counter(c.get((phase, first, kind), {}))
            if cnt:
                key = (phase, first, kind)
                items = sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[:6]
                L.append(f"- {title}: " + ", ".join(
                    f"{fr.hero(h)} {100*w/g:.0f}% — {_ids(ids.get(key, {}).get(h, ()))}" for h, w in items))
        L.append("")
    hs = P.hero_stats.head(8)
    if len(hs):
        L.append("**most picked overall**: " + ", ".join(
            f"{fr.hero(r.hero_id)} {int(r.picks)}x ({100*r.wr:.0f}%)" for r in hs.itertuples()))
    pr = P.pairs[(P.pairs["count"] >= 3) & (P.pairs.lift >= 1.5)].head(6)
    if len(pr):
        L.append("**system pairs** (picked together > independence): " + ", ".join(
            f"{fr.hero(r.hero_a)}+{fr.hero(r.hero_b)} {int(r.count)}x ({int(r.wins)}w, lift {r.lift:.1f})" for r in pr.itertuples()))
    return L + [""]


def _pressure_section(fr: Frames, us: TeamProfile, them: TeamProfile) -> list[str]:
    L = ["## Targeted bans", ""]
    L.append(f"**Opponents ban against {them.name}** (phase-1 share of their games):")
    for r in them.ban_pressure.head(8).itertuples():
        L.append(f"- {fr.hero(r.hero_id)}: {int(r.phase0)}/{int(r.games)} phase-1 ({100*r.phase0_rate:.0f}% vs meta {100*r.global_phase0_rate:.0f}%, "
                 f"targeted {100*r.targeted_lift:+.0f}pt), {int(r.bans)} total — {_ids(r.match_ids)}")
    L.append("")
    L.append(f"**Opponents ban against {us.name}**:")
    for r in us.ban_pressure.head(8).itertuples():
        L.append(f"- {fr.hero(r.hero_id)}: {int(r.phase0)}/{int(r.games)} phase-1 ({100*r.phase0_rate:.0f}% vs meta {100*r.global_phase0_rate:.0f}%, "
                 f"targeted {100*r.targeted_lift:+.0f}pt), {int(r.bans)} total")
    L.append("")
    h2h = team_matches(fr, us.team_id)
    h2h = h2h[h2h.opp == them.team_id]
    if len(h2h):
        L.append(f"**Head-to-head**: {len(h2h)} games, {int(h2h.won.sum())} wins for {us.name} — {_ids(h2h.match_id)}")
        L.append("")
    return L


def _lists_section(fr: Frames, us: TeamProfile, them: TeamProfile) -> list[str]:
    L = ["## Protect / steal / release", ""]
    our = us.stats[us.stats.games >= 3].sort_values("signature", ascending=False)
    their = them.stats[them.stats.games >= 3].sort_values("signature", ascending=False)
    bp_us = us.ban_pressure.set_index("hero_id").targeted_lift.clip(lower=0) if len(us.ban_pressure) else pd.Series(dtype=float)
    protect = (our.assign(bp=bp_us.reindex(our.hero_id).fillna(0).values)
               .sort_values(["bp", "signature", "hero_id"], ascending=[False, False, True])
               .drop_duplicates("hero_id")
               .head(6))
    L.append("**Protect (our signatures that get banned)**: " + ", ".join(
        f"{fr.hero(r.hero_id)} [{fr.player(r.account_id)}] sig {r.signature:.2f}, targeted {100*r.bp:+.0f}pt" for r in protect.itertuples()))
    shared = their.merge(our[["hero_id", "account_id", "signature"]], on="hero_id", suffixes=("_them", "_us"))
    steal = (shared.sort_values(["signature_them", "signature_us", "hero_id"], ascending=[False, False, True])
             .drop_duplicates("hero_id")
             .head(6))
    L.append("**Steal (their signatures our roster also plays)**: " + (", ".join(
        f"{fr.hero(r.hero_id)} [{fr.player(r.account_id_them)} → {fr.player(r.account_id_us)}] sig {r.signature_them:.2f}/{r.signature_us:.2f}"
        for r in steal.itertuples()) or "none"))
    hs = them.hero_stats[(them.hero_stats.picks >= 4) & (them.hero_stats.win_lift < -0.03)].head(6)
    L.append("**Release (they pick it but underperform)**: " + (", ".join(
        f"{fr.hero(r.hero_id)} {int(r.picks)}x {100*r.wr:.0f}% (lift {r.win_lift:+.2f})" for r in hs.itertuples()) or "none"))
    return L + [""]


def _opening_section(fr: Frames, us: TeamProfile, them: TeamProfile, fmt) -> list[str]:
    L = ["## Opening recommendations", ""]
    for first, label in ((0, "if we act first"), (1, "if they act first")):
        st = DraftState(fmt, frozenset(fr.heroes))
        if first == 1:
            # they act first (state team 0 == them): simulate their most likely actions until it is our turn
            simulated = []
            while not st.done and st.next_team == 0:
                c0 = candidates(fr, st, them, us, k=1)
                if not c0:
                    break
                simulated.append(f"{c0[0].action} {c0[0].hero}")
                st.apply(c0[0].hero_id)
            L.append(f"**{label}** — their likely opening: {', '.join(simulated) or '?'}; then for us:")
            cs = candidates(fr, st, them, us, k=3)
        else:
            L.append(f"**{label}**")
            cs = candidates(fr, st, us, them, k=3)
        for c in cs:
            flag = " (insufficient evidence)" if c.insufficient else ""
            L.append(f"- {c.action} **{c.hero}** — score {c.score:.2f}, conf {c.confidence:.2f}{flag}")
            for e in c.evidence[:3]:
                L.append(f"  - {e}")
            if c.predicted_response:
                L.append(f"  - likely response: {', '.join(c.predicted_response)}")
        L.append("")
    return L


def build_report(fr: Frames, us: TeamProfile, them: TeamProfile, fmt, patch: str | None, data_version: str | None) -> str:
    L = [f"# {us.name} vs {them.name} — scouting report", "",
         f"patch {patch or 'all'} · data as of {_ts(fr.as_of)} · {len(fr.matches)} clean matches in window · data_version {data_version or 'live-db'}", "",
         "## Rosters and hero pools", ""]
    L += _roster_section(fr, us) + _roster_section(fr, them)
    L += ["## Draft habits", ""] + _habits_section(fr, them) + _habits_section(fr, us)
    L += _pressure_section(fr, us, them)
    L += _lists_section(fr, us, them)
    if fmt:
        L += _opening_section(fr, us, them, fmt)
    L += ["---", "Smoothed WR = Beta-smoothed with the hero's pro win rate as prior; lift = vs the player's/team's own baseline; "
          "all counts are time-decayed (half-life "
          f"{fr.cfg['decay']['half_life_days']}d). Every line lists up to 5 match ids for verification on OpenDota. "
          "Phase labels are 1-based: phase-1 is the first ban/pick run of the draft."]
    return "\n".join(L)
