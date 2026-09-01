# Lineup win-probability baseline

Train: 6585 matches before snapshot as_of (data_version 58e7a04b5d620dcd), patch 7.41. Test: 364 later real matches (all clean 7.41 matches after as_of). Prior strength k=100 chosen on out-of-fold log-loss.

Model = shrunk residual tables (hero / synergy / counter / role gap) + calibrated logistic stacker. Lower log-loss / Brier is better; constant = training radiant win rate; hero_only = same tables, stacker refitted on hero strength alone.

| model | log-loss | Brier | accuracy |
|---|---:|---:|---:|
| model | 0.6903 | 0.2485 | 54.7% |
| hero_only | 0.6903 | 0.2486 | 51.6% |
| constant | 0.6928 | 0.2498 | 51.6% |

Stacker coefficients (logit units): side +0.112, hero +0.494, synergy +0.196, counter +0.219, gap +0.027.
Prediction spread on test: p10 43.9% / p50 52.7% / p90 60.4%.

k search (out-of-fold log-loss on training): k=10: 0.6841, k=30: 0.6838, k=50: 0.6836, k=100: 0.6835.

## Reliability (predicted radiant win probability vs actual)

| bin | n | mean predicted | actual |
|---|---:|---:|---:|
| 0.25-0.38 | 2 | 35.9% | 0.0% |
| 0.38-0.50 | 121 | 45.5% | 46.3% |
| 0.50-0.62 | 221 | 55.3% | 54.8% |
| 0.62-0.75 | 20 | 64.7% | 55.0% |

A calibrated model has actual ~= predicted in every row; a row with n < 30 is noise.