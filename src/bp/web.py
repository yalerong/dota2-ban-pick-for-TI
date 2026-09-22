"""Dependency-free local web bridge for the authoritative BP recommender."""
from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import socket
from typing import Any, Callable, Mapping
import webbrowser

from .draft_state import DraftState
from .profiles import Frames, TeamProfile
from .recommend import candidates

log = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 64 * 1024


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def _server_class(host: str) -> type[ThreadingHTTPServer]:
    return _IPv6ThreadingHTTPServer if ":" in host else ThreadingHTTPServer


def _display_urls(host: str, port: int) -> tuple[str, str | None]:
    if host == "0.0.0.0":
        return f"http://127.0.0.1:{port}/", f"http://<this-pc-ip>:{port}/"
    if host == "::":
        return f"http://[::1]:{port}/", f"http://[<this-pc-ipv6>]:{port}/"
    display_host = f"[{host}]" if ":" in host else host
    return f"http://{display_host}:{port}/", None


def _team_for(relative_team: int, first: str) -> str:
    return "us" if (relative_team == 0) == (first == "us") else "them"


def draft_response(
    fr: Frames,
    fmt: tuple[tuple[int, int], ...],
    profiles: Mapping[int, TeamProfile],
    payload: Mapping[str, Any],
    adviser: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Replay browser actions, then score the next step with ``recommend.candidates``."""
    try:
        us_id, them_id = int(payload["us"]), int(payload["them"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("us and them must be known team ids") from exc
    if us_id == them_id:
        raise ValueError("us and them must be different teams")
    if us_id not in profiles or them_id not in profiles:
        raise ValueError("requested team is not included in this H5 pack")

    first = payload.get("first", "us")
    if first not in {"us", "them"}:
        raise ValueError("first must be 'us' or 'them'")
    actions = payload.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("actions must be a list of hero ids")
    try:
        hero_ids = [int(hero_id) for hero_id in actions]
    except (TypeError, ValueError) as exc:
        raise ValueError("actions must be a list of hero ids") from exc

    k = payload.get("k", fr.cfg["evidence"]["top_k"])
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 20:
        raise ValueError("k must be an integer from 1 to 20")
    strategy = payload.get("strategy", "")
    if not isinstance(strategy, str) or len(strategy) > 500:
        raise ValueError("strategy must be a string of at most 500 characters")
    strategy = strategy.strip()

    state = DraftState(fmt, frozenset(fr.heroes))
    for hero_id in hero_ids:
        state.apply(hero_id)

    board = {"us": {"picks": [], "bans": []}, "them": {"picks": [], "bans": []}}
    for action in state.actions:
        side = _team_for(action.team, first)
        board[side]["picks" if action.is_pick else "bans"].append(action.hero_id)

    next_action = None
    if not state.done:
        next_action = {
            "action": "pick" if state.next_is_pick else "ban",
            "team": _team_for(state.next_team, first),
        }
    # The web board records the opponent's real action. It only recommends when it is our turn;
    # predicted opponent actions remain evidence on our candidate cards, never automatic draft state.
    ranked = []
    if next_action and next_action["team"] == "us":
        first_profile, second_profile = (
            (profiles[us_id], profiles[them_id]) if first == "us" else (profiles[them_id], profiles[us_id])
        )
        ranked = candidates(fr, state, first_profile, second_profile, k=k)
    candidate_rows = [asdict(candidate) for candidate in ranked]
    ai_advice = None
    ai_error = None
    if adviser is not None and candidate_rows:
        named_board = {
            side: {kind: [fr.hero(hero_id) for hero_id in hero_ids] for kind, hero_ids in values.items()}
            for side, values in board.items()
        }
        context = {
            "us": profiles[us_id].name,
            "them": profiles[them_id].name,
            "first": first,
            "step": state.step,
            "total": len(fmt),
            "action": next_action["action"],
            "strategy": strategy,
            "state": named_board,
            "candidates": candidate_rows,
        }
        try:
            ai_advice = adviser(context)
        except Exception as exc:  # provider failures must never hide the deterministic recommendation
            log.warning("AI analysis unavailable: %s", exc)
            ai_error = "AI analysis unavailable; showing local recommendations."
    return {
        "step": state.step,
        "total": len(fmt),
        "done": state.done,
        "next": next_action,
        "state": board,
        "candidates": candidate_rows,
        "ai_advice": ai_advice,
        "ai_error": ai_error,
    }


def _handler(html: str, scorer: Callable[[Mapping[str, Any]], dict[str, Any]]):
    page = html.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, value: Mapping[str, Any]) -> None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            if self.path.split("?", 1)[0] != "/api/recommend":
                self._send_json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    raise ValueError("request body must be 1-65536 bytes")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                self._send_json(200, scorer(payload))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})

        def log_message(self, fmt: str, *args: Any) -> None:
            log.info("web %s - %s", self.address_string(), fmt % args)

    return Handler


def serve(
    html: str,
    scorer: Callable[[Mapping[str, Any]], dict[str, Any]],
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
) -> None:
    """Serve the H5 page and recommendation API until interrupted."""
    server = _server_class(host)((host, port), _handler(html, scorer))
    url, phone_url = _display_urls(host, server.server_port)
    print(f"BP web: {url} (Ctrl+C to stop)")
    if phone_url:
        print(f"Phone on the same Wi-Fi: {phone_url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
