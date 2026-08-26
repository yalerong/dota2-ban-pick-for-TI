# dota2-ban-pick-for-TI

Captain's Mode BP scouting for pro/semi-pro teams. Design: `PLAN.md`; executable task list: `docs/TASKS-phase1-2.md`.

Current state: **Phase 1 (data foundation)** — OpenDota sync, normalized SQLite store, data-inferred draft formats, quality checks, as-of snapshots with a content-hash `data_version`.

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
```

## Notes

- Draft formats are never hand-written; anything not matching the per-patch mode gets `draft_format_anomaly` and is excluded.
- `position_est` is a GPM-rank heuristic (1 = highest GPM). Good enough for cores vs supports; do not trust 3 vs 4.
- Snapshots only include matches with `start_time < as_of`; `players.last_seen` / `teams.last_seen` are recomputed inside the snapshot so nothing after `as_of` leaks.
