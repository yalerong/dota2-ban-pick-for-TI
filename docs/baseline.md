# Blind-test baseline

## Protocol (frozen 2026-08-26)

- **Snapshot**: `snapshot_20260801_<hash>` — profiles use only matches with `start_time < 2026-08-01`.
- **Test set**: the first 120 clean 7.41 matches starting on/after 2026-08-01 whose two teams exist in the snapshot (2880 draft steps).
- **Metric**: hit rate of the actual pro action inside the model's Top-1/3/5, per slice; baseline = global meta frequency among legal heroes.
- **Weights**: `config/scoring.yaml` as committed with this file. Weights are **not** tuned against this test set.
- **Pending validation**: the 2026-08-26 run used a partial backfill (2577 clean matches total, fewer inside the snapshot).
  When the 7.41 backfill completes, re-export the 2026-08-01 snapshot (more training data, same as_of), rerun
  `bp blindtest --snapshot <new> --patch 7.41 --max 120` with and without `--no-context`, and append the tables below.
  The question being tested: does more data raise the numbers, and do the context terms hold up at full sample size?

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

Any future Policy model must beat the **model** column on the same snapshot before promotion.
