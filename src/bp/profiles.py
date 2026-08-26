"""Player / team profiles (P2-01, P2-03, P2-04, P2-05). Pure computation over the normalized store."""
from __future__ import annotations
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import CONFIG

DAY = 86400


def load_config(path: Path | None = None) -> dict:
    p = path or CONFIG.root / "config" / "scoring.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- frames
@dataclass
class Frames:
    matches: pd.DataFrame
    events: pd.DataFrame
    roster: pd.DataFrame
    heroes: dict[int, str]
    players: dict[int, str]
    teams: dict[int, str]
    patch_rank: dict[str, int]
    as_of: int
    cfg: dict = field(default_factory=dict)
    roles: dict = field(default_factory=dict)
    _ctx: object = field(default=None, repr=False)

    def context(self):
        """Lazily built draft-context matrices (counter / synergy / role gaps)."""
        if self._ctx is None:
            from .context import build_context
            self._ctx = build_context(self)
        return self._ctx

    def hero(self, hid: int) -> str:
        return self.heroes.get(int(hid), f"hero_{hid}")

    def player(self, acct) -> str:
        return self.players.get(int(acct), str(acct)) if acct is not None and not pd.isna(acct) else "anon"

    def team(self, tid) -> str:
        return self.teams.get(int(tid), str(tid))

    def hero_id(self, name: str) -> int | None:
        q = name.strip().lower()
        if q.isdigit():
            return int(q) if int(q) in self.heroes else None
        exact = [h for h, n in self.heroes.items() if n.lower() == q]
        if exact:
            return exact[0]
        pref = [h for h, n in self.heroes.items() if n.lower().startswith(q)]
        if len(pref) == 1:
            return pref[0]
        sub = [h for h, n in self.heroes.items() if q in n.lower()]
        return sub[0] if len(sub) == 1 else None

    def team_id(self, name: str) -> int | None:
        q = str(name).strip().lower()
        if q.isdigit():
            return int(q)
        exact = [t for t, n in self.teams.items() if (n or "").lower() == q]
        if exact:
            return exact[0]
        sub = [t for t, n in self.teams.items() if q in (n or "").lower()]
        return sub[0] if len(sub) == 1 else None


def assign_positions(roster: pd.DataFrame) -> pd.Series:
    """1..5 per (match, side) using lane_role + GPM: mid=2; safe lane top GPM=1 else 5; off lane top GPM=3 else 4;
    leftovers fill the missing positions by GPM order."""
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


def load_frames(con: sqlite3.Connection, as_of: int | None = None, patch: str | None = None,
                include_flagged: bool = False, cfg: dict | None = None) -> Frames:
    cfg = cfg or load_config()
    where = ["1=1"] if include_flagged else ["excluded=0"]
    args: list = []
    if as_of is not None:
        where.append("start_time < ?"); args.append(as_of)
    if patch:
        where.append("patch = ?"); args.append(patch)
    cond = " AND ".join(f"m.{w}" if w[0].isalpha() else w for w in where)
    m = pd.read_sql_query(f"SELECT m.match_id, m.patch, m.start_time, m.radiant_team_id, m.dire_team_id, m.radiant_win, "
                          f"m.league_name FROM matches m WHERE {cond}", con, params=args)
    # join instead of IN (...) so a full-patch window (>7k ids) never hits SQLite's bound-variable limit
    ev = pd.read_sql_query(f"SELECT d.match_id, d.order_no, d.team_side, d.is_pick, d.hero_id, d.phase FROM draft_events d "
                           f"JOIN matches m ON m.match_id = d.match_id WHERE {cond}", con, params=args)
    ro = pd.read_sql_query(f"SELECT r.match_id, r.team_id, r.account_id, r.side, r.hero_id, r.lane_role, r.gpm "
                           f"FROM roster_snapshots r JOIN matches m ON m.match_id = r.match_id WHERE {cond}", con, params=args)
    heroes = {r[0]: r[1] for r in con.execute("SELECT hero_id, localized_name FROM heroes")}
    import json as _json
    roles = {r[0]: set(_json.loads(r[1] or "[]")) for r in con.execute("SELECT hero_id, roles FROM heroes")}
    players = {r[0]: r[1] for r in con.execute("SELECT account_id, name FROM players")}
    teams = {r[0]: r[1] for r in con.execute("SELECT team_id, name FROM teams")}
    patch_rank = {r[0]: i for i, r in enumerate(con.execute("SELECT name FROM patches ORDER BY release_time"))}
    if as_of is None:
        as_of = int(m.start_time.max()) + 1 if len(m) else 0

    # events: acting team id, won, first actor
    mi = m.set_index("match_id")
    ev["team_id"] = np.where(ev.team_side == 0, mi.radiant_team_id.reindex(ev.match_id).values,
                             mi.dire_team_id.reindex(ev.match_id).values)
    ev["won"] = ((ev.team_side == 0) == mi.radiant_win.reindex(ev.match_id).values.astype(bool)).astype(int)
    first_side = ev.sort_values("order_no").groupby("match_id").team_side.first()
    ev["acts_first"] = (ev.team_side == first_side.reindex(ev.match_id).values).astype(int)
    ev = ev.sort_values(["match_id", "order_no"]).reset_index(drop=True)

    ro["won"] = ((ro.side == 0) == mi.radiant_win.reindex(ro.match_id).values.astype(bool)).astype(int)
    ro["start_time"] = mi.start_time.reindex(ro.match_id).values
    ro["patch"] = mi.patch.reindex(ro.match_id).values
    ro["pos"] = assign_positions(ro) if len(ro) else pd.Series(dtype=int)
    ro["w"] = experience_weight(ro.start_time, ro.patch, as_of, patch, patch_rank, cfg)
    ev["start_time"] = mi.start_time.reindex(ev.match_id).values
    ev["w"] = experience_weight(ev.start_time, mi.patch.reindex(ev.match_id).values, as_of, patch, patch_rank, cfg)
    return Frames(m, ev, ro, heroes, players, teams, patch_rank, as_of, cfg, roles)


