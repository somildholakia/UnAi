from __future__ import annotations

import httpx


class ProviderError(RuntimeError):
    """A safe, user-displayable model provider failure."""


class OpenAICompatibleProvider:
    """Minimal adapter for local Ollama/vLLM/other OpenAI-compatible servers."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def complete(self, messages: list[dict[str, str]]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.ConnectError as exc:
            raise ProviderError(
                "Cannot reach the configured local model server. Start it and verify UNAI_MODEL_BASE_URL."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("The model server did not respond before the configured timeout.") from exc
        except httpx.HTTPStatusError as exc:
            # Do not echo response headers or request details; they may contain secrets.
            raise ProviderError(f"Model server returned HTTP {exc.response.status_code}.") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("The model server returned an invalid response.") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("The model server response did not contain an assistant message.") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("The model server returned an empty assistant message.")
        return content.strip()
