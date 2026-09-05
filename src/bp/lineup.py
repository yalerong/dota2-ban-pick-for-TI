"""Lineup win probability: P(radiant wins | the ten heroes), calibrated on real match outcomes.

This is the "draft is over, who is favoured" number (what the in-game prediction shows at 0:00), built only from the
ten heroes - no players, no teams. Three tables, all in log-odds units and all computed on training matches only:

  s(a)      hero strength        shrunk logit of the hero's win rate (side-agnostic)
  syn(a,c)  same-side pair       shrunk residual over what the two singles already predict
  ctr(a,b)  opposite-side pair   shrunk residual of a beating b beyond the two singles; ctr(b,a) == -ctr(a,b)

Each cell is a Beta-shrunk rate with prior strength k toward the singles' expectation, so an unseen pair is exactly 0
and a 5-game pair barely moves. Per match the tables are summed into four aggregates:

  hero    = sum_R s - sum_D s        synergy = sum_pairs(R) syn - sum_pairs(D) syn
  counter = sum_{a in R, b in D} ctr(a,b)      gap = missing role targets in D - in R

and a logistic regression on the aggregates (intercept = radiant side advantage) turns them into a probability.
The regression is fitted on out-of-fold aggregates (tables rebuilt without the fold), otherwise thin cells that
"remember" their own outcome make the fit over-confident.
"""
from __future__ import annotations
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

log = logging.getLogger(__name__)

FEATURES = ("hero", "synergy", "counter", "gap")
DEFAULT_CFG = {"prior_strength": 50, "single_prior_strength": 20, "k_grid": [10, 30, 50, 100], "folds": 5, "l2": 1e-3}


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


# ---------------------------------------------------------------- data
@dataclass
class LineupMatch:
    match_id: int
    radiant: tuple[int, ...]
    dire: tuple[int, ...]
    radiant_win: int
    start_time: int = 0


def lineup_matches(fr) -> list[LineupMatch]:
    """Matches with exactly five heroes per side, from Frames.roster. Anything else is dropped (and counted in the log)."""
    ro = fr.roster
    mi = fr.matches.set_index("match_id")
    out, dropped = [], 0
    for mid, g in ro.groupby("match_id", sort=True):
        r = tuple(sorted(int(h) for h in g[g.side == 0].hero_id))
        d = tuple(sorted(int(h) for h in g[g.side == 1].hero_id))
        if len(r) != 5 or len(d) != 5 or len(set(r) | set(d)) != 10:
            dropped += 1
            continue
        out.append(LineupMatch(int(mid), r, d, int(mi.radiant_win.loc[mid]), int(mi.start_time.loc[mid])))
    if dropped:
        log.info("lineup: dropped %d matches without a clean 5v5 roster", dropped)
    return out


# ---------------------------------------------------------------- tables
@dataclass
class Tables:
    k: float
    single: dict = field(default_factory=dict)        # hero -> (s, n)
    synergy: dict = field(default_factory=dict)       # (a<c) -> (effect, n)
    counter: dict = field(default_factory=dict)       # (a, b) -> (effect, n)   a beats b
    base_rate: float = 0.5                            # radiant win rate in training

    def s(self, h: int) -> float:
        return self.single.get(h, (0.0, 0))[0]

    def syn(self, a: int, c: int) -> tuple[float, int]:
        return self.synergy.get((min(a, c), max(a, c)), (0.0, 0))

    def ctr(self, a: int, b: int) -> tuple[float, int]:
        return self.counter.get((a, b), (0.0, 0))


