import anthropic
import pytest

from app.agents.openai_compatible_client import OpenAICompatibleClient
from app.agents.runtime import DEFAULT_GROQ_MODEL, GROQ_BASE_URL, MODEL, _client_and_model
from app.core.config import settings


def test_defaults_to_anthropic(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "ai_model", "")
    client, model = _client_and_model()
    assert isinstance(client, anthropic.AsyncAnthropic)
    assert model == MODEL


def test_groq_uses_groq_base_url_and_default_model(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "groq")
    monkeypatch.setattr(settings, "ai_model", "")
    monkeypatch.setattr(settings, "ai_api_key", "gsk_test")
    client, model = _client_and_model()
    assert isinstance(client, OpenAICompatibleClient)
    assert model == DEFAULT_GROQ_MODEL
    assert client.messages._base_url == GROQ_BASE_URL


def test_lmstudio_uses_configured_base_url_and_requires_a_model(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "lmstudio")
    monkeypatch.setattr(settings, "ai_base_url", "http://172.16.0.2:1234/v1")
    monkeypatch.setattr(settings, "ai_model", "google/gemma-4-e4b")
    client, model = _client_and_model()
    assert isinstance(client, OpenAICompatibleClient)
    assert model == "google/gemma-4-e4b"
    assert client.messages._base_url == "http://172.16.0.2:1234/v1"


def test_lmstudio_without_a_model_id_fails_fast(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "lmstudio")
    monkeypatch.setattr(settings, "ai_model", "")
    with pytest.raises(ValueError):
        _client_and_model()
