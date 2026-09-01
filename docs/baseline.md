# Blind-test baseline

## Protocol (frozen 2026-08-26)

- **Snapshot**: `snapshot_20260801_<hash>` — profiles use only matches with `start_time < 2026-08-01`.
- **Test set**: the 120 clean 7.41 matches in `docs/baseline-test-matches.json`, frozen from Run 1 in chronological order
  (2880 draft steps; test-set hash `6d348cc4215c4225`).
- **Metric**: hit rate of the actual pro action inside the model's Top-1/3/5, per slice; baseline = global meta frequency among legal heroes.
- **Weights**: `config/scoring.yaml` as committed with this file. Weights are **not** tuned against this test set.
- **Incremental validation**: each checkpoint re-exports the same 2026-08-01 cutoff and evaluates the frozen match IDs with and
  without `--no-context`. New data may change the training snapshot and meta-frequency baseline, but never the evaluation matches.
  The frozen test set is an acceptance holdout; model changes must first be developed on a separate set.

## Run 1 — 2026-08-26, partial backfill (2577 clean matches in DB)

Model = linear evidence score; numbers are model / baseline.

### Context terms ON (counter / synergy / role gap)

| slice | steps | top1 | top3 | top5 |
|---|---|---|---|---|
| overall | 2880 | 9.7% / 7.0% | 21.7% / 15.8% | 31.1% / 22.1% |
| bans | 1680 | 10.3% / 8.8% | 22.9% / 18.5% | 32.1% / 25.2% |
| picks | 1200 | 8.9% / 4.4% | 20.1% / 11.9% | 29.8% / 17.8% |
| phase0_ban | 840 | 13.2% / 13.2% | 28.1% / 25.2% | 39.2% / 33.3% |
| phase1_pick | 240 | 16.2% / 1.7% | 35.8% / 11.2% | 48.3% / 22.9% |
| phase2_ban | 360 | 7.5% / 1.1% | 18.6% / 10.6% | 26.9% / 16.9% |
| phase3_pick | 720 | 8.5% / 5.8% | 18.3% / 11.9% | 28.6% / 17.1% |
| phase4_ban | 480 | 7.3% / 6.9% | 17.1% / 12.7% | 23.5% / 17.3% |
| phase5_pick | 240 | 2.9% / 2.9% | 9.6% / 12.5% | 15.0% / 14.6% |

### Context terms OFF (ablation, `--no-context`)

| slice | steps | top1 | top3 | top5 |
|---|---|---|---|---|
| overall | 2880 | 9.8% / 7.0% | 21.4% / 15.8% | 31.1% / 22.1% |
| bans | 1680 | 10.3% / 8.8% | 21.9% / 18.5% | 31.5% / 25.2% |
| picks | 1200 | 9.2% / 4.4% | 20.8% / 11.9% | 30.5% / 17.8% |
| phase0_ban | 840 | 13.2% / 13.2% | 28.1% / 25.2% | 39.2% / 33.3% |
| phase1_pick | 240 | 15.4% / 1.7% | 36.2% / 11.2% | 49.2% / 22.9% |
| phase2_ban | 360 | 8.9% / 1.1% | 18.3% / 10.6% | 29.4% / 16.9% |
| phase3_pick | 720 | 9.7% / 5.8% | 19.4% / 11.9% | 29.3% / 17.1% |
| phase4_ban | 480 | 6.2% / 6.9% | 13.8% / 12.7% | 19.8% / 17.3% |
| phase5_pick | 240 | 1.2% / 2.9% | 9.2% / 12.5% | 15.4% / 14.6% |

**Read**: context terms help bans (+1.0 Top-3 overall, +3.3 in phase-4 bans) and are neutral-to-slightly-negative on picks
(-0.7 Top-3). Last-phase picks still lose to the meta baseline either way. With 2.5k matches the pairwise counter/synergy
matrices are mostly shrunk to 0 — direction plausible, sample too thin. Kept ON pending the full-data rerun.


## Run 1b — 2026-08-26, same snapshot/test set, targeted-ban redefined

