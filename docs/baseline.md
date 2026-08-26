# Blind-test baseline

snapshot as_of 1785542400 (data_version 0cb772bfc0dd5dc5), patch 7.41, 120 later real matches, 2880 draft steps.

Model = linear evidence score (config/scoring.yaml); baseline = global meta frequency among legal heroes. Numbers are hit rates of the actual pro action within the model's Top-k (model / baseline).

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

Any future Policy model must beat the **model** column on the same snapshot before promotion.