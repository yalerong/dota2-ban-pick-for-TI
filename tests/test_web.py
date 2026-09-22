from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
import socket
import threading

import pandas as pd
import pytest

from bp.draft_formats import load_format
from bp.draft_state import DraftState
from bp.profiles import build_profile, load_frames, player_hero_stats
from bp.recommend import candidates
from bp.web import _display_urls, _handler, _server_class, ai_response, draft_response
from tests.test_phase2 import TEAM_A, TEAM_B


def _setup(league):
    fr = load_frames(league, patch="7.41")
    stats = player_hero_stats(fr)
    profiles = {
        TEAM_A: build_profile(fr, TEAM_A, stats),
        TEAM_B: build_profile(fr, TEAM_B, stats),
    }
    fmt = load_format(league, "7.41")
    return fr, profiles, fmt


def test_draft_response_returns_authoritative_candidates(league):
    fr, profiles, fmt = _setup(league)
    response = draft_response(fr, fmt, profiles, {"us": TEAM_A, "them": TEAM_B, "first": "us", "actions": []})
    expected = candidates(fr, DraftState(fmt, frozenset(fr.heroes)), profiles[TEAM_A], profiles[TEAM_B], k=5)

    assert response["step"] == 0
    assert response["total"] == len(fmt)
    assert response["done"] is False
    assert response["next"] == {"action": "ban", "team": "us"}
    assert response["candidates"][0]["hero_id"] == expected[0].hero_id
    assert set(response["candidates"][0]) == {
        "hero_id", "hero", "action", "score", "confidence", "samples", "evidence", "match_ids",
        "components", "predicted_response", "insufficient",
    }


def test_draft_response_rebases_when_opponent_acts_first(league):
    fr, profiles, fmt = _setup(league)
    response = draft_response(
        fr, fmt, profiles,
        {"us": TEAM_A, "them": TEAM_B, "first": "them", "actions": [60]},
    )
    state = DraftState(fmt, frozenset(fr.heroes))
    state.apply(60)
    expected = candidates(fr, state, profiles[TEAM_B], profiles[TEAM_A], k=5)

    assert response["next"] == {"action": "ban", "team": "us"}
    assert response["candidates"][0]["hero_id"] == expected[0].hero_id


def test_draft_response_replays_actions_before_scoring(league):
    fr, profiles, fmt = _setup(league)
    actions = [60, 61]
    response = draft_response(
        fr, fmt, profiles,
        {"us": TEAM_A, "them": TEAM_B, "first": "us", "actions": actions, "k": 3},
    )
    state = DraftState(fmt, frozenset(fr.heroes))
    for hero_id in actions:
        state.apply(hero_id)
    expected = candidates(fr, state, profiles[TEAM_A], profiles[TEAM_B], k=3)

    assert response["step"] == len(actions)
    assert response["next"] == {"action": "ban", "team": "us"}
    assert [row["hero_id"] for row in response["candidates"]] == [row.hero_id for row in expected]
    assert not set(actions) & {row["hero_id"] for row in response["candidates"]}


def test_draft_response_waits_for_recorded_opponent_action(league):
    fr, profiles, fmt = _setup(league)
    response = draft_response(
        fr, fmt, profiles,
        {"us": TEAM_A, "them": TEAM_B, "first": "us", "actions": [60]},
    )

    assert response["next"] == {"action": "ban", "team": "them"}
    assert response["candidates"] == []


def test_draft_response_adds_validated_ai_advice_without_replacing_candidates(league):
    fr, profiles, fmt = _setup(league)
    seen = {}

    def advise(context):
        seen.update(context)
        return {
            "recommended_hero": context["candidates"][1]["hero"],
            "reasons": ["matches the requested tempo"],
            "risks": ["thin sample"],
            "alternatives": [context["candidates"][0]["hero"]],
        }

    response = ai_response(
        fr, fmt, profiles,
        {"us": TEAM_A, "them": TEAM_B, "first": "us", "actions": [], "strategy": "play fast"},
        advise,
    )

    assert "candidates" not in response
    assert response["ai_advice"]["recommended_hero"] == seen["candidates"][1]["hero"]
    assert seen["strategy"] == "play fast"
    assert seen["us"] == profiles[TEAM_A].name
    assert seen["them"] == profiles[TEAM_B].name


def test_draft_response_falls_back_when_ai_fails(league):
    fr, profiles, fmt = _setup(league)

    def fail(_context):
        raise RuntimeError("provider unavailable")

    response = ai_response(
        fr, fmt, profiles,
        {"us": TEAM_A, "them": TEAM_B, "first": "us", "actions": []},
        fail,
    )

    assert response["ai_advice"] is None
    assert response["ai_error"] == "AI analysis unavailable; local recommendations remain active."