def experience_weight(start_time, patches, as_of: int, current_patch: str | None, patch_rank: dict, cfg: dict):
    d = cfg["decay"]
    age_days = (as_of - np.asarray(start_time, dtype=float)) / DAY
    w = 0.5 ** (np.clip(age_days, 0, None) / d["half_life_days"])
    if current_patch and current_patch in patch_rank:
        cur = patch_rank[current_patch]
        gap = np.array([max(0, cur - patch_rank.get(p, cur)) for p in patches])
        w = w * (d["patch_penalty"] ** gap)
    return w


# ---------------------------------------------------------------- P2-01 player x hero
def hero_prior(fr: Frames) -> pd.Series:
    """Hero-level smoothed pro win rate (the Beta prior for player x hero)."""
    gw = fr.roster.groupby("hero_id").w.sum()
    ww = fr.roster.assign(ww=fr.roster.w * fr.roster.won).groupby("hero_id").ww.sum()
    return ((ww + 1) / (gw + 2)).rename("prior")


def player_hero_stats(fr: Frames) -> pd.DataFrame:
    """Per (account_id, hero_id): games, wins, decayed games_w/wins_w, last_played, pos_mode, smoothed WR, lift."""
    k = fr.cfg["decay"]["prior_strength"]
    r = fr.roster[fr.roster.account_id.notna()].copy()
    r["account_id"] = r.account_id.astype(int)
    r["ww"] = r.w * r.won
    prior = hero_prior(fr)
    g = r.groupby(["account_id", "hero_id"]).agg(games=("won", "size"), wins=("won", "sum"), games_w=("w", "sum"),
                                                 wins_w=("ww", "sum"), last_played=("start_time", "max"),
                                                 pos=("pos", lambda s: int(s.mode().iloc[0]))).reset_index()
    g["prior"] = prior.reindex(g.hero_id).fillna(0.5).values
    g["wr"] = (g.wins_w + k * g.prior) / (g.games_w + k)
    p = r.groupby("account_id").agg(p_games=("won", "size"), p_games_w=("w", "sum"), p_wins_w=("ww", "sum"))
    p["p_wr"] = (p.p_wins_w + k * 0.5) / (p.p_games_w + k)
    g = g.join(p, on="account_id")
    g["pick_frequency"] = g.games_w / g.p_games_w
    g["relative_win_lift"] = g.wr - g.p_wr
    # how often the field picks this hero at the same position (meta popularity), and the player's excess over it
    pos_w = r.groupby("pos").w.sum()
    hero_pos_w = r.groupby(["hero_id", "pos"]).w.sum()
    g["meta_pick_freq"] = [float(hero_pos_w.get((h, p), 0.0) / max(pos_w.get(p, 1e-9), 1e-9)) for h, p in zip(g.hero_id, g.pos)]
    g["pick_lift"] = g.pick_frequency - g.meta_pick_freq
    g["recent_usage"] = ((fr.as_of - g.last_played) <= fr.cfg["decay"]["recent_days"] * DAY).astype(float)
    g["match_ids"] = [tuple(x) for x in r.groupby(["account_id", "hero_id"]).match_id.apply(
        lambda s: sorted(s, reverse=True)[:5]).reindex(pd.MultiIndex.from_frame(g[["account_id", "hero_id"]])).values]
    return g


