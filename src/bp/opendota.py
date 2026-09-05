"""OpenDota client: rate limit, retry, raw-response cache (P1-02)."""
from __future__ import annotations
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from .config import CONFIG

log = logging.getLogger(__name__)
BASE = "https://api.opendota.com/api"
SCHEMA_VERSION = 1
FREE_PER_MIN = 45   # OpenDota allows 60/min; 50 still tripped 429 once on proMatches paging
FREE_PER_DAY = 3000  # docs say 2000, X-Rate-Limit-Remaining-Day header says 3000; header wins at runtime


class DailyBudgetExceeded(RuntimeError):
    pass


def _redact(e: BaseException) -> str:
    """requests error strings carry the full URL, i.e. `?api_key=...`; never let the key reach a log file."""
    return re.sub(r"api_key=[^&\s'\"]+", "api_key=***", str(e))


class OpenDota:
    def __init__(self, api_key: str | None = None, raw_dir: Path | None = None,
                 per_min: int = FREE_PER_MIN, per_day: int = FREE_PER_DAY, offline: bool = False):
        self.api_key = api_key if api_key is not None else CONFIG.api_key
        self.raw_dir = raw_dir or CONFIG.raw_dir
        self.min_interval = 60.0 / per_min
        self.per_day = per_day if not self.api_key else 10**9
        self.offline = offline
        self._last = 0.0
        self._budget_file = self.raw_dir / "_budget.json"
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "dota2-bp/0.1"

    # ---- daily budget -------------------------------------------------
    def _budget(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._budget_file.exists():
            b = json.loads(self._budget_file.read_text())
            if b.get("day") == today:
                return b
        return {"day": today, "used": 0}

    def _spend(self) -> None:
        b = self._budget()
        if b["used"] >= self.per_day:
            raise DailyBudgetExceeded(f"daily budget {self.per_day} used up ({b['day']})")
        b["used"] += 1
        self._budget_file.write_text(json.dumps(b))

    def budget_left(self) -> int:
        return max(0, self.per_day - self._budget()["used"])

    def _sync_budget_from_header(self, remaining: int) -> None:
        """Trust OpenDota's own X-Rate-Limit-Remaining-Day over the local counter."""
        b = self._budget()
        b["used"] = max(b["used"], self.per_day - remaining) if self.per_day < 10**9 else b["used"]
        b["header_remaining"] = remaining
        self._budget_file.write_text(json.dumps(b))

    def _mark_exhausted(self) -> None:
        b = self._budget()
        b["used"] = self.per_day
        b["header_remaining"] = 0
        self._budget_file.write_text(json.dumps(b))

    # ---- raw cache ----------------------------------------------------
    def _cache_path(self, endpoint: str, key: str) -> Path:
        p = self.raw_dir / endpoint.strip("/").replace("/", "_")
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{key}.json"

    def read_cache(self, endpoint: str, key: str) -> Optional[Any]:
        p = self._cache_path(endpoint, key)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))["payload"]
        return None

    def _write_cache(self, endpoint: str, key: str, payload: Any) -> None:
        env = {"source": "opendota", "endpoint": endpoint, "key": key,
               "fetched_at": datetime.now(timezone.utc).isoformat(),
               "schema_version": SCHEMA_VERSION, "payload": payload}
        self._cache_path(endpoint, key).write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")

    # ---- http ---------------------------------------------------------
    def _get(self, endpoint: str, params: dict | None = None, retries: int = 5) -> Any:
        if self.offline:
            raise RuntimeError("offline mode: network disabled")
        params = dict(params or {})
        if self.api_key:
            params["api_key"] = self.api_key
        for attempt in range(retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._spend()
            self._last = time.monotonic()
            try:
                r = self.session.get(BASE + endpoint, params=params, timeout=60)
            except requests.RequestException as e:
                log.warning("net error %s (%s), retry %d", endpoint, _redact(e), attempt)
                time.sleep(2 ** attempt)
                continue
            day_left = r.headers.get("X-Rate-Limit-Remaining-Day")
            if day_left is not None:
                self._sync_budget_from_header(int(day_left))
            if r.status_code == 200:
                if day_left is not None and int(day_left) <= 3:
                    self._mark_exhausted()
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                if day_left is not None and int(day_left) <= 0:
                    self._mark_exhausted()
                    raise DailyBudgetExceeded("OpenDota daily limit reached (header)")
                log.warning("%s -> 429 minute limit, sleeping 30s", endpoint)
                time.sleep(30)
                continue
            if r.status_code >= 500:
                log.warning("%s -> %s, backoff", endpoint, r.status_code)
                time.sleep(2 ** attempt * 2)
                continue
            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                raise RuntimeError(_redact(e)) from None
        raise RuntimeError(f"gave up on {endpoint} after {retries} tries")

    def get_cached(self, endpoint: str, key: str, params: dict | None = None, path: str | None = None) -> Any:
        """Return cached payload if present; otherwise fetch `path` (default: endpoint), cache, return."""
        c = self.read_cache(endpoint, key)
        if c is not None:
            return c
        payload = self._get(path or endpoint, params)
        if payload is not None:
            self._write_cache(endpoint, key, payload)
        return payload

    # ---- endpoints ----------------------------------------------------
    def pro_matches_page(self, less_than_match_id: int | None = None) -> list[dict]:
        """One page (100 rows) of /proMatches. Bounded pages are immutable -> cached."""
        if less_than_match_id is None:
            return self._get("/proMatches") or []
        return self.get_cached("/proMatches", str(less_than_match_id),
                               {"less_than_match_id": less_than_match_id}) or []

    def public_matches_page(self, less_than_match_id: int | None = None, min_rank: int | None = None,
                            max_rank: int | None = None) -> list[dict]:
        """One page (100 rows) of /publicMatches, newest first. Not cached: the unbounded page changes every minute
        and bounded pages are consumed exactly once by scripts/pull_public.py, which keeps its own cursor."""
        params: dict = {}
        if less_than_match_id is not None:
            params["less_than_match_id"] = int(less_than_match_id)
        if min_rank is not None:
            params["min_rank"] = int(min_rank)
        if max_rank is not None:
            params["max_rank"] = int(max_rank)
        return self._get("/publicMatches", params) or []

    def match(self, match_id: int) -> dict | None:
        return self.get_cached("/matches", str(match_id), path=f"/matches/{int(match_id)}")

    def has_match(self, match_id: int) -> bool:
        return self._cache_path("/matches", str(match_id)).exists()