def test_draft_response_limits_strategy_text(league):
    fr, profiles, fmt = _setup(league)

    with pytest.raises(ValueError, match="strategy"):
        ai_response(
            fr, fmt, profiles,
            {"us": TEAM_A, "them": TEAM_B, "first": "us", "actions": [], "strategy": "x" * 501},
            lambda context: context,
        )


@pytest.mark.parametrize("actions, message", [([60, 60], "already picked/banned"), ([999], "unknown hero")])
def test_draft_response_rejects_illegal_actions(league, actions, message):
    fr, profiles, fmt = _setup(league)
    with pytest.raises(ValueError, match=message):
        draft_response(fr, fmt, profiles, {"us": TEAM_A, "them": TEAM_B, "first": "us", "actions": actions})


def test_draft_response_returns_complete_state_without_candidates(league):
    fr, profiles, fmt = _setup(league)
    response = draft_response(
        fr, fmt, profiles,
        {"us": TEAM_A, "them": TEAM_B, "first": "us", "actions": list(range(1, len(fmt) + 1))},
    )

    assert response["done"] is True
    assert response["next"] is None
    assert response["candidates"] == []
    assert len(response["state"]["us"]["picks"]) == 5
    assert len(response["state"]["them"]["picks"]) == 5
    assert len(response["state"]["us"]["bans"]) == 7
    assert len(response["state"]["them"]["bans"]) == 7


def test_team_profile_signature_cache_is_published_atomically(league, monkeypatch):
    fr, profiles, _ = _setup(league)
    profile = profiles[TEAM_A]
    hero_id = int(profile.stats.iloc[0].hero_id)
    expected = float(profile.stats[profile.stats.hero_id == hero_id].signature.max())
    started, release = threading.Event(), threading.Event()
    call_lock = threading.Lock()
    calls = 0
    original = pd.DataFrame.sort_values

    def delayed_first_call(frame, *args, **kwargs):
        nonlocal calls
        with call_lock:
            calls += 1
            call = calls
        if call == 1:
            started.set()
            assert release.wait(2)
        return original(frame, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "sort_values", delayed_first_call)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(profile.sig, hero_id)
        assert started.wait(2)
        second = executor.submit(profile.sig, hero_id)
        second_result = second.result(timeout=2)
        release.set()
        first_result = first.result(timeout=2)

    assert first_result[0] == pytest.approx(expected)
    assert second_result[0] == pytest.approx(expected)


def test_server_class_and_display_urls_cover_wildcard_and_ipv6_binds():
    assert _server_class("127.0.0.1").address_family == socket.AF_INET
    assert _server_class("::1").address_family == socket.AF_INET6
    assert _display_urls("0.0.0.0", 8765) == (
        "http://127.0.0.1:8765/",
        "http://<this-pc-ip>:8765/",
    )
    assert _display_urls("::", 8765) == (
        "http://[::1]:8765/",
        "http://[<this-pc-ipv6>]:8765/",
    )


def test_ai_endpoint_requires_the_per_run_token():
    local_calls = []
    ai_calls = []
    handler = _handler(
        "<html></html>",
        lambda payload: local_calls.append(payload) or {"kind": "local"},
        lambda payload: ai_calls.append(payload) or {"kind": "ai"},
        "session-token",
    )
    server = _server_class("127.0.0.1")(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(path, token=None):
        body = json.dumps({"value": 1})
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body.encode()))}
        if token is not None:
            headers["X-BP-Token"] = token
        con = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            con.request("POST", path, body=body, headers=headers)
            response = con.getresponse()
            return response.status, json.loads(response.read())
        finally:
            con.close()

    try:
        assert post("/api/recommend") == (200, {"kind": "local"})
        assert post("/api/ai") == (403, {"error": "forbidden"})
        assert post("/api/ai", "wrong") == (403, {"error": "forbidden"})
        assert post("/api/ai", "session-token") == (200, {"kind": "ai"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(local_calls) == 1
    assert len(ai_calls) == 1


def test_slow_ai_endpoint_does_not_block_local_recommendations():
    started, release = threading.Event(), threading.Event()

    def slow_ai(_payload):
        started.set()
        assert release.wait(2)
        return {"kind": "ai"}

    server = _server_class("127.0.0.1")((
        "127.0.0.1", 0,
    ), _handler("<html></html>", lambda payload: {"kind": "local"}, slow_ai, "session-token"))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def post(path, token=None):
        body = "{}"
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        if token:
            headers["X-BP-Token"] = token
        con = HTTPConnection("127.0.0.1", server.server_port, timeout=1)
        try:
            con.request("POST", path, body=body, headers=headers)
            response = con.getresponse()
            return response.status, json.loads(response.read())
        finally:
            con.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending_ai = executor.submit(post, "/api/ai", "session-token")
            assert started.wait(1)
            assert post("/api/recommend") == (200, {"kind": "local"})
            release.set()
            assert pending_ai.result(timeout=1) == (200, {"kind": "ai"})
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
