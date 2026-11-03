from __future__ import annotations

import pytest

import unai_engine.providers.openai_compat as provider_module
from unai_engine.providers.openai_compat import OpenAICompatibleProvider, ProviderError


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "local specialist response"}}]}


class FakeAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.request = {"url": url, "headers": headers, "json": json}
        requests.append(self.request)
        return FakeResponse()


requests: list[dict] = []


@pytest.mark.asyncio
async def test_provider_sends_server_selected_specialist_model_override(monkeypatch) -> None:
    requests.clear()
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeAsyncClient)
    provider = OpenAICompatibleProvider(
        "http://127.0.0.1:11434/v1",
        "base-model",
        api_key="test-secret",
    )

    result = await provider.complete(
        [{"role": "user", "content": "Choose the next step."}],
        model_override="unai-next-action-lora",
    )

    assert result == "local specialist response"
    assert requests[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert requests[0]["json"]["model"] == "unai-next-action-lora"
    assert requests[0]["headers"]["Authorization"] == "Bearer test-secret"


@pytest.mark.asyncio
async def test_provider_preserves_base_model_when_no_override_is_supplied(monkeypatch) -> None:
    requests.clear()
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeAsyncClient)
    provider = OpenAICompatibleProvider("http://127.0.0.1:11434/v1", "base-model")

    await provider.complete([{"role": "user", "content": "Hello"}])

    assert requests[0]["json"]["model"] == "base-model"


@pytest.mark.asyncio
async def test_provider_rejects_invalid_model_override_before_network_call() -> None:
    requests.clear()
    provider = OpenAICompatibleProvider("http://127.0.0.1:11434/v1", "base-model")

    with pytest.raises(ProviderError, match="model identifier is invalid"):
        await provider.complete(
            [{"role": "user", "content": "Hello"}],
            model_override="untrusted model\nwith control",
        )

    assert requests == []
