# Blind-test baseline

snapshot as_of 1785542400 (data_version 98ead296cc1f3808), patch 7.41, 120 later real matches (test set 6d348cc4215c4225), 2880 draft steps, context terms OFF.

Model = linear evidence score (config/scoring.yaml); baseline = global meta frequency among legal heroes. Numbers are hit rates of the actual pro action within the model's Top-k (model / baseline).

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

Any future Policy model must beat the **model** column on the same snapshot before promotion.