"""CLI entry: python -m bp <command>."""
from __future__ import annotations
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG
from .db import connect, TABLES
from .opendota import OpenDota


def _ts(s: str | None) -> int | None:
    if not s:
        return None
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _con(a):
    return connect(Path(a.db)) if getattr(a, "db", None) else connect()


def _snapshot_as_of(con) -> int | None:
    r = con.execute("SELECT value FROM meta WHERE key='as_of_ts'").fetchone()
    return int(r[0]) if r else None


def _default_patch(con, as_of: int | None = None) -> str | None:
    q = "SELECT patch FROM matches WHERE excluded=0"
    args: list = []
    if as_of is not None:
        q += " AND start_time < ?"
        args.append(as_of)
    r = con.execute(q + " GROUP BY patch ORDER BY COUNT(*) DESC, patch DESC LIMIT 1", args).fetchone()
    return r[0] if r else None


# ---------------------------------------------------------------- phase 1
def cmd_constants(a):
    from .constants import load_constants
    _out(load_constants(_con(a), offline=a.offline))


def cmd_sync_index(a):
    from .sync import sync_index
    _out(sync_index(OpenDota(), _con(a), since_ts=_ts(a.since), max_pages=a.max_pages, full=a.full))


def cmd_sync_matches(a):
    from .sync import sync_matches
    _out(sync_matches(OpenDota(), _con(a), limit=a.limit, since_ts=_ts(a.since)))


def cmd_normalize(a):
    from .normalize import normalize_all
    _out(normalize_all(_con(a), OpenDota(offline=True), rebuild=a.rebuild))


def cmd_formats(a):
    from .draft_formats import infer_formats, describe, load_format
    con = _con(a)
    res = infer_formats(con, min_support=a.min_support)
    for r in res:
        sig = load_format(con, r["patch"]) if r["trusted"] else None
        r["format"] = describe(sig) if sig else None
    _out(res)


def cmd_check(a):
    from .quality import run_checks
    _out(run_checks(_con(a)))


def cmd_export(a):
    from .export import export_snapshot
    _out(export_snapshot(_con(a), datetime.fromisoformat(a.as_of)))


def cmd_update(a):
    from .sync import sync_index, sync_matches
    from .normalize import normalize_all
    from .draft_formats import infer_formats
    from .quality import run_checks
    con, client = _con(a), OpenDota()
    out = {"index": sync_index(client, con, since_ts=_ts(a.since))}
    out["matches"] = sync_matches(client, con, limit=a.limit, since_ts=_ts(a.since))
    out["normalize"] = normalize_all(con, client)
    out["formats"] = [{k: r[k] for k in ("patch", "n_actions", "share", "trusted")} for r in infer_formats(con)]
    out["check"] = run_checks(con)
    _out(out)


def cmd_status(a):
    con = _con(a)
    out = {"db": str(CONFIG.db_path if not a.db else a.db), "budget_left_today": OpenDota().budget_left()}
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


# ---------------------------------------------------------------- phase 2
def _frames_and_profiles(a):
    from .profiles import load_frames, build_profile, player_hero_stats
    con = _con(a)
    as_of = _ts(getattr(a, "as_of", None))
    if as_of is None:
        as_of = _snapshot_as_of(con)
    patch = a.patch or _default_patch(con, as_of)
    fr = load_frames(con, as_of=as_of, patch=patch)
    us, them = fr.team_id(a.us), fr.team_id(a.them)
    if us is None or them is None:
        sys.exit(f"team not found: us={a.us}->{us} them={a.them}->{them} (try `bp teams --q NAME`)")
    stats = player_hero_stats(fr)
    return con, fr, build_profile(fr, us, stats), build_profile(fr, them, stats)


def _fmt(con, fr, patch):
    from .draft_formats import load_format
    p = patch or (fr.matches.patch.mode().iloc[0] if len(fr.matches) else None)
    return load_format(con, p) if p else None, p


def _data_version(con):
    r = con.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()
    return r[0] if r else None


