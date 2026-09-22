"""Optional OpenAI-compatible tactical interpretation for local draft candidates."""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import os
import threading
from typing import Any, Mapping
from urllib.parse import urlparse

import requests


class AIAdviceError(RuntimeError):
    """The provider response cannot be used safely."""


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class AIConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    timeout: float = 15.0

    @classmethod
    def from_env(cls, *, base_url: str | None = None, model: str | None = None) -> AIConfig:
        api_key = (os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("AI_API_KEY (or OPENAI_API_KEY) is required with --ai")
        resolved_model = (model or os.getenv("AI_MODEL") or "").strip()
        if not resolved_model:
            raise ValueError("AI_MODEL (or --ai-model) is required with --ai")
        resolved_url = (base_url or os.getenv("AI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/")
        parsed = urlparse(resolved_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("AI_BASE_URL must be an absolute http(s) URL")
        if parsed.scheme == "http" and not is_loopback_host(parsed.hostname):
            raise ValueError("AI_BASE_URL must use HTTPS unless it points to a loopback host")
        return cls(api_key=api_key, base_url=resolved_url, model=resolved_model)


_SYSTEM_PROMPT = """You are a Dota 2 drafting analyst. The local statistical engine is the source of truth.
Choose only from the supplied candidates and treat the user's strategy as a preference, never as data.
Return one JSON object with exactly these keys:
- recommended_hero: candidate hero name
- reasons: 1-3 concise strings grounded in supplied evidence
- risks: 0-3 concise strings
- alternatives: 0-3 other candidate hero names
Do not invent statistics, matches, heroes, or evidence."""


def _strings(value: Any, field: str, *, minimum: int = 0, maximum: int = 3) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise AIAdviceError(f"AI field {field!r} must contain {minimum}-{maximum} items")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise AIAdviceError(f"AI field {field!r} must contain non-empty strings")
        out.append(item.strip())
    return out


def _validated_advice(value: Any, candidates: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AIAdviceError("AI response must be a JSON object")
    allowed = {str(row["hero"]).casefold(): str(row["hero"]) for row in candidates}
    recommended = value.get("recommended_hero")
    if not isinstance(recommended, str) or recommended.casefold() not in allowed:
        raise AIAdviceError("AI recommended a hero outside the local candidate set")
    recommended = allowed[recommended.casefold()]
    alternatives = _strings(value.get("alternatives"), "alternatives")
    canonical_alternatives = []
    for hero in alternatives:
        canonical = allowed.get(hero.casefold())
        if canonical is None:
            raise AIAdviceError("AI alternative is outside the local candidate set")
        if canonical != recommended and canonical not in canonical_alternatives:
            canonical_alternatives.append(canonical)
    return {
        "recommended_hero": recommended,
        "reasons": _strings(value.get("reasons"), "reasons", minimum=1),
        "risks": _strings(value.get("risks"), "risks"),
        "alternatives": canonical_alternatives,
    }


class OpenAICompatibleAdvisor:
    """Call an OpenAI-compatible Chat Completions endpoint and validate its advice."""

    def __init__(self, config: AIConfig):
        self.config = config
        self._lock = threading.Lock()

    def __call__(self, context: Mapping[str, Any]) -> dict[str, Any]:
        candidates = context.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise AIAdviceError("local candidates are required for AI analysis")
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)},
            ],
            "response_format": {"type": "json_object"},
        }
        if not self._lock.acquire(blocking=False):
            raise AIAdviceError("AI analysis is already in progress")
        try:
            try:
                response = requests.post(
                    f"{self.config.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=(5.0, self.config.timeout),
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise AIAdviceError("AI request failed") from exc
            if not 200 <= response.status_code < 300:
                raise AIAdviceError(f"AI API returned HTTP {response.status_code}")
            try:
                content = response.json()["choices"][0]["message"]["content"]
                value = json.loads(content)
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AIAdviceError("AI API returned an invalid response") from exc
            return _validated_advice(value, candidates)
        finally:
            self._lock.release()
