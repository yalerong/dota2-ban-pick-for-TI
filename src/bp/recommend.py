"""Candidate Pick/Ban output with evidence (P2-07). Linear scoring, weights in config/scoring.yaml."""
from __future__ import annotations
from dataclasses import dataclass, field

import pandas as pd

from .draft_state import DraftState
from .profiles import Frames, TeamProfile, lookup_next_pick, lookup_response


@dataclass
class Candidate:
    hero_id: int
    hero: str
    action: str                      # "ban" | "pick"
    score: float
    confidence: float
    samples: int
    evidence: list[str] = field(default_factory=list)
    match_ids: list[int] = field(default_factory=list)
    components: dict = field(default_factory=dict)
    predicted_response: list[str] = field(default_factory=list)
    insufficient: bool = False


def normalize_context_actions(context: bool = True, context_actions: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None) -> frozenset[str]:
    if not context:
        return frozenset()
    if context_actions is None:
        return frozenset({"ban", "pick"})
    actions = frozenset(str(a).lower() for a in context_actions)
    unknown = actions - {"ban", "pick"}
    if unknown:
        raise ValueError(f"unknown context action(s): {', '.join(sorted(unknown))}")
    return actions


def _fmt_pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _player_line(fr: Frames, row: pd.Series) -> str:
    return (f"{fr.player(row.account_id)} (pos {int(row.pos)}) played {fr.hero(row.hero_id)} {int(row.games)}x, "
            f"{int(row.wins)} wins (smoothed {_fmt_pct(row.wr)}, lift {row.relative_win_lift:+.2f})")