def build_tables(matches: list[LineupMatch], k: float, k_single: float = 20,
                 extra=None, extra_weight: float = 1.0) -> Tables:
    """Shrunk residual log-odds tables. Residual = logit(shrunk cell rate) - logit(expected rate from singles).

    `extra` (public.PublicCounts, high-MMR ladder games) is added to every count with `extra_weight` per game; it is
    never held out by the OOF folds because no pro test match is in it."""
    n1: dict = defaultdict(int); w1: dict = defaultdict(int)
    for m in matches:
        for h in m.radiant:
            n1[h] += 1; w1[h] += m.radiant_win
        for h in m.dire:
            n1[h] += 1; w1[h] += 1 - m.radiant_win
    syn_n: dict = defaultdict(int); syn_w: dict = defaultdict(int)
    ctr_n: dict = defaultdict(int); ctr_w: dict = defaultdict(int)
    for m in matches:
        for team, won in ((m.radiant, m.radiant_win), (m.dire, 1 - m.radiant_win)):
            for a, c in combinations(team, 2):
                syn_n[(a, c)] += 1; syn_w[(a, c)] += won
        for a in m.radiant:
            for b in m.dire:
                key = (a, b) if a < b else (b, a)
                ctr_n[key] += 1; ctr_w[key] += m.radiant_win if a < b else 1 - m.radiant_win
    if extra is not None and extra_weight > 0 and extra.n_matches:
        pw = float(extra_weight)
        for h, n in extra.single_n.items():
            n1[h] += pw * n; w1[h] += pw * extra.single_w[h]
        for key, n in extra.syn_n.items():
            syn_n[key] += pw * n; syn_w[key] += pw * extra.syn_w[key]
        for key, n in extra.ctr_n.items():
            ctr_n[key] += pw * n; ctr_w[key] += pw * extra.ctr_w[key]
    single = {h: (_logit((w1[h] + k_single * 0.5) / (n1[h] + k_single)), n1[h]) for h in n1}
    s = lambda h: single.get(h, (0.0, 0))[0]
    synergy = {}
    for (a, c), n in syn_n.items():
        p0 = float(_sigmoid(s(a) + s(c)))
        synergy[(a, c)] = (_logit((syn_w[(a, c)] + k * p0) / (n + k)) - _logit(p0), n)
    counter = {}
    for (a, b), n in ctr_n.items():          # stored with a < b; effect = a beats b
        p0 = float(_sigmoid(s(a) - s(b)))
        e = _logit((ctr_w[(a, b)] + k * p0) / (n + k)) - _logit(p0)
        counter[(a, b)] = (e, n); counter[(b, a)] = (-e, n)
    base = sum(m.radiant_win for m in matches) / len(matches) if matches else 0.5
    return Tables(k, single, synergy, counter, base)


# ---------------------------------------------------------------- features
def _missing_roles(team, roles: dict, targets: dict) -> int:
    have: dict = defaultdict(int)
    for h in team:
        for t in roles.get(h, ()):
            have[t] += 1
    return sum(max(0, need - have[t]) for t, need in targets.items())


def features(t: Tables, radiant, dire, roles: dict | None = None, targets: dict | None = None) -> dict:
    f = {"hero": sum(t.s(h) for h in radiant) - sum(t.s(h) for h in dire),
         "synergy": sum(t.syn(a, c)[0] for a, c in combinations(radiant, 2)) - sum(t.syn(a, c)[0] for a, c in combinations(dire, 2)),
         "counter": sum(t.ctr(a, b)[0] for a in radiant for b in dire),
         "gap": 0.0}
    if roles and targets:
        f["gap"] = float(_missing_roles(dire, roles, targets) - _missing_roles(radiant, roles, targets))
    return f


def _design(F: list[dict]) -> np.ndarray:
    return np.array([[1.0] + [f[k] for k in FEATURES] for f in F])


# ---------------------------------------------------------------- stacker
def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1e-3, iters: int = 50) -> np.ndarray:
    """Newton-Raphson logistic regression; intercept (column 0) is not penalised."""
    beta = np.zeros(X.shape[1])
    pen = np.full(X.shape[1], l2); pen[0] = 0.0
    for _ in range(iters):
        p = _sigmoid(X @ beta)
        g = X.T @ (p - y) + pen * beta
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(pen) + 1e-9 * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        beta = beta - step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


@dataclass
class LineupPrediction:
    p_radiant: float
    logit: float
    features: dict
    contributions: dict          # logit units: side + one per feature
    per_hero: dict               # hero -> {side, hero, synergy, counter, games}   (logit units, signed for radiant)
    thin_cells: list             # pair cells with n < k (they are shrunk to ~0)
    n_cells: int


