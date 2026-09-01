# Lineup win-probability baseline

Train: 6585 matches before snapshot as_of (data_version 58e7a04b5d620dcd), patch 7.41. Test: 120 later real matches (fixed set baseline-test-matches.json, 120 matches). Prior strength k=100 chosen on out-of-fold log-loss.

Model = shrunk residual tables (hero / synergy / counter / role gap) + calibrated logistic stacker. Lower log-loss / Brier is better; constant = training radiant win rate; hero_only = same tables, stacker refitted on hero strength alone.

| model | log-loss | Brier | accuracy |
|---|---:|---:|---:|
| model | 0.6742 | 0.2407 | 57.5% |
| hero_only | 0.6836 | 0.2453 | 54.2% |
| constant | 0.6893 | 0.2481 | 55.0% |

Stacker coefficients (logit units): side +0.112, hero +0.494, synergy +0.196, counter +0.219, gap +0.027.
Prediction spread on test: p10 45.1% / p50 53.5% / p90 60.5%.

k search (out-of-fold log-loss on training): k=10: 0.6841, k=30: 0.6838, k=50: 0.6836, k=100: 0.6835.

## Reliability (predicted radiant win probability vs actual)

| bin | n | mean predicted | actual |
|---|---:|---:|---:|
| 0.25-0.38 | 2 | 35.9% | 0.0% |
| 0.38-0.50 | 33 | 46.0% | 48.5% |
| 0.50-0.62 | 80 | 55.1% | 57.5% |
| 0.62-0.75 | 5 | 65.4% | 80.0% |

A calibrated model has actual ~= predicted in every row; a row with n < 30 is noise.