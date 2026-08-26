"""Infer the Captain's Mode action sequence per patch from data (P1-06). Never hand-written."""
from __future__ import annotations
import json
import logging
import sqlite3
from collections import Counter, defaultdict

from .normalize import relative_signature

log = logging.getLogger(__name__)


def _signatures_by_patch(con: sqlite3.Connection) -> dict[str, Counter]:
    rows = con.execute(
        """SELECT m.match_id, m.patch, d.order_no, d.team_side, d.is_pick, d.hero_id
           FROM matches m JOIN draft_events d ON d.match_id = m.match_id
           ORDER BY m.match_id, d.order_no""").fetchall()
    per_match: dict[int, list] = defaultdict(list)
    patch_of: dict[int, str] = {}
    for r in rows:
        per_match[r["match_id"]].append((r["order_no"], r["team_side"], r["is_pick"], r["hero_id"]))
        patch_of[r["match_id"]] = r["patch"]
    sigs: dict[str, Counter] = defaultdict(Counter)
    for mid, seq in per_match.items():
        sigs[patch_of[mid] or "unknown"][relative_signature(seq)] += 1
    return sigs


def infer_formats(con: sqlite3.Connection, min_support: int = 5) -> list[dict]:
    sigs = _signatures_by_patch(con)
    out = []
    for patch, counter in sorted(sigs.items()):
        total = sum(counter.values())
        sig, support = counter.most_common(1)[0]
        share = support / total
        rec = {"patch": patch, "n_actions": len(sig), "support": support, "total": total, "share": round(share, 4)}
        if support < min_support:
            log.warning("patch %s: only %d matches, format not trusted", patch, total)
            rec["trusted"] = False
        else:
            rec["trusted"] = True
            con.execute("INSERT OR REPLACE INTO draft_formats (patch, seq_json, n_actions, support, total, share) VALUES (?,?,?,?,?,?)",
                        (patch, json.dumps([list(s) for s in sig]), len(sig), support, total, share))
        out.append(rec)
    con.commit()
    return out


def load_format(con: sqlite3.Connection, patch: str) -> tuple[tuple[int, int], ...] | None:
    r = con.execute("SELECT seq_json FROM draft_formats WHERE patch=?", (patch,)).fetchone()
    return tuple(tuple(x) for x in json.loads(r[0])) if r else None


def describe(sig) -> str:
    """Human-readable: B/P letters, uppercase = first-acting team."""
    return "".join(("P" if p else "B") if t == 0 else ("p" if p else "b") for p, t in sig)
