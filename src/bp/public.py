"""High-MMR ladder matches (scripts/pull_public.py -> data/db/public.sqlite) as dense hero-pair counts.

The pro dataset (~7k matches per patch) cannot support 127x127 counter / synergy tables; ladder games at Divine+ are the
same heroes played hundreds of thousands of times. This module turns them into plain counts that `context.build_context`
(BP recommendations) and `lineup.build_tables` (lineup win probability) add to the pro counts with a configurable weight
per game (`context.public_weight` / `lineup.public_weight` in scoring.yaml, 0 = off).

Counting is vectorised with numpy: 1.5M matches (52M pair observations) take seconds, and the result is cached under
data/cache keyed by the DB file stamp + filters, so a second `load_frames(..., public_db=...)` is instant.
"""
from __future__ import annotations
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import cache

log = logging.getLogger(__name__)
K = 4096                      # pair key = min * K + max; hero ids are < 4096
RANKED_ALL_PICK = (7, 22)     # lobby_type, game_mode


@dataclass
class PublicCounts:
    n_matches: int = 0
    radiant_wins: int = 0
    single_n: dict = field(default_factory=dict)     # hero -> games
    single_w: dict = field(default_factory=dict)     # hero -> wins
    syn_n: dict = field(default_factory=dict)        # (a < b) -> games on the same side
    syn_w: dict = field(default_factory=dict)        # (a < b) -> wins of that side
    ctr_n: dict = field(default_factory=dict)        # (a < b) -> games on opposite sides
    ctr_w: dict = field(default_factory=dict)        # (a < b) -> wins of a's side (a beats b)


def _agg(keys: np.ndarray, wins: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    u, inv = np.unique(keys, return_inverse=True)
    return u, np.bincount(inv), np.bincount(inv, weights=wins)


def _pair_dict(u: np.ndarray, n: np.ndarray, w: np.ndarray) -> tuple[dict, dict]:
    a, b = (u // K).astype(int), (u % K).astype(int)
    return ({(int(x), int(y)): int(c) for x, y, c in zip(a, b, n)},
            {(int(x), int(y)): float(c) for x, y, c in zip(a, b, w)})


def count_pairs(radiant: np.ndarray, dire: np.ndarray, win: np.ndarray) -> PublicCounts:
    """radiant / dire: (N, 5) int hero ids; win: (N,) 1 if radiant won."""
    radiant, dire, win = np.asarray(radiant, dtype=np.int64), np.asarray(dire, dtype=np.int64), np.asarray(win, dtype=np.int64)
    if not len(win):
        return PublicCounts()
    # singles
    u, n, w = _agg(np.concatenate([radiant.ravel(), dire.ravel()]),
                   np.concatenate([np.repeat(win, 5), np.repeat(1 - win, 5)]))
    out = PublicCounts(int(len(win)), int(win.sum()),
                       {int(h): int(c) for h, c in zip(u, n)}, {int(h): float(c) for h, c in zip(u, w)})
    # synergy: 10 unordered pairs per side
    keys, wins = [], []
    for team, tw in ((radiant, win), (dire, 1 - win)):
        for i in range(5):
            for j in range(i + 1, 5):
                a, b = team[:, i], team[:, j]
                keys.append(np.minimum(a, b) * K + np.maximum(a, b)); wins.append(tw)
    out.syn_n, out.syn_w = _pair_dict(*_agg(np.concatenate(keys), np.concatenate(wins)))
    # counter: 25 cross pairs, stored with a < b and wins counted for a's side
    keys, wins = [], []
    for i in range(5):
        for j in range(5):
            a, b = radiant[:, i], dire[:, j]
            keys.append(np.minimum(a, b) * K + np.maximum(a, b)); wins.append(np.where(a < b, win, 1 - win))
    out.ctr_n, out.ctr_w = _pair_dict(*_agg(np.concatenate(keys), np.concatenate(wins)))
    return out


def _parse_teams(col: pd.Series) -> np.ndarray:
    """'[1,2,3,4,5]' strings -> (N, 5) ints, vectorised."""
    return col.str.strip("[] ").str.split(",", expand=True).astype(np.int64).to_numpy()


def load_public(path: Path, patch: str | None = None, as_of: int | None = None, ranked_only: bool = True) -> PublicCounts:
    """Pair counts for the ladder matches in `path` (pull_public.py schema), filtered to patch / start_time < as_of."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"public DB {path} not found (run scripts/pull_public.py)")
    key = cache.key("public", cache.file_stamp(path), patch, as_of, ranked_only)
    hit = cache.get("public", key)
    if hit is not None:
        return hit
    where, args = ["1=1"], []
    if patch:
        where.append("patch = ?"); args.append(patch)
    if as_of is not None:
        where.append("start_time < ?"); args.append(int(as_of))
    if ranked_only:
        where.append("lobby_type = ? AND game_mode = ?"); args += list(RANKED_ALL_PICK)
    con = sqlite3.connect(path)
    try:
        df = pd.read_sql_query(f"SELECT radiant, dire, radiant_win FROM public_matches WHERE {' AND '.join(where)}", con, params=args)
    finally:
        con.close()
    if not len(df):
        log.warning("public DB %s: no matches for patch=%s as_of=%s", path, patch, as_of)
        counts = PublicCounts()
    else:
        counts = count_pairs(_parse_teams(df.radiant), _parse_teams(df.dire), df.radiant_win.to_numpy())
        log.info("public: %d ladder matches from %s (patch %s)", counts.n_matches, path.name, patch)
    cache.put("public", key, counts)
    return counts
