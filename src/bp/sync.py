"""Pro match index backfill / incremental sync (P1-03) and detail fetch (P1-04)."""
from __future__ import annotations
import logging
import sqlite3

from .opendota import OpenDota, DailyBudgetExceeded

log = logging.getLogger(__name__)

INDEX_COLS = ["match_id", "start_time", "duration", "leagueid", "league_name", "series_id",
              "series_type", "radiant_team_id", "dire_team_id", "radiant_name", "dire_name", "radiant_win"]


def sync_index(client: OpenDota, con: sqlite3.Connection, since_ts: int | None = None,
               max_pages: int = 10_000, full: bool = False) -> dict:
    """Walk /proMatches newest->oldest.

    Stops when a whole page is already known (incremental) unless `full`, or when
    the oldest row on a page is before `since_ts`.
    """
    inserted = pages = 0
    last_id: int | None = None
    while pages < max_pages:
        page = client.pro_matches_page(last_id)
        pages += 1
        if not page:
            break
        new = 0
        for m in page:
            vals = [m.get(c) for c in INDEX_COLS]
            vals[11] = None if m.get("radiant_win") is None else int(m["radiant_win"])
            cur = con.execute(
                f"INSERT OR IGNORE INTO match_index ({','.join(INDEX_COLS)}) VALUES ({','.join('?' * len(INDEX_COLS))})",
                vals)
            new += cur.rowcount
        con.commit()
        inserted += new
        last_id = page[-1]["match_id"]
        oldest = page[-1]["start_time"]
        log.info("index page %d: %d new (oldest match %d @ %d)", pages, new, last_id, oldest)
        if since_ts is not None and oldest < since_ts:
            break
        if not full and new == 0:
            break
    return {"pages": pages, "inserted": inserted}


def sync_matches(client: OpenDota, con: sqlite3.Connection, limit: int | None = None,
                 since_ts: int | None = None) -> dict:
    """Fetch /matches/{id} for index rows without a detail_status. Resumable."""
    q = "SELECT match_id FROM match_index WHERE detail_status IS NULL"
    args: list = []
    if since_ts is not None:
        q += " AND start_time >= ?"
        args.append(since_ts)
    q += " ORDER BY start_time DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    ids = [r[0] for r in con.execute(q, args)]
    stats = {"ok": 0, "missing_picks_bans": 0, "not_found": 0, "error": 0, "budget_stop": False}
    for i, mid in enumerate(ids, 1):
        try:
            payload = client.match(mid)
        except DailyBudgetExceeded as e:
            log.warning("%s; %d remaining", e, len(ids) - i + 1)
            stats["budget_stop"] = True
            break
        except Exception as e:  # network gave up etc.
            log.error("match %d: %s", mid, e)
            stats["error"] += 1
            continue
        if payload is None:
            status = "not_found"
        elif not payload.get("picks_bans"):
            status = "missing_picks_bans"
        else:
            status = "ok"
        stats[status] += 1
        con.execute("UPDATE match_index SET detail_status=? WHERE match_id=?", (status, mid))
        if i % 25 == 0:
            con.commit()
            log.info("details %d/%d (%s)", i, len(ids), stats)
    con.commit()
    stats["remaining"] = con.execute(
        "SELECT COUNT(*) FROM match_index WHERE detail_status IS NULL").fetchone()[0]
    return stats
