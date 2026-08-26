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
            log.info("index: %s", sync_index(client, con, since_ts=since_ts, full=True))
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
        time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main())
