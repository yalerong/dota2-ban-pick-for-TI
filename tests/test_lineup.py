"""Lineup win-probability model: shrinkage invariants, side symmetry, and that it learns a planted structure."""
from __future__ import annotations
import math
import random

import numpy as np
import pytest

from bp import lineup as L

HEROES = list(range(1, 31))
STRENGTH = {h: 0.35 * (1 if h % 3 == 0 else -1 if h % 3 == 1 else 0) for h in HEROES}   # planted hero strengths (logit)
COUNTER = {(1, 2): 1.2}                                                                     # hero 1 hard-counters hero 2
SIDE = 0.15                                                                                  # radiant advantage


def _synth(n: int, seed: int) -> list[L.LineupMatch]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        ten = rng.sample(HEROES, 10)
        r, d = tuple(sorted(ten[:5])), tuple(sorted(ten[5:]))
        z = SIDE + sum(STRENGTH[h] for h in r) - sum(STRENGTH[h] for h in d)
        for a in r:
            for b in d:
                z += COUNTER.get((a, b), 0.0) - COUNTER.get((b, a), 0.0)
        out.append(L.LineupMatch(i, r, d, int(rng.random() < 1 / (1 + math.exp(-z))), start_time=i))
    return out


def test_tables_shrink_unseen_and_thin_cells_and_are_antisymmetric():
    t = L.build_tables(_synth(400, 1), k=50)
    assert t.ctr(999, 1) == (0.0, 0) and t.syn(999, 1) == (0.0, 0)       # unseen pair is exactly 0
    for (a, b), (e, n) in list(t.counter.items())[:200]:
        e2, n2 = t.ctr(b, a)
        assert n2 == n and abs(e + e2) < 1e-12                             # a beats b  <=>  b loses to a
    thin = [abs(e) for (e, n) in L.build_tables(_synth(60, 1), k=50).counter.values() if n <= 3]
    assert thin and max(thin) < 0.25                                        # 3 games cannot move a k=50 cell much


def test_model_is_side_symmetric_up_to_the_intercept():
    model = L.fit(_synth(1500, 2), k=30)
    r, d = (1, 3, 5, 7, 9), (2, 4, 6, 8, 10)
    z1 = model.predict(r, d).logit - model.beta[0]
    z2 = model.predict(d, r).logit - model.beta[0]
    assert abs(z1 + z2) < 1e-9


def test_model_learns_planted_structure_and_beats_constant_out_of_sample():
    train, test = _synth(4000, 3), _synth(1500, 4)
    model = L.fit(train, k=30)
    res = L.evaluate(model, test)
    assert res["model"]["logloss"] < res["constant"]["logloss"] - 0.01
    assert model.beta[1] > 0 and model.beta[3] > 0                          # hero strength and counter both used
    strong = [h for h in HEROES if STRENGTH[h] > 0][:5]
    weak = [h for h in HEROES if STRENGTH[h] < 0][:5]
    assert model.predict(strong, weak).p_radiant > 0.75
    # the planted counter shows up as a negative cell for the countered hero, and the prediction moves with it
    assert model.tables.ctr(1, 2)[0] > 0.3
    base = model.predict((1, 3, 5, 7, 9), (4, 6, 8, 10, 12)).p_radiant
    with_counter = model.predict((1, 3, 5, 7, 9), (2, 6, 8, 10, 12)).p_radiant
    assert with_counter > base
    assert any(r["n"] > 30 and abs(r["pred"] - r["actual"]) < 0.08 for r in res["reliability"])


def test_fit_refuses_tiny_samples_and_k_search_is_recorded():
    with pytest.raises(ValueError, match="at least 50"):
        L.fit(_synth(20, 5))
    model = L.fit(_synth(300, 6), cfg={"k_grid": [10, 100], "folds": 3})
    assert [s["k"] for s in model.k_search] == [10, 100] and model.tables.k in (10, 100)


def test_markdown_report_lists_all_baselines():
    model = L.fit(_synth(600, 7), k=30)
    md = L.to_markdown(L.evaluate(model, _synth(300, 8)), "ver", "7.41", "unit")
    assert "| model |" in md and "| hero_only |" in md and "| constant |" in md and "## Reliability" in md


@pytest.fixture(scope="module")
def fitted():
    return L.fit(_synth(1500, 2), k=30)


def test_hero_only_ablation_is_scored_with_the_training_fit(fitted):
    tests = _synth(400, 9)
    res = L.evaluate(fitted, tests)
    F = [L.features(fitted.tables, m.radiant, m.dire) for m in tests]
    p = L._sigmoid(np.array([[1.0, f["hero"]] for f in F]) @ fitted.beta_hero)
    assert abs(res["hero_only"]["logloss"] - L.log_loss(p, np.array([m.radiant_win for m in tests], dtype=float))) < 1e-9
    # relabelling the test set must not move a single hero-only prediction: its coefficients come from training only
    flipped = [L.LineupMatch(m.match_id, m.radiant, m.dire, 1 - m.radiant_win, m.start_time) for m in tests]
    res2 = L.evaluate(fitted, flipped)
    assert abs(res2["hero_only"]["accuracy"] - (1 - res["hero_only"]["accuracy"])) < 1e-9


def test_per_hero_pair_columns_sum_to_their_logit_contribution(fitted):
    pred = fitted.predict((1, 3, 5, 7, 9), (2, 4, 6, 8, 10))
    for key in ("hero", "synergy", "counter"):
        assert abs(sum(d[key] for d in pred.per_hero.values()) - pred.contributions[key]) < 1e-9
