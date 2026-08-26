"""Client tests with a fake HTTP session (no network)."""
import json

from bp.opendota import OpenDota


class FakeResp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/matches/123"):
            return FakeResp(200, {"match_id": 123, "picks_bans": []})
        if url.endswith("/proMatches"):
            return FakeResp(200, [{"match_id": 5}])
        return FakeResp(404, {"error": "not found"})


def test_match_url_and_cache(tmp_path):
    c = OpenDota(api_key="", raw_dir=tmp_path, per_min=100000)
    c.session = FakeSession()
    assert c.match(123)["match_id"] == 123
    assert c.session.calls[-1][0].endswith("/api/matches/123")
    assert c.has_match(123)
    # second call served from cache, no HTTP
    n = len(c.session.calls)
    assert c.match(123)["match_id"] == 123 and len(c.session.calls) == n
    # 404 -> None and not cached
    assert c.match(999) is None and not c.has_match(999)
    # bounded proMatches page is cached, first page is not
    c.pro_matches_page(None); c.pro_matches_page(None)
    c.pro_matches_page(777); c.pro_matches_page(777)
    urls = [u for u, p in c.session.calls]
    assert sum(u.endswith("/proMatches") for u in urls) == 3
    assert c.session.calls[-1][1] == {"less_than_match_id": 777}
    # offline mode never touches the network but still serves cache
    off = OpenDota(api_key="", raw_dir=tmp_path, offline=True)
    assert off.read_cache("/matches", "123")["match_id"] == 123