def cmd_teams(a):
    con = _con(a)
    q = f"%{a.q.lower()}%" if a.q else "%"
    rows = con.execute("""SELECT t.team_id, t.name, COUNT(m.match_id) games, MAX(m.start_time) last
                          FROM teams t JOIN matches m ON t.team_id IN (m.radiant_team_id, m.dire_team_id)
                          WHERE LOWER(t.name) LIKE ? AND m.excluded=0 GROUP BY 1 ORDER BY games DESC LIMIT ?""", (q, a.limit)).fetchall()
    for r in rows:
        print(f"{r[0]:>10}  {r[1]:<30} {r[2]:>4} games  last {datetime.fromtimestamp(r[3], timezone.utc).date()}")


def cmd_report(a):
    from .report import build_report
    con, fr, us, them = _frames_and_profiles(a)
    fmt, patch = _fmt(con, fr, a.patch)
    md = build_report(fr, us, them, fmt, patch, _data_version(con))
    out = Path(a.out) if a.out else CONFIG.root / "reports" / f"{us.name}-vs-{them.name}-{patch}.md".replace("/", "_").replace(" ", "_")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md if a.stdout else f"wrote {out}")


def cmd_h5(a):
    """Single-file mobile page: ladder helper + one section per --matchup "Us|Them"."""
    from .profiles import load_frames, build_profile, player_hero_stats
    from .report import build_report
    from .h5 import ladder_payload, matchup_payload, render
    con = _con(a)
    # pin the patch before loading: the page is labelled with one patch, so every number on it must come from that patch
    patch = a.patch or _default_patch(con, _ts(a.as_of))
    fr = load_frames(con, as_of=_ts(a.as_of), patch=patch)
    fmt, patch = _fmt(con, fr, patch)
    if fmt is None:
        print(f"warning: no draft format for patch {patch}; the Draft Board will be disabled (run `bp formats`)", file=sys.stderr)
    stats = player_hero_stats(fr)
    matchups = []
    for spec in a.matchup or []:
        if "|" not in spec:
            sys.exit(f'--matchup expects "Us|Them", got {spec!r}')
        us_q, them_q = (x.strip() for x in spec.split("|", 1))
        us, them = fr.team_id(us_q), fr.team_id(them_q)
        if us is None or them is None:
            sys.exit(f"team not found: us={us_q}->{us} them={them_q}->{them} (try `bp teams --q NAME`)")
        pu, pt = build_profile(fr, us, stats), build_profile(fr, them, stats)
        matchups.append(matchup_payload(fr, pu, pt, build_report(fr, pu, pt, fmt, patch, _data_version(con))))
    meta = {"patch": patch or "all", "matches": int(len(fr.matches)),
            "as_of": datetime.fromtimestamp(fr.as_of, timezone.utc).strftime("%Y-%m-%d"), "data_version": _data_version(con) or "live-db"}
    out = Path(a.out) if a.out else CONFIG.root / "h5" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(ladder_payload(fr, con, fmt, download_icons=not a.no_icons), matchups, meta), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(matchups)} matchups)")
    print(f"phone on the same Wi-Fi: python -m http.server 8000 -d \"{out.parent}\"  then open http://<this-pc-ip>:8000/")