`targeted_ban_pressure` = ban rate vs this team **minus the patch-wide ban rate** (clipped at 0), so a meta hero banned
against everyone no longer inflates a player's signature. Context terms ON.

| slice | steps | top1 | top3 | top5 |
|---|---|---|---|---|
| overall | 2880 | 9.3% / 7.0% | 21.7% / 15.8% | 30.3% / 22.1% |
| bans | 1680 | 9.7% / 8.8% | 22.6% / 18.5% | 31.4% / 25.2% |
| picks | 1200 | 8.7% / 4.4% | 20.4% / 11.9% | 28.7% / 17.8% |
| phase0_ban | 840 | 12.3% / 13.2% | 26.8% / 25.2% | 36.9% / 33.3% |
| phase1_pick | 240 | 16.7% / 1.7% | 35.0% / 11.2% | 45.8% / 22.9% |
| phase2_ban | 360 | 7.2% / 1.1% | 20.0% / 10.6% | 27.8% / 16.9% |
| phase3_pick | 720 | 7.9% / 5.8% | 18.8% / 11.9% | 27.6% / 17.1% |
| phase4_ban | 480 | 7.1% / 6.9% | 17.1% / 12.7% | 24.6% / 17.3% |
| phase5_pick | 240 | 2.9% / 2.9% | 10.8% / 12.5% | 15.0% / 14.6% |

**Read**: hit rates unchanged within noise (Top-3 overall 21.7% both ways; picks +0.3, bans -0.3, last-phase picks +1.2).
The change is about *what the score means*, not accuracy: e.g. Yatoro's Lone Druid went from "banned 25% -> signature"
to "meta hero (51% banned patch-wide), not targeted", while Drow Ranger stays targeted (+21pt over meta).

## Run 2 — 2026-08-27, incremental checkpoint (5355 clean matches in DB)

Snapshot `98ead296cc1f3808` contains 4991 clean pre-cutoff matches, up from roughly 2200 in Run 1. The fixed test set is
`6d348cc4215c4225`; no weights changed between runs.

### Context terms ON

| slice | steps | top1 | top3 | top5 |
|---|---|---|---|---|
| overall | 2880 | 8.8% / 5.3% | 21.7% / 12.2% | 30.7% / 18.8% |
| bans | 1680 | 9.1% / 7.4% | 23.4% / 13.0% | 32.3% / 19.7% |
| picks | 1200 | 8.4% / 2.5% | 19.4% / 11.0% | 28.3% / 17.4% |
| phase0_ban | 840 | 11.0% / 9.8% | 28.7% / 14.4% | 38.6% / 24.6% |
| phase1_pick | 240 | 11.7% / 2.1% | 33.8% / 8.8% | 43.8% / 17.5% |
| phase2_ban | 360 | 7.5% / 2.2% | 19.7% / 10.6% | 28.1% / 12.2% |
| phase3_pick | 720 | 9.0% / 2.4% | 18.1% / 12.2% | 27.1% / 19.4% |
| phase4_ban | 480 | 7.1% / 7.1% | 16.9% / 12.5% | 24.6% / 16.7% |
| phase5_pick | 240 | 3.3% / 3.3% | 9.2% / 9.6% | 16.7% / 11.2% |

### Context terms OFF

| slice | steps | top1 | top3 | top5 |
|---|---|---|---|---|
| overall | 2880 | 9.1% / 5.3% | 21.5% / 12.2% | 30.6% / 18.8% |
| bans | 1680 | 9.2% / 7.4% | 22.4% / 13.0% | 31.4% / 19.7% |
| picks | 1200 | 8.8% / 2.5% | 20.2% / 11.0% | 29.5% / 17.4% |
| phase0_ban | 840 | 11.0% / 9.8% | 28.7% / 14.4% | 38.6% / 24.6% |
| phase1_pick | 240 | 12.5% / 2.1% | 35.0% / 8.8% | 42.5% / 17.5% |
| phase2_ban | 360 | 10.0% / 2.2% | 19.2% / 10.6% | 30.0% / 12.2% |
| phase3_pick | 720 | 9.6% / 2.4% | 19.0% / 12.2% | 29.7% / 19.4% |
| phase4_ban | 480 | 5.6% / 7.1% | 14.0% / 12.5% | 20.0% / 16.7% |
| phase5_pick | 240 | 2.9% / 3.3% | 8.8% / 9.6% | 15.8% / 11.2% |

