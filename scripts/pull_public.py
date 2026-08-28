"""Pull high-rank public (ladder) matches from OpenDota /publicMatches into data/db/public.sqlite.

Why: the pro dataset (~7k matches per patch) cannot support hero-pair counter/synergy tables (docs/lineup-baseline.md);
ladder games at Divine+ are the same heroes played hundreds of thousands of times. This is the dense prior for `bp lineup`.

What it does
  - walks /publicMatches newest -> oldest with less_than_match_id, min_rank filter (default 70 = Divine 1+)
  - stores every row (all modes; filter at training time) with a `patch` derived from the patches table
  - keeps its cursor in the DB, so Ctrl-C / crash / reboot -> rerun the same command and it continues
  - stops when it reaches --until (default: release of the oldest patch requested) or --target rows
  - when the daily OpenDota budget is gone it sleeps (wall clock, chunked) until the next UTC day and continues

Throughput: 100 rows per call, free tier ~3000 calls/day -> ~300k matches/day. Run on a machine that is NOT also
running the pro backfill: the daily quota is per IP.

  python scripts/pull_public.py --patch 7.41 --target 1500000
  python scripts/pull_public.py --status
"""
from __future__ import annotations
import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bp.config import CONFIG  # noqa: E402
from bp.opendota import OpenDota, DailyBudgetExceeded  # noqa: E402

log = logging.getLogger("pull_public")

SCHEMA = """
CREATE TABLE IF NOT EXISTS public_matches (
  match_id INTEGER PRIMARY KEY, match_seq_num INTEGER, start_time INTEGER, duration INTEGER,
  radiant_win INTEGER, lobby_type INTEGER, game_mode INTEGER, avg_rank_tier INTEGER, num_rank_tier INTEGER,
  cluster INTEGER, patch TEXT, radiant TEXT, dire TEXT
);
CREATE INDEX IF NOT EXISTS ix_public_start ON public_matches(start_time);
CREATE INDEX IF NOT EXISTS ix_public_patch_mode ON public_matches(patch, game_mode, lobby_type);
CREATE TABLE IF NOT EXISTS pull_state (key TEXT PRIMARY KEY, value TEXT);
"""


def _heroes(v) -> list[int] | None:
    """radiant_team / dire_team come back as "1,2,3,4,5" on older API builds and as [1,2,3,4,5] on newer ones."""
    if v is None:
        return None
    if isinstance(v, str):
        v = [x for x in v.split(",") if x.strip()]
    try:
        ids = [int(x) for x in v]
    except (TypeError, ValueError):
        return None
    return ids if len(ids) == 5 and len(set(ids)) == 5 else None


def _patch_for(start_time: int, patches: list[tuple[str, int]]) -> str | None:
    """patches: [(name, release_time)] sorted ascending -> name of the last patch released before start_time."""
    name = None
    for n, t in patches:
        if t <= start_time:
            name = n
        else:
            break
    return name


def load_patches(pro_db: Path) -> list[tuple[str, int]]:
    if not pro_db.exists():
        sys.exit(f"pro DB {pro_db} not found: run `python -m bp constants` first so the patches table exists")
    con = sqlite3.connect(pro_db)
    rows = con.execute("SELECT name, release_time FROM patches WHERE release_time IS NOT NULL ORDER BY release_time").fetchall()
    con.close()
    if not rows:
        sys.exit("patches table is empty: run `python -m bp constants`")
    return [(str(n), int(t)) for n, t in rows]


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def get_state(con, key, default=None):
    r = con.execute("SELECT value FROM pull_state WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_state(con, key, value):
    con.execute("INSERT OR REPLACE INTO pull_state VALUES (?,?)", (key, json.dumps(value)))


def status(con) -> dict:
    n, lo, hi = con.execute("SELECT COUNT(*), MIN(start_time), MAX(start_time) FROM public_matches").fetchone()
    by_patch = con.execute("SELECT patch, COUNT(*) FROM public_matches GROUP BY patch ORDER BY 2 DESC").fetchall()
    ranked_ap = con.execute("SELECT COUNT(*) FROM public_matches WHERE lobby_type=7 AND game_mode=22").fetchone()[0]
    fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M") if t else None
    return {"rows": n, "oldest": fmt(lo), "newest": fmt(hi), "ranked_all_pick": ranked_ap, "by_patch": dict(by_patch),
            "cursor": get_state(con, "cursor"), "min_rank": get_state(con, "min_rank"), "pages": get_state(con, "pages", 0),
            "skipped_bad_rows": get_state(con, "skipped", 0)}


def sleep_until(when: datetime, chunk: float = 300.0) -> None:
    """Chunked wall-clock sleep: one long time.sleep() does not count down while the machine sleeps."""
    while (left := (when - datetime.now(timezone.utc)).total_seconds()) > 0:
        time.sleep(min(chunk, left))


def next_utc_day() -> datetime:
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)