def cmd_draft(a):
    from .draft_state import DraftState
    from .recommend import candidates, format_candidates
    con, fr, us, them = _frames_and_profiles(a)
    fmt, patch = _fmt(con, fr, a.patch)
    if not fmt:
        sys.exit("no draft format known for this patch")
    # state team 0 == first actor. Profiles passed in first/second order.
    P1, P2 = (us, them) if a.first == "us" else (them, us)
    st = DraftState(fmt, frozenset(fr.heroes))
    scripted = [x.strip() for x in a.actions.split(",")] if a.actions else []
    print(f"{us.name} vs {them.name}, patch {patch}, {'we' if a.first == 'us' else 'they'} act first. "
          f"Type hero name, 'undo', 'state' or 'quit'.")
    while not st.done:
        who = "WE" if (st.next_team == 0) == (a.first == "us") else "THEY"
        print(f"\n--- {who} to {'PICK' if st.next_is_pick else 'BAN'} ---")
        print(format_candidates(candidates(fr, st, P1, P2), st.step, len(fmt)))
        if scripted:
            line = scripted.pop(0); print(f"> {line}")
        else:
            try:
                line = input("> ").strip()
            except EOFError:
                break
        if not line:
            continue
        if line == "quit":
            break
        if line == "undo":
            st.undo(); continue
        if line == "state":
            print("picks P1:", [fr.hero(h) for h in st.picks(0)], "picks P2:", [fr.hero(h) for h in st.picks(1)],
                  "bans:", [fr.hero(h) for h in st.bans()]); continue
        hid = fr.hero_id(line)
        if hid is None:
            print(f"unknown/ambiguous hero '{line}'"); continue
        try:
            st.apply(hid)
        except ValueError as e:
            print(e)
    if st.done:
        print("\nDraft complete.")
        print("P1 picks:", [fr.hero(h) for h in st.picks(0)]); print("P2 picks:", [fr.hero(h) for h in st.picks(1)])


def cmd_blindtest(a):
    import sqlite3
    from .blindtest import blind_test, to_markdown
    snap = sqlite3.connect(a.snapshot); snap.row_factory = sqlite3.Row
    as_of = int(snap.execute("SELECT value FROM meta WHERE key='as_of_ts'").fetchone()[0])
    live = _con(a)
    res = blind_test(snap, live, as_of, until=_ts(a.until), patch=a.patch, league=a.league, max_matches=a.max,
                     context=not a.no_context)
    md = to_markdown(res, _data_version(snap))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(md, encoding="utf-8")
    print(md)


def main(argv=None):
    p = argparse.ArgumentParser(prog="bp", description="Dota 2 BP scouting")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--db", help="sqlite path (default data/db/bp.sqlite); pass a snapshot for as-of analysis")
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

    s = sp.add_parser("teams", help="find team ids"); s.add_argument("--q", default=""); s.add_argument("--limit", type=int, default=30); s.set_defaults(f=cmd_teams)
    for name, fn, hlp in (("report", cmd_report, "pre-match scouting report (markdown)"), ("draft", cmd_draft, "interactive draft board")):
        s = sp.add_parser(name, help=hlp)
        s.add_argument("--us", required=True); s.add_argument("--them", required=True)
        s.add_argument("--patch"); s.add_argument("--as-of", help="YYYY-MM-DD; only use matches before this")
        if name == "report":
            s.add_argument("--out"); s.add_argument("--stdout", action="store_true")
        else:
            s.add_argument("--first", choices=["us", "them"], required=True)
            s.add_argument("--actions", help="comma-separated scripted actions (non-interactive)")
        s.set_defaults(f=fn)
    s = sp.add_parser("h5", help="single-file mobile page (ladder helper + pro BP report/board)")
    s.add_argument("--matchup", action="append", metavar="US|THEM", help='repeatable, e.g. --matchup "Team Spirit|Team Liquid"')
    s.add_argument("--patch"); s.add_argument("--as-of", help="YYYY-MM-DD"); s.add_argument("--out", help="default h5/index.html")
    s.add_argument("--no-icons", action="store_true", help="skip hero icon download (offline); cached icons are still embedded"); s.set_defaults(f=cmd_h5)
    s = sp.add_parser("blindtest", help="replay real drafts after a snapshot's as_of; Top-k hit rates")
    s.add_argument("--snapshot", required=True); s.add_argument("--until"); s.add_argument("--patch"); s.add_argument("--league", type=int)
    s.add_argument("--max", type=int, default=150); s.add_argument("--out"); s.add_argument("--no-context", action="store_true", help="ablation: disable counter/synergy/gap terms"); s.set_defaults(f=cmd_blindtest)

    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):   # player names contain non-GBK glyphs; never crash on a Windows console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    a.f(a)


if __name__ == "__main__":
    sys.exit(main())