def signature_scores(stats: pd.DataFrame, fr: Frames, ban_pressure: pd.DataFrame | None = None) -> pd.DataFrame:
    """PLAN's six-component signature score; weights from config."""
    w = fr.cfg["signature"]
    s = stats.copy()
    if ban_pressure is not None and len(ban_pressure):
        bpi = ban_pressure.set_index("hero_id")
        s["ban_rate_vs_team"] = bpi.phase0_rate.reindex(s.hero_id).fillna(0).values
        s["meta_ban_rate"] = bpi.global_phase0_rate.reindex(s.hero_id).fillna(0).values
    else:
        s["ban_rate_vs_team"] = 0.0
        s["meta_ban_rate"] = 0.0
    # only the excess over the patch-wide ban rate counts as *targeted*; a meta hero banned against everyone scores 0 here
    s["targeted_ban_pressure"] = (s.ban_rate_vs_team - s.meta_ban_rate).clip(lower=0)
    s["role_adjusted_performance"] = 1.0
    s["tournament_readiness"] = 1.0
    s["signature"] = (w["pick_frequency"] * s.pick_frequency + w["relative_win_lift"] * s.relative_win_lift
                      + w["targeted_ban_pressure"] * s.targeted_ban_pressure + w["recent_usage"] * s.recent_usage
                      + w["role_adjusted_performance"] * 0 + w["tournament_readiness"] * 0)
    # sample-size shrink: tiny samples cannot produce big scores
    s["signature"] *= s.games_w / (s.games_w + 2)
    s["tag"] = [classify(r) for r in s.itertuples()]
    return s.sort_values("signature", ascending=False)


def classify(r) -> str:
    """PLAN classes: signature (played far above the field's rate and at/above own baseline), meta (hot on the patch),
    targeted (banned against this team well above the meta rate). Multiple tags joined by '+'."""
    tags = []
    if r.games >= 3 and r.pick_frequency >= 2 * max(r.meta_pick_freq, 0.01) and r.relative_win_lift >= -0.02:
        tags.append("signature")
    if r.meta_ban_rate >= 0.15 or r.meta_pick_freq >= 0.10:
        tags.append("meta")
    if r.targeted_ban_pressure >= 0.10:
        tags.append("targeted")
    return "+".join(tags) or "-"


# ---------------------------------------------------------------- team helpers
def team_matches(fr: Frames, team_id: int) -> pd.DataFrame:
    m = fr.matches
    t = m[(m.radiant_team_id == team_id) | (m.dire_team_id == team_id)].copy()
    t["side"] = np.where(t.radiant_team_id == team_id, 0, 1)
    t["won"] = (t.side == 0) == t.radiant_win.astype(bool)
    t["opp"] = np.where(t.side == 0, t.dire_team_id, t.radiant_team_id)
    return t.sort_values("start_time", ascending=False)


def team_draft(fr: Frames, team_id: int) -> pd.DataFrame:
    tm = team_matches(fr, team_id)
    e = fr.events[fr.events.match_id.isin(tm.match_id)].copy()
    side = tm.set_index("match_id").side
    e["by_team"] = (e.team_side == side.reindex(e.match_id).values).astype(int)
    e["team_won"] = (e.won == e.by_team).astype(int)   # won from the team's perspective
    first = e[e.order_no == 0].set_index("match_id").team_side
    e["we_first"] = (side.reindex(e.match_id).values == first.reindex(e.match_id).values).astype(int)
    return e


def current_roster(fr: Frames, team_id: int, n_games: int | None = None) -> list[int]:
    n = n_games or fr.cfg["decay"]["roster_window_games"]
    tm = team_matches(fr, team_id).head(n)
    r = fr.roster[(fr.roster.match_id.isin(tm.match_id)) & (fr.roster.team_id == team_id) & fr.roster.account_id.notna()]
    c = r.groupby("account_id").agg(n=("match_id", "size"), pos=("pos", lambda s: int(s.mode().iloc[0])))
    return [int(a) for a in c.sort_values(["pos", "n"], ascending=[True, False]).index][:5] if len(c) else []


