"""Test-wide fixtures: the pickle cache must never touch the repo's data/ directory or leak between tests."""
import pytest

from bp import cache
from tests.test_phase2 import league  # noqa: F401  (shared synthetic-league fixture)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "ENABLED", True)
