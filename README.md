# Dota 2 Ban/Pick Scout

[![Release](https://img.shields.io/github/v/release/yalerong/dota2-ban-pick-for-TI)](https://github.com/yalerong/dota2-ban-pick-for-TI/releases)
[![CI](https://github.com/yalerong/dota2-ban-pick-for-TI/actions/workflows/ci.yml/badge.svg)](https://github.com/yalerong/dota2-ban-pick-for-TI/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Evidence-backed Captain's Mode scouting for professional and semi-professional Dota 2 teams. The tool builds an OpenDota dataset, profiles teams and players, recommends picks and bans with traceable match evidence, and provides both terminal and browser draft boards.

**Current release: v0.2.0 — hybrid scouting MVP.** It keeps the local statistical recommender authoritative and adds optional AI tactical interpretation for its candidate set.

## Features

- Incremental OpenDota sync with resumable raw-data caching.
- Normalized SQLite data, quality checks, and immutable as-of snapshots.
- Player hero pools, signature scores, targeted-ban pressure, team draft habits, synergy, counters, and opponent responses.
- Evidence-backed Pick/Ban candidates and Markdown matchup reports.
- Interactive terminal and browser draft boards.
- Optional OpenAI-compatible tactical interpretation with validated, local-candidate-only output and automatic fallback.
- Ten-hero lineup win-probability model and frozen-snapshot evaluation commands.

> [!IMPORTANT]
> Recommendations are decision support, not automated draft calls. In the frozen blind test, bans beat the meta-frequency baseline while picks do not yet; review the cited matches, current roster, and patch context before acting on a candidate.

## Requirements

- Python 3.11 or newer.
- Internet access for OpenDota and dotaconstants synchronization.
- An optional `OPENDOTA_API_KEY` for higher practical request capacity. Without a key, OpenDota's free-tier limits apply.

## Install v0.2.0

Install the wheel attached to the GitHub release:

```bash
python -m pip install https://github.com/yalerong/dota2-ban-pick-for-TI/releases/download/v0.2.0/dota2_bp-0.2.0-py3-none-any.whl
bp --help
```

To work from source instead:

```bash
git clone https://github.com/yalerong/dota2-ban-pick-for-TI.git
cd dota2-ban-pick-for-TI
python -m pip install -e .
```

The API key is optional. Set it in your shell before synchronization if you have one:

```bash
# macOS / Linux
export OPENDOTA_API_KEY="your-key"

# Windows PowerShell
$env:OPENDOTA_API_KEY="your-key"
```

## Quick start

```bash
bp constants
bp update --since 2026-03-24 --limit 1500
bp teams --q spirit
bp report --us "Team Spirit" --them "Team Liquid"
bp h5 --matchup "Team Spirit|Team Liquid" --serve
```

The first synchronization can take time because match details are downloaded within the OpenDota request budget. Later runs are incremental and resumable.

## Optional AI tactical analysis

The AI layer never generates the statistical candidate list. It receives the current draft state and the local Top candidates, then returns a validated recommendation, reasons, risks, and alternatives selected only from that list. If the provider fails or returns invalid output, the browser keeps showing the local recommendations.

Every user supplies their own OpenAI-compatible provider credentials at runtime. Never put a real API Key in the repository, README, command history, or a file intended for distribution. Set it in the user's local shell environment and start the local server with `--ai`:

```bash
# macOS / Linux
export AI_API_KEY="your-key"
export AI_MODEL="your-model"
export AI_BASE_URL="https://api.openai.com/v1"  # optional; this is the default

# Windows PowerShell
$env:AI_API_KEY="your-key"
$env:AI_MODEL="your-model"
$env:AI_BASE_URL="https://api.openai.com/v1"   # optional

bp h5 --matchup "Team Spirit|Team Liquid" --serve --ai
```

`OPENAI_API_KEY` and `OPENAI_BASE_URL` are also accepted. The model and base URL can be overridden with `--ai-model` and `--ai-base-url`. Values such as `your-key` above are placeholders only. Each installation uses its own Key; no provider credentials ship in the repository or release package. The API Key stays in the local Python process and is never embedded in the page; the draft state, strategy text, local candidates, and their evidence are sent to the configured provider.

For safety, AI mode only serves on a loopback host (`127.0.0.1`, `localhost`, or `::1`) and remote providers must use HTTPS. Plain HTTP remains available for a model gateway running on the same machine. Run without `--ai` if the browser board must be exposed to a phone over local Wi-Fi.

## Development

```bash
python -m pip install -e ".[dev]"
python -m ruff check src scripts tests
python -m pytest -q
```

Design notes live in [`PLAN.md`](PLAN.md); the original Phase 1–2 task breakdown is in [`docs/TASKS-phase1-2.md`](docs/TASKS-phase1-2.md).

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
bp h5 --matchup "Team Spirit|Team Liquid" --serve      # browser board backed by the authoritative Python recommender
bp --db data/snapshots/snapshot_20260801_<hash>.sqlite report --us ... --them ...   # as-of analysis on a frozen snapshot
bp blindtest --snapshot data/snapshots/snapshot_20260801_<hash>.sqlite --patch 7.41 --test-matches docs/baseline-test-matches.json --out docs/baseline.md
bp lineup --match 8960991322 --swap "Juggernaut=Kez"           # P(radiant | ten heroes), trained only on matches before that one
bp lineup --radiant "Axe,Invoker,Rubick,Hoodwink,Kez" --dire "Bane,Lifestealer,Pangolier,Mirana,Dark Seer"
bp lineup-eval --snapshot data/snapshots/snapshot_20260801_<hash>.sqlite --patch 7.41 --out docs/lineup-baseline.md
```

`bp h5 --serve` opens a local browser page and serves `POST /api/recommend` from the same process, so the board uses
`recommend.py` rather than the simplified offline JavaScript score. Opponent actions are always entered manually; candidates
appear only on our turns. Add `--host 0.0.0.0` to use the page from a phone on the same Wi-Fi. Plain `bp h5` still writes a
single offline HTML file and falls back to the lighter in-page score.

Candidates are **reference material**: on the frozen blind test they beat the meta-frequency baseline on bans but not on
picks (`docs/baseline.md`), so read them against the roster and the match ids rather than as calls.

All scores are linear combinations of explainable statistics; weights live in `config/scoring.yaml`.
Two further components ship switched off (weight 0) until they pass the dev-set / frozen-set protocol in `docs/baseline.md`:
`pick.position_fit` (is the hero in the pool of a roster player who still has no hero) and `ban.their_next_pick`
(share of the opponent's *picks* that followed the current draft prefix). Try them without touching the yaml:

```bash
bp blindtest --snapshot ... --test-matches docs/development-test-matches.json --weight pick.position_fit=1.0 --weight ban.their_next_pick=1.0
```

High-MMR ladder matches (`scripts/pull_public.py` -> `data/db/public.sqlite`) can join the counter / synergy tables as a dense
prior: pass `--public data/db/public.sqlite` and set `context.public_weight` / `lineup.public_weight` (weight of one ladder
game next to one pro game; 0 = off, also via `--weight`). The 1.5M-row count is vectorised and cached, so it costs seconds once.

`bp report` / `bp draft` / `bp lineup` / `bp h5` keep frames, profiles and the fitted lineup model in `data/cache/` keyed by the
DB file, filters, weights and a stamp of the source files, so match-day commands open instantly; `--no-cache` (or
`BP_NO_CACHE=1`) rebuilds.
Draft-context terms (`context.py`): Beta-smoothed counter matrix (hero vs enemy picks so far), synergy matrix (with own picks),
and role-gap fill from dotaconstants tags. `bp blindtest --no-context` runs the ablation.

`bp lineup` is the separate "draft is over, who is favoured" number (what the in-game prediction shows at 0:00): ten heroes only,
no players or teams. Hero / synergy / counter tables are Beta-shrunk residual log-odds (an unseen pair is exactly 0), summed per
match and calibrated by a logistic stacker fitted on out-of-fold aggregates. `docs/lineup-baseline.md` records log-loss / Brier
against a constant and a hero-only baseline plus a reliability table; on pro data alone the pair terms add nothing yet
(see the doc) - which is the reason for adding high-MMR public matches before trusting counters.
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
  web.py           local H5 server: authoritative recommendation API, request validation, no extra dependencies
  blindtest.py     P2-10: replay real drafts after a snapshot's as_of, Top-k hit rate vs meta-frequency baseline
  lineup.py        P(radiant | ten heroes): shrunk residual tables + calibrated logistic stacker
  public.py        ladder matches -> vectorised hero-pair counts (dense prior for context / lineup tables)
  cache.py         pickle cache under data/cache (frames / profiles / lineup model), invalidated by code + data stamps
scripts/backfill.py  multi-day OpenDota backfill driver (full index walk on round 1 and every --full-every rounds)
scripts/pull_public.py  high-rank /publicMatches puller with a resumable cursor
```

CI (`.github/workflows/ci.yml`) runs `ruff check` + `pytest` on every push and pull request.

## Notes

- Draft formats are never hand-written; anything not matching the per-patch mode gets `draft_format_anomaly` and is excluded.
- `position_est` is a GPM-rank heuristic (1 = highest GPM). Good enough for cores vs supports; do not trust 3 vs 4.
- Snapshots only include matches with `start_time < as_of`; `players.last_seen` / `teams.last_seen` are recomputed inside the snapshot so nothing after `as_of` leaks.
