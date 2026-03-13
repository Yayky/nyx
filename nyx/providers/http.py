"""HTTP provider implementations for Nyx.

These providers use ``httpx.AsyncClient`` so model queries remain compatible
with the project's asyncio-only constraint. Each provider follows the current
official API shape for its backend.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx

from nyx.config import ProviderConfig
from nyx.providers.base import (
    ModelProvider,
    ProviderQueryError,
    ProviderUnavailableError,
)

AsyncClientFactory = Callable[..., httpx.AsyncClient]


class OllamaProvider(ModelProvider):
    """Model provider backed by the official Ollama HTTP API."""

    def __init__(
        self,
        provider_config: ProviderConfig,
        client_factory: AsyncClientFactory | None = None,
    ) -> None:
        """Initialize the provider with its config and optional client factory."""

        super().__init__(provider_config)
        self._client_factory = client_factory or httpx.AsyncClient
        self._base_url = self.require_option("host")
        self._timeout_seconds = float(self.provider_config.options.get("timeout_seconds", 60.0))
        self._vision_timeout_seconds = float(
            self.provider_config.options.get("vision_timeout_seconds", 180.0)
        )

    async def is_available(self) -> bool:
        """Check whether the local Ollama server is reachable."""

        try:
            async with self._client_factory(base_url=self._base_url, timeout=5.0) as client:
                response = await client.get("/api/tags")
        except httpx.HTTPError:
            return False
        return response.is_success

    async def query(self, prompt: str, context: dict[str, Any]) -> str:
        """Generate text through ``/api/generate`` using non-streaming output."""

        payload = {
            "model": self.require_option("model"),
            "prompt": self.render_prompt(prompt, context),
            "stream": False,
        }
        try:
            async with self._client_factory(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.post("/api/generate", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderQueryError(
                f"Ollama provider '{self.name}' timed out after {self._timeout_seconds:.0f} seconds."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderQueryError(
                f"Ollama provider '{self.name}' request failed: {exc}"
            ) from exc

        self._raise_for_status(response, "Ollama request failed")
        data = response.json()
        text = data.get("response")
        if not isinstance(text, str) or not text.strip():
            raise ProviderQueryError(
                f"Ollama provider '{self.name}' returned no response text."
            )
        return text.strip()

    @property
    def supports_vision(self) -> bool:
        """Ollama can accept image input when the configured model supports it."""

        return True

    async def query_with_image(
        self,
        prompt: str,
        image_path: Path,
        context: dict[str, Any],
    ) -> str:
        """Generate text through ``/api/chat`` with one base64-encoded image."""

        payload = {
            "model": self.require_option("model"),
            "messages": [
                {
                    "role": "user",
                    "content": self.render_prompt(prompt, context),
                    "images": [_base64_image(image_path)],
                }
            ],
            "stream": False,
        }
        try:
            async with self._client_factory(
                base_url=self._base_url,
                timeout=self._vision_timeout_seconds,
            ) as client:
                response = await client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderQueryError(
                f"Ollama provider '{self.name}' vision request timed out after "
                f"{self._vision_timeout_seconds:.0f} seconds."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderQueryError(
                f"Ollama provider '{self.name}' vision request failed: {exc}"
            ) from exc

        self._raise_for_status(response, "Ollama vision request failed")
        data = response.json()
        message = data.get("message")
        if not isinstance(message, dict):
            raise ProviderQueryError(
                f"Ollama provider '{self.name}' returned an unexpected vision payload."
            )
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise ProviderQueryError(
                f"Ollama provider '{self.name}' returned no vision response text."
            )
        return text.strip()

    def _raise_for_status(self, response: httpx.Response, message: str) -> None:
        """Raise a provider error for unsuccessful HTTP responses."""

        if response.is_success:
            return
        raise ProviderQueryError(f"{message}: {response.status_code} {response.text}")


class AnthropicProvider(ModelProvider):
    """Model provider backed by the Anthropic Messages API."""

    _ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        provider_config: ProviderConfig,
        client_factory: AsyncClientFactory | None = None,
    ) -> None:
        """Initialize the provider with API credentials resolved at query time."""

        super().__init__(provider_config)
        self._client_factory = client_factory or httpx.AsyncClient
        self._base_url = str(self.provider_config.options.get("base_url", "https://api.anthropic.com"))

    async def is_available(self) -> bool:
        """Anthropic is available when the configured API key env var is set."""

        return bool(self._api_key())

    async def query(self, prompt: str, context: dict[str, Any]) -> str:
        """Submit a prompt to Anthropic's Messages API."""

        api_key = self._api_key()
        if not api_key:
            raise ProviderUnavailableError(
                f"Anthropic provider '{self.name}' is missing its API key."
            )

        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._ANTHROPIC_VERSION,
        }
        payload = {
            "model": self.require_option("model"),
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": self.render_prompt(prompt, context),
                }
            ],
        }
        async with self._client_factory(
            base_url=self._base_url,
            headers=headers,
            timeout=60.0,
        ) as client:
            response = await client.post("/v1/messages", json=payload)

        self._raise_for_status(response, "Anthropic request failed")
        data = response.json()
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderQueryError(
                f"Anthropic provider '{self.name}' returned an unexpected payload."
            )

        texts = [
            item.get("text", "").strip()
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "\n".join(part for part in texts if part)
        if not text:
            raise ProviderQueryError(
                f"Anthropic provider '{self.name}' returned no text content."
            )
        return text

    @property
    def supports_vision(self) -> bool:
        """Anthropic supports base64 image blocks in the Messages API."""

        return True

    async def query_with_image(
        self,
        prompt: str,
        image_path: Path,
        context: dict[str, Any],
    ) -> str:
        """Submit one image plus prompt to Anthropic's Messages API."""

        api_key = self._api_key()
        if not api_key:
            raise ProviderUnavailableError(
                f"Anthropic provider '{self.name}' is missing its API key."
            )

        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._ANTHROPIC_VERSION,
        }
        payload = {
            "model": self.require_option("model"),
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": _guess_media_type(image_path),
                                "data": _base64_image(image_path),
                            },
                        },
                        {
                            "type": "text",
                            "text": self.render_prompt(prompt, context),
                        },
                    ],
                }
            ],
        }
        async with self._client_factory(
            base_url=self._base_url,
            headers=headers,
            timeout=60.0,
        ) as client:
            response = await client.post("/v1/messages", json=payload)

        self._raise_for_status(response, "Anthropic vision request failed")
        data = response.json()
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderQueryError(
                f"Anthropic provider '{self.name}' returned an unexpected payload."
            )

        texts = [
            item.get("text", "").strip()
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "\n".join(part for part in texts if part)
        if not text:
            raise ProviderQueryError(
                f"Anthropic provider '{self.name}' returned no vision text content."
            )
        return text

    def _api_key(self) -> str | None:
        """Read the Anthropic API key from the configured environment variable."""

        env_name = self.require_option("api_key_env")
        return os.environ.get(env_name) if isinstance(env_name, str) else None

    def _raise_for_status(self, response: httpx.Response, message: str) -> None:
        """Raise a provider error for unsuccessful HTTP responses."""

        if response.is_success:
            return
        raise ProviderQueryError(f"{message}: {response.status_code} {response.text}")


class OpenAICompatibleProvider(ModelProvider):
    """Provider for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        provider_config: ProviderConfig,
        client_factory: AsyncClientFactory | None = None,
    ) -> None:
        """Initialize a provider using a configurable OpenAI-compatible base URL."""

        super().__init__(provider_config)
        self._client_factory = client_factory or httpx.AsyncClient
        self._base_url = str(self.require_option("base_url"))

    async def is_available(self) -> bool:
        """A compat provider is available if its optional API key is satisfied."""

        env_name = self.provider_config.options.get("api_key_env")
        if env_name is None:
            return True
        return bool(self._api_key())

    async def query(self, prompt: str, context: dict[str, Any]) -> str:
        """Query an OpenAI-compatible ``/chat/completions`` endpoint."""

        headers = self._build_headers()
        payload = {
            "model": self.require_option("model"),
            "messages": [
                {
                    "role": "user",
                    "content": self.render_prompt(prompt, context),
                }
            ],
        }
        async with self._client_factory(
            base_url=self._base_url,
            headers=headers,
            timeout=60.0,
        ) as client:
            response = await client.post("/chat/completions", json=payload)

        self._raise_for_status(response, "OpenAI-compatible request failed")
        return _extract_openai_message_text(response.json(), provider_name=self.name)

    @property
    def supports_vision(self) -> bool:
        """OpenAI-compatible chat APIs may accept image_url content blocks."""

        return True

    async def query_with_image(
        self,
        prompt: str,
        image_path: Path,
        context: dict[str, Any],
    ) -> str:
        """Query an OpenAI-compatible chat endpoint with one image input."""

        headers = self._build_headers()
        payload = {
            "model": self.require_option("model"),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self.render_prompt(prompt, context),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": _data_url(image_path),
                            },
                        },
                    ],
                }
            ],
        }
        async with self._client_factory(
            base_url=self._base_url,
            headers=headers,
            timeout=60.0,
        ) as client:
            response = await client.post("/chat/completions", json=payload)

        self._raise_for_status(response, "OpenAI-compatible vision request failed")
        return _extract_openai_message_text(response.json(), provider_name=self.name)

    def _api_key(self) -> str | None:
        """Read an optional API key from the configured environment variable."""

        env_name = self.provider_config.options.get("api_key_env")
        if not isinstance(env_name, str):
            return None
        return os.environ.get(env_name)

    def _build_headers(self) -> dict[str, str]:
        """Construct request headers for the provider."""

        headers: dict[str, str] = {}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _raise_for_status(self, response: httpx.Response, message: str) -> None:
        """Raise a provider error for unsuccessful HTTP responses."""

        if response.is_success:
            return
        raise ProviderQueryError(f"{message}: {response.status_code} {response.text}")


class OpenAIProvider(OpenAICompatibleProvider):
    """Provider for OpenAI's hosted chat completion API."""

    def __init__(
        self,
        provider_config: ProviderConfig,
        client_factory: AsyncClientFactory | None = None,
    ) -> None:
        """Initialize the OpenAI provider with the official API base URL."""

        if "base_url" not in provider_config.options:
            provider_config = ProviderConfig(
                name=provider_config.name,
                type=provider_config.type,
                options={
                    **provider_config.options,
                    "base_url": "https://api.openai.com/v1",
                },
            )
        super().__init__(provider_config, client_factory=client_factory)

    async def is_available(self) -> bool:
        """OpenAI is available only when the configured API key is present."""

        return bool(self._api_key())

    async def query(self, prompt: str, context: dict[str, Any]) -> str:
        """Require an API key before delegating to the shared chat completion flow."""

        if not self._api_key():
            raise ProviderUnavailableError(
                f"OpenAI provider '{self.name}' is missing its API key."
            )
        return await super().query(prompt, context)


def _extract_openai_message_text(payload: dict[str, Any], provider_name: str) -> str:
    """Extract the assistant message text from a chat completion payload."""

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderQueryError(
            f"Provider '{provider_name}' returned no choices in the response payload."
        )

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ProviderQueryError(
            f"Provider '{provider_name}' returned no message object in the first choice."
        )

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        texts = [
            item.get("text", "").strip()
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        text = "\n".join(part for part in texts if part)
        if text:
            return text

    raise ProviderQueryError(
        f"Provider '{provider_name}' returned no assistant text content."
    )


def _guess_media_type(image_path: Path) -> str:
    """Return a best-effort image media type for an on-disk screenshot."""

    guessed, _ = mimetypes.guess_type(image_path.name)
    if isinstance(guessed, str) and guessed.startswith("image/"):
        return guessed
    return "image/png"


def _base64_image(image_path: Path) -> str:
    """Encode an image file as base64 ASCII."""

    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def _data_url(image_path: Path) -> str:
    """Return a ``data:`` URL for one local image file."""

    return f"data:{_guess_media_type(image_path)};base64,{_base64_image(image_path)}"