def candidates(fr: Frames, state: DraftState, us: TeamProfile, them: TeamProfile, k: int | None = None,
               context: bool = True, context_actions: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None) -> list[Candidate]:
    """Score every legal hero for the next action from `us` perspective (state.team 0 must be `us`'s side).

    The caller re-bases the state so that team 0 == the team we score for.
    """
    cfg = fr.cfg
    k = k or cfg["evidence"]["top_k"]
    if state.done:
        return []
    is_pick = state.next_is_pick
    acting = state.next_team           # 0 = us, 1 = them
    action = "pick" if is_pick else "ban"
    active_context_actions = normalize_context_actions(context, context_actions)
    use_context = action in active_context_actions
    # if it's their turn we still can score (used by blind test): swap roles
    me, opp = (us, them) if acting == 0 else (them, us)
    w = cfg["pick"] if is_pick else cfg["ban"]
    their_bp = opp.by_hero("ban_pressure")      # bans opp receives
    my_bp = me.by_hero("ban_pressure")          # bans I receive
    my_hs = me.by_hero("hero_stats")
    opp_hs = opp.by_hero("hero_stats")
    my_bans = me.by_hero("own_bans")
    cw = cfg.get("context", {}) if use_context else {}
    ctx = fr.context() if use_context else None
    my_picks, their_picks = state.picks(acting), state.picks(1 - acting)
    weights = {**w, "counter": cw.get("counter", 0), "synergy": cw.get("synergy", 0), "gap": cw.get("gap", 0)}
    # pick: which roster players still need a hero (weight pick.position_fit; 0 = off)
    free = me.open_positions(my_picks) if is_pick and w.get("position_fit", 0) else None
    # ban: what the opponent tends to pick next from this draft position (weight ban.their_next_pick; 0 = off)
    next_pick = None
    if not is_pick and w.get("their_next_pick", 0):
        resp = lookup_next_pick(opp.edges, _rebase_prefix(state.prefix_key(), for_team=1 - acting))
        tot = sum(wgt for _, wgt, _ in resp) or 1.0
        next_pick = {hh: (wgt / tot, nn) for hh, wgt, nn in resp}
    out: list[Candidate] = []
    for h in sorted(state.legal()):
        comps: dict[str, float] = {}
        ev: list[str] = []
        mids: list[int] = []
        n = 0
        if is_pick:
            s, row = me.sig(h)
            comps["our_signature"] = s
            if row is not None:
                ev.append(_player_line(fr, row)); mids += list(row.match_ids); n = max(n, int(row.games))
            bp = my_bp.get(h)
            comps["their_ban_pressure"] = float(bp.phase0_rate) if bp is not None else 0.0
            if bp is not None and bp.bans:
                ev.append(f"{fr.hero(h)} banned against us {int(bp.bans)}x in {int(bp.games)} games ({int(bp.phase0)} in phase 1)")
                mids += list(bp.match_ids)
                n = max(n, int(bp.bans))
            hs = my_hs.get(h)
            comps["our_pick_rate"] = float(hs.pick_rate) if hs is not None else 0.0
            comps["our_win_lift"] = float(hs.win_lift) if hs is not None else 0.0
            if hs is not None:
                ev.append(f"we picked {fr.hero(h)} {int(hs.picks)}x, {int(hs.wins)} wins (smoothed {_fmt_pct(hs.wr)})")
                mids += list(hs.match_ids)
                n = max(n, int(hs.picks))
            if free is not None:
                takers = me.can_take(h, free)
                comps["position_fit"] = 1.0 if takers else 0.0
                if takers:
                    ev.append("in the pool of " + ", ".join(fr.player(a) for a in takers) + " (still without a hero)")
        else:
            s, row = opp.sig(h)
            comps["their_signature"] = s
            if row is not None:
                ev.append("their " + _player_line(fr, row)); mids += list(row.match_ids); n = max(n, int(row.games))
            hs = opp_hs.get(h)
            comps["their_pick_rate"] = float(hs.pick_rate) if hs is not None else 0.0
            comps["their_win_lift"] = float(hs.win_lift) if hs is not None else 0.0
            if hs is not None:
                ev.append(f"they picked {fr.hero(h)} {int(hs.picks)}x, {int(hs.wins)} wins (smoothed {_fmt_pct(hs.wr)}, lift {hs.win_lift:+.2f})")
                mids += list(hs.match_ids); n = max(n, int(hs.picks))
            bp = their_bp.get(h)
            if bp is not None and bp.bans:
                ev.append(f"{fr.hero(h)} banned against them {int(bp.bans)}x in {int(bp.games)} games")
                mids += list(bp.match_ids)
                n = max(n, int(bp.bans))
            ob = my_bans.get(h)
            comps["our_ban_habit"] = float(ob.rate) if ob is not None else 0.0
            if ob is not None:
                ev.append(f"we banned {fr.hero(h)} {int(ob.bans)}x before")
            if next_pick is not None:
                pr, nn = next_pick.get(h, (0.0, 0))
                comps["their_next_pick"] = pr
                if nn:
                    ev.append(f"they picked {fr.hero(h)} next {nn}x from a similar draft position")
        if ctx is not None and (my_picks or their_picks):
            # pick: h should counter their picks, fit our picks, fill our gaps.
            # ban: deny what would counter our picks / fit their picks / fill their gaps.
            vs, with_, gaps_of = (their_picks, my_picks, my_picks) if is_pick else (my_picks, their_picks, their_picks)
            c_eff, c_det = ctx.counter_vs(h, vs)
            s_eff, s_det = ctx.synergy_with(h, with_)
            g_eff, fills = ctx.gap_fill(h, gaps_of)
            comps["counter"], comps["synergy"], comps["gap"] = c_eff, s_eff, g_eff
            who_vs, who_with = ("their", "our") if is_pick else ("our", "their")
            for o, e, nn in c_det:
                if nn >= 3 and abs(e) >= 0.03:
                    ev.append(f"{'counters' if e > 0 else 'loses to'} {who_vs} {fr.hero(o)} ({e:+.2f} WR, {nn} games)")
            for m, e, nn in s_det:
                if nn >= 3 and abs(e) >= 0.03:
                    ev.append(f"{'synergy' if e > 0 else 'anti-synergy'} with {who_with} {fr.hero(m)} ({e:+.2f} WR, {nn} games)")
            if fills:
                ev.append(f"fills {who_with} missing role(s): {', '.join(fills)}")
        score = sum(weights.get(c, 0) * v for c, v in comps.items())
        conf = n / (n + cfg["evidence"]["confidence_k"])
        out.append(Candidate(h, fr.hero(h), action, score, conf, n, ev,
                             sorted(set(int(m) for m in mids), reverse=True)[:6], comps,
                             insufficient=n < cfg["evidence"]["min_samples"]))
    out.sort(key=lambda c: (-c.score, c.hero_id))
    top = out[:k]
    # predicted opponent response for the top candidates (opponent = the team NOT acting)
    for c in top:
        prefix = state.prefix_key() + ((int(is_pick), acting, c.hero_id),)
        resp = lookup_response(opp.edges, _rebase_prefix(prefix, for_team=1 - acting))
        c.predicted_response = [f"{fr.hero(h)} ({n}x)" for h, wgt, n in resp[:3]]
    return top


def _rebase_prefix(prefix: tuple, for_team: int) -> tuple:
    """Edges are keyed with rel_team 0 == the profiled team. Re-base a (is_pick, team, hero) prefix."""
    return tuple((p, 0 if t == for_team else 1, h) for p, t, h in prefix)


def format_candidates(cands: list[Candidate], step: int, total: int) -> str:
    if not cands:
        return "(draft complete)"
    lines = [f"Step {step + 1}/{total}: {cands[0].action.upper()}"]
    for i, c in enumerate(cands, 1):
        flag = "  [insufficient evidence]" if c.insufficient else ""
        lines.append(f"{i}. {c.hero}  score {c.score:.2f}  conf {c.confidence:.2f}  n={c.samples}{flag}")
        for e in c.evidence:
            lines.append(f"     - {e}")
        if c.predicted_response:
            lines.append(f"     -> likely response: {', '.join(c.predicted_response)}")
        if c.match_ids:
            lines.append(f"     matches: {', '.join(str(m) for m in c.match_ids)}")
    return "\n".join(lines)