def pull(con: sqlite3.Connection, od: OpenDota, patches: list[tuple[str, int]], min_rank: int, until: int,
         target: int | None, max_pages: int | None) -> int:
    """Walk pages until the cursor passes `until`, `target` rows exist, or `max_pages` pages were fetched. Returns rows added."""
    prev_rank = get_state(con, "min_rank")
    if prev_rank is not None and prev_rank != min_rank:
        sys.exit(f"this DB was started with --min-rank {prev_rank}; changing it mid-pull would mix populations. "
                 f"Use a different --db or the same --min-rank.")
    set_state(con, "min_rank", min_rank)
    cursor = get_state(con, "cursor")            # smallest match_id seen so far; None = start from the newest
    pages = get_state(con, "pages", 0)
    skipped = get_state(con, "skipped", 0)
    added = 0
    fetched_pages = 0
    while True:
        total = con.execute("SELECT COUNT(*) FROM public_matches").fetchone()[0]
        if target is not None and total >= target:
            log.info("target reached: %d rows", total); return added
        if max_pages is not None and fetched_pages >= max_pages:
            return added
        rows = od.public_matches_page(less_than_match_id=cursor, min_rank=min_rank)
        fetched_pages += 1; pages += 1
        if not rows:
            log.info("empty page at cursor %s: end of data", cursor); set_state(con, "pages", pages); con.commit(); return added
        page_min = min(int(r["match_id"]) for r in rows)
        oldest = min(int(r["start_time"]) for r in rows)
        batch = []
        for r in rows:
            rad, dire = _heroes(r.get("radiant_team")), _heroes(r.get("dire_team"))
            if rad is None or dire is None or set(rad) & set(dire):
                skipped += 1; continue
            st = int(r["start_time"])
            batch.append((int(r["match_id"]), r.get("match_seq_num"), st, r.get("duration"), int(bool(r.get("radiant_win"))),
                          r.get("lobby_type"), r.get("game_mode"), r.get("avg_rank_tier"), r.get("num_rank_tier"), r.get("cluster"),
                          _patch_for(st, patches), json.dumps(rad, separators=(",", ":")), json.dumps(dire, separators=(",", ":"))))
        cur = con.executemany("INSERT OR IGNORE INTO public_matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        added += cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(batch)
        cursor = page_min
        set_state(con, "cursor", cursor); set_state(con, "pages", pages); set_state(con, "skipped", skipped)
        con.commit()
        if pages % 20 == 0:
            log.info("page %d: cursor %d, oldest in page %s, rows %d, budget left %d", pages, cursor,
                     datetime.fromtimestamp(oldest, timezone.utc).strftime("%Y-%m-%d %H:%M"), total + len(batch), od.budget_left())
        if oldest < until:
            log.info("reached --until (%s): done", datetime.fromtimestamp(until, timezone.utc).date()); return added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="default data/db/public.sqlite")
    ap.add_argument("--pro-db", help="pro DB providing the patches table (default data/db/bp.sqlite)")
    ap.add_argument("--min-rank", type=int, default=70, help="OpenDota rank tier floor: 70 = Divine 1, 80 = Immortal (default 70)")
    ap.add_argument("--patch", action="append", help="repeatable; stop at the release of the oldest one given (default: newest patch)")
    ap.add_argument("--until", help="YYYY-MM-DD: stop when pages are older than this (overrides --patch)")
    ap.add_argument("--target", type=int, help="stop once this many rows are stored")
    ap.add_argument("--max-pages", type=int, help="fetch at most this many pages this run (smoke tests)")
    ap.add_argument("--once", action="store_true", help="exit when the daily budget is exhausted instead of sleeping to the next UTC day")
    ap.add_argument("--status", action="store_true", help="print progress and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    db = Path(a.db) if a.db else CONFIG.data_dir / "db" / "public.sqlite"
    con = open_db(db)
    if a.status:
        print(json.dumps(status(con), ensure_ascii=False, indent=2)); return 0
    patches = load_patches(Path(a.pro_db) if a.pro_db else CONFIG.db_path)
    if a.until:
        until = int(datetime.fromisoformat(a.until).replace(tzinfo=timezone.utc).timestamp())
    else:
        wanted = a.patch or [patches[-1][0]]
        rel = {n: t for n, t in patches}
        unknown = [p for p in wanted if p not in rel]
        if unknown:
            sys.exit(f"unknown patch(es) {unknown}; known: {[n for n, _ in patches][-6:]}")
        until = min(rel[p] for p in wanted)
    log.info("db %s | min_rank %d | until %s | target %s", db, a.min_rank,
             datetime.fromtimestamp(until, timezone.utc).date(), a.target or "none")
    od = OpenDota()
    while True:
        try:
            pull(con, od, patches, a.min_rank, until, a.target, a.max_pages)
            log.info("finished: %s", json.dumps(status(con), ensure_ascii=False)); return 0
        except DailyBudgetExceeded as e:
            con.commit()
            if a.once:
                log.info("daily budget exhausted (%s); --once -> exiting. Rerun tomorrow, it resumes from the cursor.", e); return 0
            nxt = next_utc_day()
            log.info("daily budget exhausted (%s); sleeping until %s UTC", e, nxt.isoformat())
            sleep_until(nxt)
        except KeyboardInterrupt:
            con.commit(); log.info("interrupted; cursor saved: %s", json.dumps(status(con), ensure_ascii=False)); return 130


if __name__ == "__main__":
    sys.exit(main())