# ---------------------------------------------------------------- P2-03 targeted ban pressure
def ban_pressure(fr: Frames, team_id: int) -> pd.DataFrame:
    """Per hero: how often opponents ban it against this team (all phases / phase 0), decayed rates, sample ids."""
    e = team_draft(fr, team_id)
    games_w = e.drop_duplicates("match_id").w.sum()
    games = e.match_id.nunique()
    b = e[(e.by_team == 0) & (e.is_pick == 0)]
    g = b.groupby("hero_id").agg(bans=("match_id", "size"), bans_w=("w", "sum"),
                                 phase0=("phase", lambda s: int((s == 0).sum())),
                                 phase0_w=("w", lambda s: float(s[b.loc[s.index, "phase"] == 0].sum())),
                                 match_ids=("match_id", lambda s: tuple(sorted(s, reverse=True)[:5]))).reset_index()
    g["rate"] = g.bans_w / max(games_w, 1e-9)
    g["phase0_rate"] = g.phase0_w / max(games_w, 1e-9)
    g["games"] = games
    # patch-wide ban rates (all matches in the frame) -> what a hero gets banned regardless of opponent
    allb = fr.events[fr.events.is_pick == 0]
    all_games_w = fr.events.drop_duplicates("match_id").w.sum()
    gp0 = allb[allb.phase == 0].groupby("hero_id").w.sum() / max(all_games_w, 1e-9)
    gall = allb.groupby("hero_id").w.sum() / max(all_games_w, 1e-9)
    g["global_phase0_rate"] = gp0.reindex(g.hero_id).fillna(0).values
    g["global_rate"] = gall.reindex(g.hero_id).fillna(0).values
    g["targeted_lift"] = g.phase0_rate - g.global_phase0_rate
    return g.sort_values("targeted_lift", ascending=False)


def own_bans(fr: Frames, team_id: int) -> pd.DataFrame:
    e = team_draft(fr, team_id)
    b = e[(e.by_team == 1) & (e.is_pick == 0)]
    games_w = e.drop_duplicates("match_id").w.sum()
    g = b.groupby("hero_id").agg(bans=("match_id", "size"), bans_w=("w", "sum"),
                                 phase0=("phase", lambda s: int((s == 0).sum()))).reset_index()
    g["rate"] = g.bans_w / max(games_w, 1e-9)
    return g.sort_values("bans_w", ascending=False)


# ---------------------------------------------------------------- P2-04 team hero / phase habits
def team_hero_stats(fr: Frames, team_id: int) -> pd.DataFrame:
    e = team_draft(fr, team_id)
    games_w = e.drop_duplicates("match_id").w.sum()
    p = e[(e.by_team == 1) & (e.is_pick == 1)].copy()
    p["ww"] = p.w * p.team_won
    k = fr.cfg["decay"]["prior_strength"]
    prior = hero_prior(fr)
    g = p.groupby("hero_id").agg(picks=("match_id", "size"), picks_w=("w", "sum"), wins=("team_won", "sum"), wins_w=("ww", "sum"),
                                 first_phase=("phase", lambda s: int((s <= 1).sum())),
                                 match_ids=("match_id", lambda s: tuple(sorted(s, reverse=True)[:5]))).reset_index()
    g["pick_rate"] = g.picks_w / max(games_w, 1e-9)
    g["prior"] = prior.reindex(g.hero_id).fillna(0.5).values
    g["wr"] = (g.wins_w + k * g.prior) / (g.picks_w + k)
    g["win_lift"] = g.wr - g.prior
    return g.sort_values("picks_w", ascending=False)


def phase_habits(fr: Frames, team_id: int) -> dict:
    """Per phase x (we_first) x action type: decayed hero counters for this team's own actions."""
    e = team_draft(fr, team_id)
    out: dict = defaultdict(Counter)
    for r in e[e.by_team == 1].itertuples():
        out[(int(r.phase), int(r.we_first), int(r.is_pick))][int(r.hero_id)] += float(r.w)
    games = e.drop_duplicates("match_id").groupby("we_first").w.sum().to_dict()
    return {"counters": dict(out), "games_w_by_first": games, "games": int(e.match_id.nunique())}


