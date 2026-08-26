"""SQLite schema (P1-07) and helpers."""
from __future__ import annotations
import sqlite3
from pathlib import Path

from .config import CONFIG

SCHEMA = """
CREATE TABLE IF NOT EXISTS match_index (
  match_id INTEGER PRIMARY KEY, start_time INTEGER, duration INTEGER,
  leagueid INTEGER, league_name TEXT, series_id INTEGER, series_type INTEGER,
  radiant_team_id INTEGER, dire_team_id INTEGER, radiant_name TEXT, dire_name TEXT,
  radiant_win INTEGER, detail_status TEXT
);
CREATE TABLE IF NOT EXISTS heroes (
  hero_id INTEGER PRIMARY KEY, name TEXT, localized_name TEXT, primary_attr TEXT, roles TEXT
);
CREATE TABLE IF NOT EXISTS patches (
  patch_id INTEGER PRIMARY KEY, name TEXT, release_time INTEGER
);
CREATE TABLE IF NOT EXISTS draft_formats (
  patch TEXT PRIMARY KEY, seq_json TEXT, n_actions INTEGER, support INTEGER, total INTEGER, share REAL
);
CREATE TABLE IF NOT EXISTS matches (
  match_id INTEGER PRIMARY KEY, patch TEXT, patch_id INTEGER, leagueid INTEGER, league_name TEXT,
  start_time INTEGER, duration INTEGER, radiant_team_id INTEGER, dire_team_id INTEGER,
  radiant_win INTEGER, series_id INTEGER, series_type INTEGER, has_draft_timings INTEGER,
  quality_flags TEXT DEFAULT '[]', excluded INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_matches_time ON matches(start_time);
CREATE INDEX IF NOT EXISTS ix_matches_teams ON matches(radiant_team_id, dire_team_id);
CREATE TABLE IF NOT EXISTS draft_events (
  match_id INTEGER, order_no INTEGER, team_side INTEGER, is_pick INTEGER, hero_id INTEGER, phase INTEGER,
  PRIMARY KEY (match_id, order_no)
);
CREATE TABLE IF NOT EXISTS players (
  account_id INTEGER PRIMARY KEY, name TEXT, last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS teams (
  team_id INTEGER PRIMARY KEY, name TEXT, names_json TEXT, last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS roster_snapshots (
  match_id INTEGER, team_id INTEGER, account_id INTEGER, player_slot INTEGER, side INTEGER,
  hero_id INTEGER, lane_role INTEGER, gpm INTEGER, position_est INTEGER,
  PRIMARY KEY (match_id, player_slot)
);
CREATE INDEX IF NOT EXISTS ix_roster_acct ON roster_snapshots(account_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

TABLES = ["match_index", "heroes", "patches", "draft_formats", "matches", "draft_events",
          "players", "teams", "roster_snapshots"]


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or CONFIG.db_path
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
