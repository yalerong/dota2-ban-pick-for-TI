# Blind-test baseline

snapshot as_of 1785542400 (data_version 98ead296cc1f3808), patch 7.41, 120 later real matches (test set 6d348cc4215c4225), 2880 draft steps, context terms ON.

Model = linear evidence score (config/scoring.yaml); baseline = global meta frequency among legal heroes. Numbers are hit rates of the actual pro action within the model's Top-k (model / baseline).

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

Any future Policy model must beat the **model** column on the same snapshot before promotion.