def pair_synergy(fr: Frames, team_id: int, min_count: int = 3) -> pd.DataFrame:
    """Hero pairs this team picks together more than independence predicts (simple 'system hero' proxy)."""
    e = team_draft(fr, team_id)
    p = e[(e.by_team == 1) & (e.is_pick == 1)]
    games_w = e.drop_duplicates("match_id").w.sum()
    single = p.groupby("hero_id").w.sum() / max(games_w, 1e-9)
    pairs: Counter = Counter(); cnt: Counter = Counter(); wins: Counter = Counter()
    for mid, g in p.groupby("match_id"):
        hs = sorted(g.hero_id.tolist()); w = float(g.w.iloc[0]); won = int(g.team_won.iloc[0])
        for i in range(len(hs)):
            for j in range(i + 1, len(hs)):
                pairs[(hs[i], hs[j])] += w; cnt[(hs[i], hs[j])] += 1; wins[(hs[i], hs[j])] += won
    rows = []
    for (a, b), w in pairs.items():
        if cnt[(a, b)] < min_count:
            continue
        exp = single.get(a, 0) * single.get(b, 0) * games_w
        rows.append({"hero_a": a, "hero_b": b, "count": cnt[(a, b)], "wins": wins[(a, b)], "together_w": w,
                     "lift": w / exp if exp > 0 else float("inf")})
    df = pd.DataFrame(rows, columns=["hero_a", "hero_b", "count", "wins", "together_w", "lift"])
    return df.sort_values(["lift", "count"], ascending=False)


# ---------------------------------------------------------------- P2-05 opponent response edges
def response_edges(fr: Frames, team_id: int, max_prefix: int = 3) -> dict:
    """prefix (tuple of (is_pick, rel_team, hero) for steps before s) -> Counter(hero this team played at step s),
    for s in 1..max_prefix where this team acts. rel_team: 0 = this team, 1 = opponent."""
    e = team_draft(fr, team_id)
    edges: dict = defaultdict(Counter)
    for mid, g in e.groupby("match_id"):
        g = g.sort_values("order_no")
        seq = [(int(r.is_pick), 0 if r.by_team else 1, int(r.hero_id), float(r.w)) for r in g.itertuples()]
        for s in range(1, min(max_prefix, len(seq) - 1) + 1):
            if seq[s][1] != 0:
                continue
            prefix = tuple(x[:3] for x in seq[:s])
            edges[prefix][seq[s][2]] += seq[s][3]
            edges[("_n",) + prefix][seq[s][2]] += 1
    return dict(edges)


def lookup_response(edges: dict, prefix: tuple) -> list[tuple[int, float, int]]:
    """-> [(hero, weight, raw_count)] sorted for a given prefix; empty if unseen."""
    c = edges.get(prefix)
    if not c:
        return []
    n = edges.get(("_n",) + prefix, Counter())
    return sorted(((h, w, n[h]) for h, w in c.items()), key=lambda x: -x[1])


# ---------------------------------------------------------------- team profile bundle
@dataclass
class TeamProfile:
    team_id: int
    name: str
    roster: list[int]
    games: int
    stats: pd.DataFrame          # player x hero with signature (roster only)
    ban_pressure: pd.DataFrame
    own_bans: pd.DataFrame
    hero_stats: pd.DataFrame
    habits: dict
    pairs: pd.DataFrame
    edges: dict

    def sig(self, hero_id: int) -> tuple[float, pd.Series | None]:
        s = self.stats[self.stats.hero_id == hero_id]
        if not len(s):
            return 0.0, None
        row = s.sort_values("signature", ascending=False).iloc[0]
        return float(row.signature), row


def build_profile(fr: Frames, team_id: int, all_stats: pd.DataFrame | None = None) -> TeamProfile:
    roster = current_roster(fr, team_id)
    bp = ban_pressure(fr, team_id)
    stats = all_stats if all_stats is not None else player_hero_stats(fr)
    st = signature_scores(stats[stats.account_id.isin(roster)], fr, bp) if roster else stats.iloc[0:0]
    return TeamProfile(team_id, fr.team(team_id), roster, int(team_matches(fr, team_id).match_id.nunique()), st, bp,
                       own_bans(fr, team_id), team_hero_stats(fr, team_id), phase_habits(fr, team_id),
                       pair_synergy(fr, team_id), response_edges(fr, team_id))
