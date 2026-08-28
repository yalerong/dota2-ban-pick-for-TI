"""End-to-end blind test (P2-10): profiles from an as-of snapshot, replay later real drafts, measure Top-k hit rate."""
from __future__ import annotations
import hashlib
import json
import logging
import sqlite3
from collections import Counter, defaultdict

import pandas as pd

from .draft_formats import load_format
from .draft_state import DraftState
from .profiles import Frames, build_profile, load_frames, player_hero_stats
from .recommend import candidates, normalize_context_actions

log = logging.getLogger(__name__)


def context_mode(context: bool = True, context_actions: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None) -> dict:
    actions = normalize_context_actions(context, context_actions)
    if actions == {"ban", "pick"}:
        return {"key": "all", "label": "ALL", "actions": ["ban", "pick"]}
    if actions == {"ban"}:
        return {"key": "ban", "label": "BAN ONLY", "actions": ["ban"]}
    if actions == {"pick"}:
        return {"key": "pick", "label": "PICK ONLY", "actions": ["pick"]}
    return {"key": "none", "label": "OFF", "actions": []}


def _global_baseline(fr: Frames) -> dict:
    """(phase, is_pick) -> Counter(hero weight): the 'meta frequency' baseline every model must beat."""
    c: dict = defaultdict(Counter)
    for r in fr.events.itertuples():
        c[(int(r.phase), int(r.is_pick))][int(r.hero_id)] += float(r.w)
    return c


def blind_test(snap: sqlite3.Connection, live: sqlite3.Connection, as_of: int, until: int | None = None,
               patch: str | None = None, league: int | None = None, max_matches: int = 150, ks=(1, 3, 5),
               context: bool = True, test_match_ids: list[int] | None = None,
               context_actions: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None) -> dict:
    if patch is None:
        r = snap.execute(
            """SELECT patch FROM matches
               WHERE excluded=0 AND start_time < ?
               GROUP BY patch ORDER BY COUNT(*) DESC, patch DESC LIMIT 1""",
            (as_of,),
        ).fetchone()
        patch = r[0] if r else None
    assert patch, "no patch available in snapshot before as_of"
    fr = load_frames(snap, as_of=as_of, patch=patch)
    assert int(fr.matches.start_time.max()) < as_of, "snapshot leaks matches at/after as_of"
    fmt_patch = patch
    fmt = load_format(snap, fmt_patch)
    assert fmt, f"no draft format for {fmt_patch} in snapshot"
    stats = player_hero_stats(fr)
    baseline = _global_baseline(fr)
    mode = context_mode(context, context_actions)

    q = "SELECT match_id, patch, start_time, radiant_team_id, dire_team_id, league_name FROM matches WHERE excluded=0 AND start_time >= ?"
    args: list = [as_of]
    if until:
        q += " AND start_time < ?"; args.append(until)
    q += " AND patch = ?"; args.append(fmt_patch)
    if league:
        q += " AND leagueid = ?"; args.append(league)
    fixed_ids = [int(mid) for mid in test_match_ids] if test_match_ids is not None else None
    if fixed_ids is not None:
        if not fixed_ids:
            raise ValueError("fixed test match ids are empty")
        if len(set(fixed_ids)) != len(fixed_ids):
            raise ValueError("fixed test match ids contain duplicates")
        fixed_ids = fixed_ids[:max_matches]
        q += f" AND match_id IN ({','.join('?' * len(fixed_ids))})"
        args.extend(fixed_ids)
    tests = pd.read_sql_query(q + " ORDER BY start_time", live, params=args)
    known = set(fr.teams)
    if fixed_ids is None:
        tests = tests[tests.radiant_team_id.isin(known) & tests.dire_team_id.isin(known)].head(max_matches)
    else:
        found = set(int(mid) for mid in tests.match_id)
        missing = [mid for mid in fixed_ids if mid not in found]
        if missing:
            raise ValueError(f"fixed test matches unavailable or outside filters: {missing}")
        tests = tests.set_index("match_id").loc[fixed_ids].reset_index()
        unknown = tests[~tests.radiant_team_id.isin(known) | ~tests.dire_team_id.isin(known)]
        if len(unknown):
            raise ValueError(f"fixed test matches contain teams absent from snapshot: {unknown.match_id.tolist()}")
    log.info("blind test: %d matches after %s", len(tests), as_of)

    profiles: dict = {}

    def prof(tid):
        if tid not in profiles:
            profiles[tid] = build_profile(fr, int(tid), stats)
        return profiles[tid]

    kmax = max(ks)
    hits = {"model": defaultdict(Counter), "baseline": defaultdict(Counter)}
    steps = Counter()
    for t in tests.itertuples():
        ev = pd.read_sql_query("SELECT order_no, team_side, is_pick, hero_id, phase FROM draft_events WHERE match_id=? ORDER BY order_no",
                               live, params=(int(t.match_id),))
        if len(ev) != len(fmt):
            continue
        first_side = int(ev.team_side.iloc[0])
        first_team = t.radiant_team_id if first_side == 0 else t.dire_team_id
        second_team = t.dire_team_id if first_side == 0 else t.radiant_team_id
        P1, P2 = prof(first_team), prof(second_team)
        st = DraftState(fmt, frozenset(fr.heroes))
        for r in ev.itertuples():
            if st.done:
                break
            key = (int(r.phase), int(r.is_pick))
            actual = int(r.hero_id)
            cands = candidates(fr, st, P1, P2, k=kmax, context=context, context_actions=mode["actions"])
            ranked = [c.hero_id for c in cands]
            legal = st.legal()
            base = [h for h, _ in baseline[key].most_common() if h in legal][:kmax]
            for k in ks:
                hits["model"][key][k] += int(actual in ranked[:k])
                hits["baseline"][key][k] += int(actual in base[:k])
            steps[key] += 1
            try:
                st.apply(actual, team=0 if int(r.team_side) == first_side else 1, is_pick=bool(r.is_pick))
            except ValueError as e:
                log.warning("match %s step %s: %s", t.match_id, r.order_no, e)
                break

    def agg(which, keys):
        n = sum(steps[k] for k in keys)
        return {f"top{k}": (sum(hits[which][kk][k] for kk in keys) / n if n else None) for k in ks} | {"n": n}

    all_keys = list(steps)
    selected_ids = [int(mid) for mid in tests.match_id]
    test_set_hash = hashlib.sha256(json.dumps(selected_ids, separators=(",", ":")).encode()).hexdigest()[:16]
    res = {"as_of": as_of, "patch": fmt_patch, "context": bool(mode["actions"]), "context_mode": mode,
           "test_matches": int(len(tests)),
           "test_match_ids": selected_ids, "test_set_hash": test_set_hash, "steps": sum(steps.values()),
           "overall": {w: agg(w, all_keys) for w in ("model", "baseline")},
           "bans": {w: agg(w, [k for k in all_keys if k[1] == 0]) for w in ("model", "baseline")},
           "picks": {w: agg(w, [k for k in all_keys if k[1] == 1]) for w in ("model", "baseline")},
           "by_phase": {f"phase{p}_{'pick' if ip else 'ban'}": {w: agg(w, [(p, ip)]) for w in ("model", "baseline")}
                        for (p, ip) in sorted(all_keys)}}
    return res


