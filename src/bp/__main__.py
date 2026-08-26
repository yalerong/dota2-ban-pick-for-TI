"""CLI entry: python -m bp <command>."""
from __future__ import annotations
import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from .config import CONFIG
from .db import connect, TABLES
from .opendota import OpenDota


def _ts(s: str | None) -> int | None:
    if not s:
        return None
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_constants(a):
    from .constants import load_constants
    _out(load_constants(connect(), offline=a.offline))


def cmd_sync_index(a):
    from .sync import sync_index
    _out(sync_index(OpenDota(), connect(), since_ts=_ts(a.since), max_pages=a.max_pages, full=a.full))


def cmd_sync_matches(a):
    from .sync import sync_matches
    _out(sync_matches(OpenDota(), connect(), limit=a.limit, since_ts=_ts(a.since)))


def cmd_normalize(a):
    from .normalize import normalize_all
    _out(normalize_all(connect(), OpenDota(offline=True), rebuild=a.rebuild))


def cmd_formats(a):
    from .draft_formats import infer_formats, describe, load_format
    con = connect()
    res = infer_formats(con, min_support=a.min_support)
    for r in res:
        sig = load_format(con, r["patch"]) if r["trusted"] else None
        r["format"] = describe(sig) if sig else None
    _out(res)


def cmd_check(a):
    from .quality import run_checks
    _out(run_checks(connect()))


def cmd_export(a):
    from .export import export_snapshot
    _out(export_snapshot(connect(), datetime.fromisoformat(a.as_of)))


def cmd_update(a):
    """Incremental: index -> details -> normalize -> formats -> check."""
    from .sync import sync_index, sync_matches
    from .normalize import normalize_all
    from .draft_formats import infer_formats
    from .quality import run_checks
    con, client = connect(), OpenDota()
    out = {"index": sync_index(client, con, since_ts=_ts(a.since))}
    out["matches"] = sync_matches(client, con, limit=a.limit, since_ts=_ts(a.since))
    out["normalize"] = normalize_all(con, client)
    out["formats"] = [{k: r[k] for k in ("patch", "n_actions", "share", "trusted")} for r in infer_formats(con)]
    out["check"] = run_checks(con)
    _out(out)


def cmd_status(a):
    con = connect()
    out = {"db": str(CONFIG.db_path), "budget_left_today": OpenDota().budget_left()}
    for t in TABLES:
        out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    out["detail_status"] = {r[0] or "pending": r[1] for r in
                            con.execute("SELECT detail_status, COUNT(*) FROM match_index GROUP BY 1")}
    out["by_patch"] = {r[0]: {"matches": r[1], "clean": r[2]} for r in
                       con.execute("SELECT patch, COUNT(*), SUM(1-excluded) FROM matches GROUP BY 1 ORDER BY 1")}
    r = con.execute("SELECT MIN(start_time), MAX(start_time) FROM matches").fetchone()
    if r[0]:
        out["time_range"] = [datetime.fromtimestamp(r[0], timezone.utc).isoformat(),
                             datetime.fromtimestamp(r[1], timezone.utc).isoformat()]
    _out(out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="bp", description="Dota 2 BP scouting - data pipeline")
    p.add_argument("-v", "--verbose", action="store_true")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("constants", help="load heroes/patches from dotaconstants"); s.add_argument("--offline", action="store_true"); s.set_defaults(f=cmd_constants)
    sync = sp.add_parser("sync", help="sync from OpenDota").add_subparsers(dest="what", required=True)
    s = sync.add_parser("index"); s.add_argument("--since", help="YYYY-MM-DD"); s.add_argument("--max-pages", type=int, default=10_000); s.add_argument("--full", action="store_true"); s.set_defaults(f=cmd_sync_index)
    s = sync.add_parser("matches"); s.add_argument("--limit", type=int); s.add_argument("--since", help="YYYY-MM-DD"); s.set_defaults(f=cmd_sync_matches)
    s = sp.add_parser("normalize"); s.add_argument("--rebuild", action="store_true"); s.set_defaults(f=cmd_normalize)
    s = sp.add_parser("formats", help="infer draft format per patch"); s.add_argument("--min-support", type=int, default=5); s.set_defaults(f=cmd_formats)
    s = sp.add_parser("check", help="data quality checks"); s.set_defaults(f=cmd_check)
    s = sp.add_parser("export", help="as-of snapshot"); s.add_argument("--as-of", required=True, help="YYYY-MM-DD"); s.set_defaults(f=cmd_export)
    s = sp.add_parser("update", help="index+details+normalize+formats+check"); s.add_argument("--since"); s.add_argument("--limit", type=int); s.set_defaults(f=cmd_update)
    s = sp.add_parser("status"); s.set_defaults(f=cmd_status)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    a.f(a)


if __name__ == "__main__":
    sys.exit(main())
