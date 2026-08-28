"""Mobile (H5) single-file page: ladder draft helper + pro-match BP (report viewer and a lite Captain's Mode board).

Everything the page needs is embedded as JSON, so the file works from file:// or a plain `python -m http.server`.
The in-page scoring is a deliberately *simplified* client-side mirror of recommend.py (context terms + signature /
meta rates, no habit / response terms); the authoritative numbers stay in `bp report` / `bp draft`.
"""
from __future__ import annotations
import base64
import html
import json
import logging
import re
from pathlib import Path

import pandas as pd

from .config import CONFIG
from .profiles import Frames, TeamProfile

log = logging.getLogger(__name__)
TEMPLATE = Path(__file__).with_name("h5_template.html")
MIN_PAIR_N = 2          # pairwise effects with fewer raw games are noise and bloat the page
ICON_URL = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/icons/{short}.png"   # ~5 KB each


def hero_icons(heroes: list[dict], cache_dir: Path | None = None, download: bool = True) -> dict[int, str]:
    """hero_id -> data URI of the official mini icon. Downloaded once into data/raw/hero_icons/, then served from cache;
    a hero whose icon cannot be fetched simply has no image (the page falls back to its name)."""
    cache = cache_dir or CONFIG.data_dir / "raw" / "hero_icons"
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for h in heroes:
        short = (h.get("npc") or "").removeprefix("npc_dota_hero_")
        if not short:
            continue
        f = cache / f"{short}.png"
        if not f.exists() and download:
            try:
                import requests
                r = requests.get(ICON_URL.format(short=short), timeout=8)
                if r.ok and r.headers.get("content-type", "").startswith("image/"):
                    f.write_bytes(r.content)
                else:
                    log.warning("icon %s: HTTP %s", short, r.status_code)
            except Exception as e:  # offline: give up on the rest instead of waiting out 127 timeouts
                log.warning("icon %s: %s -- skipping remaining downloads (names only)", short, e)
                download = False
        if f.exists():
            out[h["id"]] = "data:image/png;base64," + base64.b64encode(f.read_bytes()).decode()
    return out


def hero_zh_names(path: Path | None = None) -> dict[str, list[str]]:
    """short npc name -> [display name, aliases...] from config/hero_zh.yaml (missing file -> English only)."""
    import yaml
    f = path or CONFIG.root / "config" / "hero_zh.yaml"
    if not f.exists():
        return {}
    return {k: [str(x) for x in (v or [])] for k, v in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).items()}


# ---------------------------------------------------------------- payloads
def ladder_payload(fr: Frames, con, fmt, download_icons: bool = True) -> dict:
    """Hero list, global WR, per-position pick share, counter/synergy pairs, CM sequence."""
    rows = con.execute("SELECT hero_id, localized_name, primary_attr, roles, name FROM heroes ORDER BY localized_name").fetchall()
    heroes = [{"id": int(h), "name": n, "attr": a or "", "roles": json.loads(r or "[]"), "npc": npc} for h, n, a, r, npc in rows]
    icons = hero_icons(heroes, download=download_icons)
    zh = hero_zh_names()
    for h in heroes:
        h["icon"] = icons.get(h["id"], "")
        h["zh"] = zh.get(h["npc"].removeprefix("npc_dota_hero_"), [])
        del h["npc"]
    r = fr.roster
    tot_w = float(r.w.sum()) or 1.0
    pos_w = r.groupby("pos").w.sum()
    hero_pos = r.groupby(["hero_id", "pos"]).w.sum()
    ctx = fr.context()
    stats = {}
    for h in heroes:
        hid = h["id"]
        share = [float(hero_pos.get((hid, p), 0.0) / max(float(pos_w.get(p, 1e-9)), 1e-9)) for p in range(1, 6)]
        stats[hid] = {"wr": round(float(ctx.hero_wr.get(hid, 0.5)), 3),
                      "n": int((r.hero_id == hid).sum()),
                      "rate": round(float(r[r.hero_id == hid].w.sum() / tot_w * 10), 4),   # share of all pro games (10 slots)
                      "pos": [round(x, 4) for x in share]}
    counter = [[int(a), int(b), round(float(e), 4), int(n)] for (a, b), (e, n) in ctx.counter.items() if n >= MIN_PAIR_N]
    synergy = [[int(a), int(b), round(float(e), 4), int(n)] for (a, b), (e, n) in ctx.synergy.items() if n >= MIN_PAIR_N]
    return {"heroes": heroes, "stats": stats, "counter": counter, "synergy": synergy,
            "targets": ctx.targets, "cm_seq": [[int(p), int(t)] for p, t in (fmt or ())],
            "weights": {"context": dict(fr.cfg.get("context", {})), "pick": dict(fr.cfg["pick"]), "ban": dict(fr.cfg["ban"])}}