@dataclass
class LineupModel:
    tables: Tables
    beta: np.ndarray                  # [intercept] + FEATURES
    beta_hero: np.ndarray             # [intercept, hero]: the hero-only ablation, fitted on the training OOF rows
    roles: dict
    targets: dict
    n_train: int
    k_search: list = field(default_factory=list)   # [{k, logloss}] from the OOF grid
    n_public: int = 0                              # ladder matches folded into the tables (0 = pro only)
    public_weight: float = 0.0

    def predict(self, radiant, dire) -> LineupPrediction:
        radiant, dire = [int(h) for h in radiant], [int(h) for h in dire]
        f = features(self.tables, radiant, dire, self.roles, self.targets)
        x = np.array([1.0] + [f[k] for k in FEATURES])
        contrib = {"side": float(self.beta[0])} | {k: float(self.beta[i + 1] * f[k]) for i, k in enumerate(FEATURES)}
        z = float(x @ self.beta)
        t = self.tables
        thin = [((a, b), n) for a in radiant for b in dire for _, n in [t.ctr(a, b)] if n < t.k] + \
               [((a, c), n) for team in (radiant, dire) for a, c in combinations(team, 2) for _, n in [t.syn(a, c)] if n < t.k]
        per_hero = {}
        for side, team, opp, sign in ((0, radiant, dire, 1.0), (1, dire, radiant, -1.0)):
            for h in team:
                per_hero[h] = {"side": side,
                               "hero": sign * self.beta[1] * t.s(h),
                               # half of each pair effect books to each participant, so a column sums to its
                               # contribution above instead of twice it
                               "synergy": 0.5 * sign * self.beta[2] * sum(t.syn(h, c)[0] for c in team if c != h),
                               "counter": 0.5 * sign * self.beta[3] * sum(t.ctr(h, b)[0] for b in opp),
                               "games": t.single.get(h, (0.0, 0))[1]}
        return LineupPrediction(float(_sigmoid(z)), z, f, contrib, per_hero, thin, 25 + 20)


def _folds(n: int, folds: int) -> list[np.ndarray]:
    idx = np.arange(n)
    return [idx[i::folds] for i in range(folds)]


def oof_features(matches: list[LineupMatch], k: float, folds: int, roles, targets, k_single: float,
                 extra=None, extra_weight: float = 1.0) -> list[dict]:
    """Aggregates for every training match from tables that never saw that match (interleaved folds)."""
    out: list = [None] * len(matches)
    for hold in _folds(len(matches), folds):
        hs = set(hold.tolist())
        t = build_tables([m for i, m in enumerate(matches) if i not in hs], k, k_single, extra, extra_weight)
        for i in hold:
            out[i] = features(t, matches[i].radiant, matches[i].dire, roles, targets)
    return out


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit(matches: list[LineupMatch], roles: dict | None = None, targets: dict | None = None, cfg: dict | None = None,
        k: float | None = None, extra=None) -> LineupModel:
    """Pick k on out-of-fold log-loss (unless given), fit the stacker on OOF aggregates, then build final tables on everything.
    `extra` = public.PublicCounts added to the tables with cfg["public_weight"] per ladder game (0 = ignored)."""
    c = {**DEFAULT_CFG, **(cfg or {})}
    roles, targets = roles or {}, targets or {}
    pw = float(c.get("public_weight", 0.0) or 0.0)
    if extra is None or pw <= 0 or not getattr(extra, "n_matches", 0):
        extra, pw = None, 0.0
    if len(matches) < 50:
        raise ValueError(f"lineup model needs at least 50 training matches, got {len(matches)}")
    matches = sorted(matches, key=lambda m: (m.start_time, m.match_id))
    y = np.array([m.radiant_win for m in matches], dtype=float)
    search = []
    grid = [k] if k is not None else list(c["k_grid"])
    best = None
    for kk in grid:
        F = oof_features(matches, kk, c["folds"], roles, targets, c["single_prior_strength"], extra, pw)
        X = _design(F)
        # the stackers themselves are fitted in-sample on OOF aggregates (a handful of coefficients cannot overfit
        # thousands of rows); the hero-only ablation shares these rows so it never sees any test label either
        b = fit_logistic(X, y, c["l2"])
        b_hero = fit_logistic(X[:, :2], y, c["l2"])
        ll = log_loss(_sigmoid(X @ b), y)
        search.append({"k": kk, "logloss": ll})
        if best is None or ll < best[0]:
            best = (ll, kk, b, b_hero)
    _, k_best, beta, beta_hero = best
    tables = build_tables(matches, k_best, c["single_prior_strength"], extra, pw)
    n_public = extra.n_matches if extra is not None else 0
    log.info("lineup: fitted on %d matches (+%d ladder x %.2f), k=%s, beta=%s", len(matches), n_public, pw, k_best,
             np.round(beta, 3).tolist())
    return LineupModel(tables, beta, beta_hero, roles, targets, len(matches), search, n_public, pw)