def to_markdown(res: dict, snapshot_version: str | None) -> str:
    def row(name, d):
        m, b = d["model"], d["baseline"]
        return (f"| {name} | {m['n']} | " + " | ".join(f"{100*m[f'top{k}']:.1f}% / {100*b[f'top{k}']:.1f}%"
                                                    for k in (1, 3, 5) if m.get(f'top{k}') is not None) + " |")
    L = [f"# Blind-test baseline", "",
         f"snapshot as_of {res['as_of']} (data_version {snapshot_version or '?'}), patch {res['patch']}, "
         f"{res['test_matches']} later real matches (test set {res['test_set_hash']}), {res['steps']} draft steps, "
         f"context terms {res.get('context_mode', {}).get('label', 'ALL' if res.get('context', True) else 'OFF')}.", "",
         f"Machine context_mode: `{res.get('context_mode', {}).get('key', 'all' if res.get('context', True) else 'none')}`.", "",
         "Model = linear evidence score (config/scoring.yaml); baseline = global meta frequency among legal heroes. "
         "Numbers are hit rates of the actual pro action within the model's Top-k (model / baseline).", "",
         "| slice | steps | top1 | top3 | top5 |", "|---|---|---|---|---|",
         row("overall", res["overall"]), row("bans", res["bans"]), row("picks", res["picks"])]
    for name, d in res["by_phase"].items():
        L.append(row(name, d))
    L += ["", "Any future Policy model must beat the **model** column on the same snapshot before promotion."]
    return "\n".join(L)
