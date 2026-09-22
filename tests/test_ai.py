from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from bp.ai import AIAdviceError, AIConfig, OpenAICompatibleAdvisor


def _context():
    return {
        "action": "pick",
        "strategy": "需要稳定先手",
        "candidates": [
            {"hero": "Axe", "hero_id": 2, "score": 1.2, "evidence": ["strong initiation"]},
            {"hero": "Mars", "hero_id": 129, "score": 1.1, "evidence": ["teamfight control"]},
        ],
    }


def test_ai_config_reads_generic_environment(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "secret")
    monkeypatch.setenv("AI_BASE_URL", "https://llm.example/v1/")
    monkeypatch.setenv("AI_MODEL", "example-model")

    cfg = AIConfig.from_env()

    assert cfg.api_key == "secret"
    assert cfg.base_url == "https://llm.example/v1"
    assert cfg.model == "example-model"
    assert "secret" not in repr(cfg)


def test_ai_config_requires_key_and_model(monkeypatch):
    for name in ("AI_API_KEY", "OPENAI_API_KEY", "AI_MODEL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="AI_API_KEY"):
        AIConfig.from_env()

    monkeypatch.setenv("AI_API_KEY", "secret")
    with pytest.raises(ValueError, match="AI_MODEL"):
        AIConfig.from_env()


def test_ai_config_rejects_plain_http_except_for_loopback(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "secret")
    monkeypatch.setenv("AI_MODEL", "example-model")

    with pytest.raises(ValueError, match="HTTPS"):
        AIConfig.from_env(base_url="http://llm.example/v1")

    assert AIConfig.from_env(base_url="http://127.0.0.1:11434/v1").base_url == "http://127.0.0.1:11434/v1"


def test_advisor_requests_json_and_validates_candidate_names(monkeypatch):
    seen = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "recommended_hero": "Mars",
                            "reasons": ["更符合稳定先手要求"],
                            "risks": ["对线强度依赖阵容"],
                            "alternatives": ["Axe"],
                        })
                    }
                }]
            }

    def fake_post(url, *, headers, json, timeout, allow_redirects):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout, allow_redirects=allow_redirects)
        return Response()

    monkeypatch.setattr("bp.ai.requests.post", fake_post)
    advisor = OpenAICompatibleAdvisor(AIConfig("secret", "https://llm.example/v1", "example-model"))

    result = advisor(_context())

    assert result["recommended_hero"] == "Mars"
    assert result["alternatives"] == ["Axe"]
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer secret"
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert seen["payload"]["model"] == "example-model"
    assert "temperature" not in seen["payload"]
    assert seen["timeout"] == (5.0, 15.0)
    assert seen["allow_redirects"] is False
    assert "secret" not in json.dumps(seen["payload"])


def test_advisor_rejects_hero_outside_local_candidates(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": json.dumps({
                "recommended_hero": "Pudge",
                "reasons": ["surprise"],
                "risks": [],
                "alternatives": [],
            })}}]}

    monkeypatch.setattr("bp.ai.requests.post", lambda *args, **kwargs: Response())
    advisor = OpenAICompatibleAdvisor(AIConfig("secret", "https://llm.example/v1", "example-model"))

    with pytest.raises(AIAdviceError, match="local candidate"):
        advisor(_context())


def test_advisor_rejects_overlapping_paid_calls(monkeypatch):
    started, release = threading.Event(), threading.Event()

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": json.dumps({
                "recommended_hero": "Axe",
                "reasons": ["local evidence"],
                "risks": [],
                "alternatives": ["Mars"],
            })}}]}

    def slow_post(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return Response()

    monkeypatch.setattr("bp.ai.requests.post", slow_post)
    advisor = OpenAICompatibleAdvisor(AIConfig("secret", "https://llm.example/v1", "example-model"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(advisor, _context())
        assert started.wait(2)
        with pytest.raises(AIAdviceError, match="already in progress"):
            advisor(_context())
        release.set()
        assert first.result(timeout=2)["recommended_hero"] == "Axe"