### Top-3 comparison

| slice | Run 1b ON | Run 2 ON | Run 2 − Run 1b | Run 2 OFF | ON − OFF |
|---|---:|---:|---:|---:|---:|
| overall | 21.7% | 21.7% | 0.0pt | 21.5% | +0.2pt |
| bans | 22.6% | 23.4% | +0.8pt | 22.4% | +1.0pt |
| picks | 20.4% | 19.4% | -1.0pt | 20.2% | -0.8pt |
| phase0_ban | 26.8% | 28.7% | +1.9pt | 28.7% | 0.0pt |
| phase1_pick | 35.0% | 33.8% | -1.2pt | 35.0% | -1.2pt |
| phase2_ban | 20.0% | 19.7% | -0.3pt | 19.2% | +0.5pt |
| phase3_pick | 18.8% | 18.1% | -0.7pt | 19.0% | -0.9pt |
| phase4_ban | 17.1% | 16.9% | -0.2pt | 14.0% | +2.9pt |
| phase5_pick | 10.8% | 9.2% | -1.6pt | 8.8% | +0.4pt |

**Read**: doubling the pre-cutoff training sample did not improve overall Top-3. Bans improved by 0.8pt, while picks fell by
1.0pt. The context ablation repeated the earlier pattern: context helps bans (+1.0pt overall and +2.9pt in phase 4) but
hurts picks (-0.8pt). Last-phase picks remain below the current meta baseline (9.2% vs 9.6%). The meta baseline changed because
its training frequencies changed; the 120 evaluation matches did not. No weights were changed or promoted from this result.

Next experiment: use a separate development match set to test action-specific context handling. Keep this frozen set untouched
until a candidate is selected, then require it to beat the Run 2 model column before promotion.

Any future Policy model must beat the **Run 2 model** column on this fixed test set before promotion.

## Run 3 — 2026-08-27, independent development set

Snapshot `58e7a04b5d620dcd` contains 6585 clean pre-cutoff matches. The independent development set is the 120-match
`c8b39c666b2141bc` list in `docs/development-test-matches.json`; it has no overlap with the frozen acceptance set.

The action-specific experiment compared context on every action, no context, and context on bans only:

| mode | dev overall Top-3 | dev bans Top-3 | dev picks Top-3 | dev overall Top-5 |
|---|---:|---:|---:|---:|
| all actions | 16.2% | 18.9% | 12.5% | 24.0% |
| bans only | 16.2% | 18.9% | 12.5% | 24.1% |
| off | 15.7% | 18.0% | 12.5% | 23.9% |

The bans-only candidate was not clearly better on development data, but was carried to the frozen acceptance set once because
it preserved Top-3 and slightly improved Top-5. On acceptance, all-actions vs bans-only was 21.5% vs 21.5% Top-3 and 30.2%
vs 30.4% Top-5; Top-1 moved 9.0% to 8.9%. The new snapshot's all-actions Top-3 (21.5%) also remains just below Run 2
(21.7%), so neither the checkpoint nor bans-only mode is promoted. The code retains the action-specific switch for future
experiments; production behavior remains context on all actions.

## Lineup win probability (separate metric)

`bp lineup-eval` trains the ten-hero win-probability model on the same 2026-08-01 snapshot and scores every clean 7.41 match
after the cutoff (and the frozen 120 as a second file). Results and the calibration table live in `docs/lineup-baseline.md` /
`docs/lineup-baseline-frozen.md`. Promotion rule for that model: lower log-loss than the committed report on the same test
set, and reliability rows with n >= 30 within 5pt of the diagonal.
