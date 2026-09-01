# dota2-ban-pick-for-TI

Captain's Mode BP scouting for pro/semi-pro teams. Design: `PLAN.md`; executable task list: `docs/TASKS-phase1-2.md`.

Current state: **Phase 1 (data foundation) + Phase 2 (scouting MVP)** — OpenDota sync, normalized SQLite store, data-inferred draft formats, quality checks, as-of snapshots with a content-hash `data_version`; player/team profiles, signature scores, targeted-ban pressure, draft habits, opponent response edges, evidence-backed Pick/Ban candidates, Markdown scouting report, interactive draft board, blind-test baseline.

## Setup

```bash
pip install -e .[dev]      # or: pip install requests pandas pyyaml pytest
cp .env.example .env       # OPENDOTA_API_KEY optional (free tier: 60/min, 2000/day)
pytest -q
```

## Pipeline

```bash
bp constants                          # heroes + patches from dotaconstants
bp sync index --since 2026-03-24      # /proMatches backfill (incremental afterwards)
bp sync matches --limit 1500          # /matches/{id}, resumable, stops at daily budget
bp normalize                          # -> matches / draft_events / players / teams / roster_snapshots
bp formats                            # infer CM action sequence per patch from data
bp check                              # quality flags; hard flags set matches.excluded=1
bp export --as-of 2026-08-01          # immutable snapshot data/snapshots/snapshot_<date>_<hash>.sqlite
bp status
bp update --since 2026-03-24 --limit 1500   # index+details+normalize+formats+check in one go
```

`python -m bp ...` works without installing (`PYTHONPATH=src`).

## Scouting (Phase 2)

```bash
bp teams --q spirit                                   # resolve team ids / names
bp report --us "Team Spirit" --them "Team Liquid"     # -> reports/<us>-vs-<them>-<patch>.md
bp draft  --us "Team Spirit" --them "Team Liquid" --first them   # interactive board; hero names, undo, state, quit
bp --db data/snapshots/snapshot_20260801_<hash>.sqlite report --us ... --them ...   # as-of analysis on a frozen snapshot
bp blindtest --snapshot data/snapshots/snapshot_20260801_<hash>.sqlite --patch 7.41 --test-matches docs/baseline-test-matches.json --out docs/baseline.md
bp lineup --match 8960991322 --swap "Juggernaut=Kez"           # P(radiant | ten heroes), trained only on matches before that one
bp lineup --radiant "Axe,Invoker,Rubick,Hoodwink,Kez" --dire "Bane,Lifestealer,Pangolier,Mirana,Dark Seer"
bp lineup-eval --snapshot data/snapshots/snapshot_20260801_<hash>.sqlite --patch 7.41 --out docs/lineup-baseline.md
```

All scores are linear combinations of explainable statistics; weights live in `config/scoring.yaml`.
Draft-context terms (`context.py`): Beta-smoothed counter matrix (hero vs enemy picks so far), synergy matrix (with own picks),
and role-gap fill from dotaconstants tags. `bp blindtest --no-context` runs the ablation.

`bp lineup` is the separate "draft is over, who is favoured" number (what the in-game prediction shows at 0:00): ten heroes only,
no players or teams. Hero / synergy / counter tables are Beta-shrunk residual log-odds (an unseen pair is exactly 0), summed per
match and calibrated by a logistic stacker fitted on out-of-fold aggregates. `docs/lineup-baseline.md` records log-loss / Brier
against a constant and a hero-only baseline plus a reliability table; on pro data alone the pair terms add nothing yet
(see the doc), which is the case for adding high-MMR public matches before trusting counters.
Every number in a report carries up to 5 match ids so it can be verified on OpenDota.

## Layout

```
src/bp/
  opendota.py      client: rate limit, retry, raw cache (data/raw/<endpoint>/<id>.json with fetched_at/schema_version)
  sync.py          index backfill/incremental + detail fetch
  constants.py     dotaconstants heroes/patches
  normalize.py     raw payload -> tables; phase = run of same is_pick; position_est = GPM rank within team
  draft_formats.py per-patch action sequence = mode of relative (is_pick, team) signatures
  quality.py       hard/soft flags (see HARD/SOFT)
  export.py        as-of snapshot + data_version
  db.py            schema
  profiles.py      P2-01..05: player x hero stats (Beta-smoothed, time-decayed), signature score, ban pressure,
                   team hero/phase habits, pair synergy, opponent response edges; positions = lane_role + GPM
  recommend.py     P2-07: candidate pick/ban scoring with evidence + predicted response
  context.py       counter / synergy / lineup-gap terms fed into recommend (unseen pair == 0)
  report.py        P2-09: Markdown scouting report
  draft_state.py   P2-06: Captain's Mode state machine (format-parameterized)
  blindtest.py     P2-10: replay real drafts after a snapshot's as_of, Top-k hit rate vs meta-frequency baseline
scripts/backfill.py  multi-day OpenDota backfill driver
```

## Notes

- Draft formats are never hand-written; anything not matching the per-patch mode gets `draft_format_anomaly` and is excluded.
- `position_est` is a GPM-rank heuristic (1 = highest GPM). Good enough for cores vs supports; do not trust 3 vs 4.
- Snapshots only include matches with `start_time < as_of`; `players.last_seen` / `teams.last_seen` are recomputed inside the snapshot so nothing after `as_of` leaks.