def _team_payload(fr: Frames, P: TeamProfile, top_n: int = 8) -> dict:
    roster = []
    for acct in P.roster:
        s = P.stats[P.stats.account_id == acct].sort_values("signature", ascending=False)
        if not len(s):
            roster.append({"name": fr.player(acct), "pos": 0, "heroes": []}); continue
        roster.append({"name": fr.player(acct), "pos": int(s.pos.mode().iloc[0]), "games": int(s.games.sum()),
                       "heroes": [{"id": int(x.hero_id), "sig": round(float(x.signature), 3), "tag": x.tag, "g": int(x.games),
                                   "w": int(x.wins), "wr": round(float(x.wr), 3), "ban": round(float(x.ban_rate_vs_team), 3)}
                                  for x in s.head(top_n).itertuples()]})
    # hero -> best signature across the roster (what the lite board scores with)
    sig = P.stats.groupby("hero_id").signature.max() if len(P.stats) else pd.Series(dtype=float)
    hs = P.hero_stats.head(20)
    return {"id": int(P.team_id), "name": P.name, "games": int(P.games), "roster": roster,
            "sig": {int(k): round(float(v), 3) for k, v in sig.items() if v > 0},
            "bans": [{"id": int(x.hero_id), "rate": round(float(x.phase0_rate), 3), "meta": round(float(x.global_phase0_rate), 3),
                      "lift": round(float(x.targeted_lift), 3)} for x in P.ban_pressure.head(10).itertuples()],
            "picks": [{"id": int(x.hero_id), "n": int(x.picks), "rate": round(float(x.pick_rate), 3), "wr": round(float(x.wr), 3),
                       "lift": round(float(x.win_lift), 3)} for x in hs.itertuples()]}


def matchup_payload(fr: Frames, us: TeamProfile, them: TeamProfile, report_md: str) -> dict:
    return {"title": f"{us.name} vs {them.name}", "us": _team_payload(fr, us), "them": _team_payload(fr, them),
            "report_html": md_to_html(report_md)}


# ---------------------------------------------------------------- markdown -> html (the subset report.py emits)
# italic only for a whole-line _..._ (report.py's "_no roster found_"); an inner-word regex would eat text between
# underscored player names such as not_me ... some_name
_INLINE = [(re.compile(r"\*\*(.+?)\*\*"), r"<b>\1</b>"), (re.compile(r"^_(.+)_$"), r"<i>\1</i>")]


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    for rx, rep in _INLINE:
        s = rx.sub(rep, s)
    # match ids -> OpenDota links (8-10 digit numbers)
    return re.sub(r"\b(\d{8,10})\b", r'<a href="https://www.opendota.com/matches/\1" target="_blank">\1</a>', s)


def md_to_html(md: str) -> str:
    """Headings (h1-h3), nested '-' lists (2-space indent), '---', paragraphs. h2/h3 become collapsible sections."""
    out, stack, open_sec = [], [], 0

    def close_lists(to: int = 0):
        while len(stack) > to:
            out.append("</ul>"); stack.pop()

    for line in md.splitlines():
        if not line.strip():
            close_lists(); continue
        m = re.match(r"^(#{1,3}) (.*)$", line)
        if m:
            close_lists()
            lvl = len(m.group(1))
            if lvl == 1:
                out.append(f"<h1>{_inline(m.group(2))}</h1>"); continue
            out.append("</div></details>" * open_sec); open_sec = 0
            out.append(f'<details class="sec l{lvl}"{" open" if lvl == 2 else ""}><summary>{_inline(m.group(2))}</summary><div>'); open_sec = 1
            continue
        if line.strip() == "---":
            close_lists(); out.append("<hr>"); continue
        m = re.match(r"^(\s*)- (.*)$", line)
        if m:
            depth = len(m.group(1)) // 2 + 1
            while len(stack) < depth:
                out.append("<ul>"); stack.append(depth)
            close_lists(depth)
            out.append(f"<li>{_inline(m.group(2))}</li>"); continue
        close_lists(); out.append(f"<p>{_inline(line)}</p>")
    close_lists()
    out.append("</div></details>" * open_sec)
    return "\n".join(out)


# ---------------------------------------------------------------- render
def render(ladder: dict, matchups: list[dict], meta: dict, teams: list[dict] | None = None) -> str:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    by_id = {int(t["id"]): t for t in (teams or [])}
    for matchup in matchups:
        for side in ("us", "them"):
            team = matchup[side]
            by_id[int(team["id"])] = team
    data = json.dumps({"ladder": ladder, "matchups": matchups, "teams": list(by_id.values()), "meta": meta},
                      ensure_ascii=False, separators=(",", ":"))
    # keep the JSON safe inside <script>
    data = data.replace("</", "<\\/")
    return tpl.replace("__DATA__", data)
