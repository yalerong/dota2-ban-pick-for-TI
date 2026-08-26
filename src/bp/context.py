"""Draft-context terms: counter matrix, synergy matrix, lineup structure gaps.

All effects are Beta-smoothed deviations from the heroes' own win-rate baselines, so an unseen pair is exactly 0.
"""
from __future__ import annotations
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

# role tags that a lineup "needs"; target = minimum count in a full 5-man lineup
DEFAULT_TARGETS = {"Initiator": 1, "Disabler": 2, "Durable": 1, "Carry": 1, "Pusher": 1, "Nuker": 1}


@dataclass
class Context:
    counter: dict            # (a, b) -> (effect, n)   a on one side, b on the other; effect > 0 == a beats b
    synergy: dict            # (a, c) -> (effect, n)   same side, symmetric
    hero_wr: dict            # hero -> smoothed global WR
    roles: dict              # hero -> set(tags)
    targets: dict = field(default_factory=lambda: dict(DEFAULT_TARGETS))

    def counter_vs(self, h: int, opp: list[int]) -> tuple[float, list[tuple[int, float, int]]]:
        """Mean counter effect of h against the given enemy heroes + per-hero detail."""
        det = [(o, *self.counter.get((h, o), (0.0, 0))) for o in opp]
        return (float(np.mean([e for _, e, _ in det])) if det else 0.0), det

    def synergy_with(self, h: int, mine: list[int]) -> tuple[float, list[tuple[int, float, int]]]:
        det = [(m, *self.synergy.get((min(h, m), max(h, m)), (0.0, 0))) for m in mine]
        return (float(np.mean([e for _, e, _ in det])) if det else 0.0), det

    def gap_fill(self, h: int, mine: list[int]) -> tuple[float, list[str]]:
        """Share of currently-missing role targets that h would fill (0..1) + the tags it fills."""
        have: dict[str, int] = defaultdict(int)
        for m in mine:
            for t in self.roles.get(m, ()):
                have[t] += 1
        missing = [t for t, need in self.targets.items() if have[t] < need]
        if not missing:
            return 0.0, []
        fills = [t for t in missing if t in self.roles.get(h, ())]
        return len(fills) / len(missing), fills


def build_context(fr, con: sqlite3.Connection | None = None, prior_strength: int | None = None) -> Context:
    """fr: profiles.Frames (uses fr.roster with side/hero_id/won/w). Roles come from the heroes table."""
    k = prior_strength or fr.cfg.get("context", {}).get("prior_strength", 10)
    r = fr.roster
    # global smoothed WR per hero
    gw = r.groupby("hero_id").w.sum()
    ww = r.assign(ww=r.w * r.won).groupby("hero_id").ww.sum()
    hero_wr = ((ww + 1) / (gw + 2)).to_dict()

    cnt_n: dict = defaultdict(float); cnt_w: dict = defaultdict(float); cnt_raw: dict = defaultdict(int)
    syn_n: dict = defaultdict(float); syn_w: dict = defaultdict(float); syn_raw: dict = defaultdict(int)
    cols = r[["match_id", "side", "hero_id", "won", "w"]].to_numpy()
    by_match: dict = defaultdict(lambda: ([], []))
    for mid, side, hid, won, w in cols:
        by_match[mid][int(side)].append((int(hid), int(won), float(w)))
    for mid, (rad, dire) in by_match.items():
        for a, awon, w in rad:
            for b, _, _ in dire:
                cnt_n[(a, b)] += w; cnt_w[(a, b)] += w * awon; cnt_raw[(a, b)] += 1
                cnt_n[(b, a)] += w; cnt_w[(b, a)] += w * (1 - awon); cnt_raw[(b, a)] += 1
        for team in (rad, dire):
            for i in range(len(team)):
                for j in range(i + 1, len(team)):
                    a, b = sorted((team[i][0], team[j][0]))
                    won = team[i][1]; w = team[i][2]
                    syn_n[(a, b)] += w; syn_w[(a, b)] += w * won; syn_raw[(a, b)] += 1
    counter = {}
    for key, n in cnt_n.items():
        a, b = key
        base = hero_wr.get(a, 0.5)
        counter[key] = ((cnt_w[key] + k * base) / (n + k) - base, cnt_raw[key])
    synergy = {}
    for key, n in syn_n.items():
        a, b = key
        base = (hero_wr.get(a, 0.5) + hero_wr.get(b, 0.5)) / 2
        synergy[key] = ((syn_w[key] + k * base) / (n + k) - base, syn_raw[key])
    roles = dict(getattr(fr, "roles", {}) or {})
    if con is not None and not roles:
        for hid, rj in con.execute("SELECT hero_id, roles FROM heroes"):
            roles[hid] = set(json.loads(rj or "[]"))
    targets = dict(fr.cfg.get("context", {}).get("targets", DEFAULT_TARGETS))
    return Context(counter, synergy, hero_wr, roles, targets)