# ---------------------------------------------------------------- evaluation
def evaluate(model: LineupModel, tests: list[LineupMatch], bins: int = 8) -> dict:
    """Log-loss / Brier / accuracy vs a constant baseline and a hero-only ablation, plus a reliability table."""
    y = np.array([m.radiant_win for m in tests], dtype=float)
    p = np.array([model.predict(m.radiant, m.dire).p_radiant for m in tests])
    base = model.tables.base_rate
    p_const = np.full_like(y, base)
    # hero-only ablation: same tables, its stacker was fitted on the training OOF rows (fit), never on these labels
    F = [features(model.tables, m.radiant, m.dire, model.roles, model.targets) for m in tests]
    X_hero = np.array([[1.0, f["hero"]] for f in F])
    p_hero = _sigmoid(X_hero @ model.beta_hero)

    def summary(pp):
        return {"logloss": log_loss(pp, y), "brier": float(np.mean((pp - y) ** 2)), "accuracy": float(np.mean((pp >= 0.5) == (y == 1)))}
    edges = np.linspace(0, 1, bins + 1)
    rel = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if sel.any():
            rel.append({"bin": f"{lo:.2f}-{hi:.2f}", "n": int(sel.sum()), "pred": float(p[sel].mean()), "actual": float(y[sel].mean())})
    return {"n": len(tests), "base_rate": base, "model": summary(p), "constant": summary(p_const), "hero_only": summary(p_hero),
            "reliability": rel, "k": model.tables.k,
            "beta": {"side": float(model.beta[0])} | {k: float(model.beta[i + 1]) for i, k in enumerate(FEATURES)},
            "k_search": model.k_search, "n_train": model.n_train, "n_public": model.n_public, "public_weight": model.public_weight,
            "spread": {"p10": float(np.percentile(p, 10)), "p50": float(np.percentile(p, 50)), "p90": float(np.percentile(p, 90))}}


def to_markdown(res: dict, snapshot_version: str | None, patch: str | None, label: str) -> str:
    L = ["# Lineup win-probability baseline", "",
         f"Train: {res['n_train']} matches before snapshot as_of (data_version {snapshot_version or '?'}), patch {patch}"
         + (f", plus {res['n_public']} high-MMR ladder matches at weight {res['public_weight']:g} per game" if res.get("n_public") else "")
         + ". "
         f"Test: {res['n']} later real matches ({label}). Prior strength k={res['k']} chosen on out-of-fold log-loss.", "",
         "Model = shrunk residual tables (hero / synergy / counter / role gap) + calibrated logistic stacker. "
         "Lower log-loss / Brier is better; constant = training radiant win rate; hero_only = same tables, its stacker "
         "fitted on out-of-fold training aggregates (never on the test set).", "",
         "| model | log-loss | Brier | accuracy |", "|---|---:|---:|---:|"]
    for name in ("model", "hero_only", "constant"):
        m = res[name]
        L.append(f"| {name} | {m['logloss']:.4f} | {m['brier']:.4f} | {100 * m['accuracy']:.1f}% |")
    L += ["", "Stacker coefficients (logit units): " + ", ".join(f"{k} {v:+.3f}" for k, v in res["beta"].items()) + ".",
          f"Prediction spread on test: p10 {100 * res['spread']['p10']:.1f}% / p50 {100 * res['spread']['p50']:.1f}% / "
          f"p90 {100 * res['spread']['p90']:.1f}%.", "",
          "k search (out-of-fold log-loss on training): " + ", ".join(f"k={s['k']}: {s['logloss']:.4f}" for s in res["k_search"]) + ".", "",
          "## Reliability (predicted radiant win probability vs actual)", "", "| bin | n | mean predicted | actual |", "|---|---:|---:|---:|"]
    for r in res["reliability"]:
        L.append(f"| {r['bin']} | {r['n']} | {100 * r['pred']:.1f}% | {100 * r['actual']:.1f}% |")
    L += ["", "A calibrated model has actual ~= predicted in every row; a row with n < 30 is noise."]
    return "\n".join(L)
