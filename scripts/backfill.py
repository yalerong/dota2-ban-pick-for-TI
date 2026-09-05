"""Multi-day backfill driver: fetch until the daily OpenDota budget runs out, post-process, sleep to next UTC day, repeat.

    python scripts/backfill.py --since 2025-12-16
Logs to data/backfill.log. Stops when every indexed match has a detail_status.
"""
from __future__ import annotations
import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bp.config import CONFIG  # noqa: E402
from bp.db import connect  # noqa: E402
from bp.opendota import OpenDota  # noqa: E402
from bp.constants import load_constants  # noqa: E402
from bp.sync import sync_index, sync_matches  # noqa: E402
from bp.normalize import normalize_all  # noqa: E402
from bp.draft_formats import infer_formats  # noqa: E402
from bp.quality import run_checks  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DD (UTC); oldest match start to index")
    ap.add_argument("--reserve", type=int, default=50, help="daily calls to leave unused")
    ap.add_argument("--full-every", type=int, default=7,
                    help="walk the whole /proMatches index back to --since every N rounds (round 1 is always full); "
                         "other rounds stop at the first page with no new rows, which costs 1-2 calls instead of ~150")
    a = ap.parse_args()
    since_ts = int(datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc).timestamp())

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(CONFIG.data_dir / "backfill.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("backfill")
    con = connect()
    client = OpenDota()
    try:
        log.info("constants: %s", load_constants(con))
    except Exception as e:
        log.warning("constants skipped: %s", e)

    round_no = 0
    while True:
        round_no += 1
        log.info("=== round %d, budget left %d ===", round_no, client.budget_left())
        try:
            # /proMatches pages are keyed by less_than_match_id and the newest page shifts every day, so a full walk
            # never hits the page cache: keep it for round 1 and every --full-every rounds (late-parsed matches)
            full = round_no == 1 or (a.full_every > 0 and round_no % a.full_every == 0)
            log.info("index (%s): %s", "full" if full else "incremental", sync_index(client, con, since_ts=since_ts, full=full))
            st = sync_matches(client, con, since_ts=since_ts)
            log.info("details: %s", st)
        except Exception as e:
            log.exception("round %d failed: %s", round_no, e)
            st = {"remaining": -1, "budget_stop": True}
        log.info("normalize: %s", normalize_all(con, client))
        log.info("formats: %s", [(r["patch"], r["n_actions"], r["share"], r["total"]) for r in infer_formats(con)])
        chk = run_checks(con)
        log.info("check: matches=%d clean=%d flags=%s", chk["matches"], chk["clean"],
                 {k: v["count"] for k, v in chk["flags"].items()})
        pending = con.execute("SELECT COUNT(*) FROM match_index WHERE detail_status IS NULL AND start_time >= ?",
                              (since_ts,)).fetchone()[0]
        if pending == 0:
            log.info("backfill complete")
            return 0
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        wait = (nxt - now).total_seconds()
        log.info("%d pending; sleeping %.1f h until %s", pending, wait / 3600, nxt.isoformat())
        sleep_until(nxt)


def sleep_until(when: datetime, chunk: float = 300.0) -> None:
    """Sleep against the wall clock in short chunks: a single long time.sleep() does not count down while the
    machine is asleep, so after a laptop nap the driver would overshoot the UTC budget reset by hours."""
    while (left := (when - datetime.now(timezone.utc)).total_seconds()) > 0:
        time.sleep(min(chunk, left))


if __name__ == "__main__":
    sys.exit(main())
