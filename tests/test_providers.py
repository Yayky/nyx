"""Tests for Nyx provider implementations and registry fallback behavior."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from nyx.config import ProviderConfig, load_config
from nyx.providers.base import (
    ModelProvider,
    ProviderQueryError,
    ProviderQueryResult,
    ProviderUnavailableError,
)
from nyx.providers.http import AnthropicProvider, OllamaProvider, OpenAIProvider
from nyx.providers.registry import ProviderRegistry
from nyx.providers.subprocess_cli import SubprocessCLIProvider


def _client_factory(transport: httpx.BaseTransport):
    """Create an ``httpx.AsyncClient`` factory bound to a mock transport."""

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, **kwargs)

    return factory


@pytest.mark.anyio
async def test_ollama_provider_queries_generate_endpoint() -> None:
    """Ollama provider should use the documented non-streaming generate API."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        assert request.method == "POST"
        assert request.read().decode().find('"stream":false') > 0
        return httpx.Response(200, json={"response": "ollama answer"})

    provider = OllamaProvider(
        ProviderConfig(
            name="ollama-local",
            type="ollama",
            options={"host": "http://localhost:11434", "model": "qwen2.5:7b"},
        ),
        client_factory=_client_factory(httpx.MockTransport(handler)),
    )

    result = await provider.query("hello", {})

    assert result == "ollama answer"


@pytest.mark.anyio
async def test_openai_provider_extracts_chat_completion_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI provider should extract assistant text from chat completions."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "openai answer",
                        }
                    }
                ]
            },
        )

    provider = OpenAIProvider(
        ProviderConfig(
            name="openai",
            type="openai",
            options={"model": "gpt-4o", "api_key_env": "OPENAI_API_KEY"},
        ),
        client_factory=_client_factory(httpx.MockTransport(handler)),
    )

    result = await provider.query("hello", {})

    assert result == "openai answer"


@pytest.mark.anyio
async def test_openai_provider_sends_image_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OpenAI vision queries should send image_url content blocks."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        content = payload["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "vision answer"}}]},
        )

    provider = OpenAIProvider(
        ProviderConfig(
            name="openai",
            type="openai",
            options={"model": "gpt-4o", "api_key_env": "OPENAI_API_KEY"},
        ),
        client_factory=_client_factory(httpx.MockTransport(handler)),
    )

    result = await provider.query_with_image("what is on screen?", image_path, {})

    assert result == "vision answer"


@pytest.mark.anyio
async def test_ollama_provider_sends_images_array_for_vision(tmp_path: Path) -> None:
    """Ollama vision queries should send base64 images to the chat endpoint."""

    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"fakepng")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.read().decode())
        assert payload["messages"][0]["images"] == [base64.b64encode(b"fakepng").decode("ascii")]
        return httpx.Response(200, json={"message": {"content": "ollama vision answer"}})

    provider = OllamaProvider(
        ProviderConfig(
            name="ollama-local",
            type="ollama",
            options={"host": "http://localhost:11434", "model": "gemma3"},
        ),
        client_factory=_client_factory(httpx.MockTransport(handler)),
    )

    result = await provider.query_with_image("describe screen", image_path, {})

    assert result == "ollama vision answer"


@pytest.mark.anyio
async def test_ollama_provider_uses_extended_default_vision_timeout(tmp_path: Path) -> None:
    """Ollama vision queries should allow longer time for local model startup."""

    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"fakepng")
    seen_timeout: Any = None

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        nonlocal seen_timeout
        seen_timeout = kwargs.get("timeout")
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200,
            json={"message": {"content": "ok"}},
        )), **kwargs)

    provider = OllamaProvider(
        ProviderConfig(
            name="ollama-local",
            type="ollama",
            options={"host": "http://localhost:11434", "model": "gemma3"},
        ),
        client_factory=factory,
    )

    result = await provider.query_with_image("describe screen", image_path, {})

    assert result == "ok"
    assert seen_timeout == 180.0


@pytest.mark.anyio
async def test_ollama_provider_maps_vision_timeout_to_provider_error(tmp_path: Path) -> None:
    """Ollama vision timeout failures should become descriptive provider errors."""

    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"fakepng")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    provider = OllamaProvider(
        ProviderConfig(
            name="ollama-vision",
            type="ollama",
            options={"host": "http://localhost:11434", "model": "qwen2.5vl:7b"},
        ),
        client_factory=_client_factory(httpx.MockTransport(handler)),
    )

    with pytest.raises(ProviderQueryError, match="vision request timed out after 180 seconds"):
        await provider.query_with_image("what is on screen?", image_path, {})


@pytest.mark.anyio
async def test_anthropic_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic provider should be unavailable when its API key is absent."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider(
        ProviderConfig(
            name="anthropic",
            type="anthropic",
            options={"model": "claude-sonnet-4-5", "api_key_env": "ANTHROPIC_API_KEY"},
        )
    )

    assert await provider.is_available() is False
    with pytest.raises(ProviderUnavailableError):
        await provider.query("hello", {})


@pytest.mark.anyio
async def test_subprocess_provider_parses_json_output() -> None:
    """The subprocess provider should extract response text from JSON stdout."""

    class FakeProcess:
        """Small fake subprocess object for deterministic provider tests."""

        returncode = 0

        async def communicate(self, input_data: bytes | None) -> tuple[bytes, bytes]:
            assert input_data == b"hello"
            return b'{"response": "subprocess answer"}\n', b""

        def kill(self) -> None:
            """The fake process never needs to be killed."""

    async def fake_process_factory(*args: str, **kwargs: Any) -> FakeProcess:
        assert args[0] == "python3"
        return FakeProcess()

    provider = SubprocessCLIProvider(
        ProviderConfig(
            name="fixture-cli",
            type="subprocess-cli",
            options={
                "binary": "python3",
                "args": ["fake_cli.py", "-"],
                "timeout_seconds": 5,
            },
        ),
        process_factory=fake_process_factory,
    )

    result = await provider.query("hello", {})

    assert result == "subprocess answer"


@pytest.mark.anyio
async def test_subprocess_provider_parses_codex_jsonl_events() -> None:
    """The subprocess provider should extract text from Codex JSONL events."""

    class FakeProcess:
        """Small fake subprocess object for deterministic Codex-event tests."""

        returncode = 0

        async def communicate(self, input_data: bytes | None) -> tuple[bytes, bytes]:
            assert input_data == b"hello"
            return (
                (
                    b'{"type":"thread.started","thread_id":"abc"}\n'
                    b'{"type":"turn.started"}\n'
                    b'{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Hi."}}\n'
                    b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
                ),
                b"",
            )

        def kill(self) -> None:
            """The fake process never needs to be killed."""

    async def fake_process_factory(*args: str, **kwargs: Any) -> FakeProcess:
        assert args[0] == "codex"
        return FakeProcess()

    provider = SubprocessCLIProvider(
        ProviderConfig(
            name="codex-cli",
            type="subprocess-cli",
            options={
                "binary": "codex",
                "args": ["exec", "--json", "-"],
                "timeout_seconds": 5,
            },
        ),
        process_factory=fake_process_factory,
    )

    result = await provider.query("hello", {})

    assert result == "Hi."


@pytest.mark.anyio
async def test_subprocess_provider_supports_configured_image_args(tmp_path: Path) -> None:
    """The subprocess provider should pass configured image arguments to the CLI."""

    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"fakepng")

    class FakeProcess:
        """Small fake subprocess object for deterministic vision-provider tests."""

        returncode = 0

        async def communicate(self, input_data: bytes | None) -> tuple[bytes, bytes]:
            assert input_data == b"describe this image"
            return b'{"response": "vision via codex"}\n', b""

        def kill(self) -> None:
            """The fake process never needs to be killed."""

    async def fake_process_factory(*args: str, **kwargs: Any) -> FakeProcess:
        assert args == (
            "codex",
            "exec",
            "--json",
            "-",
            "--image",
            str(image_path),
        )
        return FakeProcess()

    provider = SubprocessCLIProvider(
        ProviderConfig(
            name="codex-cli",
            type="subprocess-cli",
            options={
                "binary": "codex",
                "args": ["exec", "--json", "-"],
                "image_args": ["--image", "{image_path}"],
                "timeout_seconds": 5,
            },
        ),
        process_factory=fake_process_factory,
    )

    result = await provider.query_with_image("describe this image", image_path, {})

    assert provider.supports_vision is True
    assert result == "vision via codex"


@pytest.mark.anyio
async def test_provider_registry_uses_fallback_chain(tmp_path) -> None:
    """The registry should skip unavailable providers and use the next fallback."""

    @dataclass
    class FakeProvider(ModelProvider):
        """Small provider stub used to verify fallback ordering."""

        available: bool
        response: str

        def __init__(self, name: str, provider_type: str, available: bool, response: str) -> None:
            super().__init__(ProviderConfig(name=name, type=provider_type, options={}))
            self.available = available
            self.response = response

        async def query(self, prompt: str, context: dict[str, Any]) -> str:
            return self.response

        async def is_available(self) -> bool:
            return self.available

    config = load_config(tmp_path / "missing.toml")
    registry = ProviderRegistry(config)
    registry.providers = {
        "ollama-local": FakeProvider("ollama-local", "ollama", available=False, response=""),
        "anthropic": FakeProvider("anthropic", "anthropic", available=True, response="fallback answer"),
    }
    config.models.fallback = ["anthropic"]

    result = await registry.query("hello", {}, preferred_provider_name=None)

    assert result.text == "fallback answer"
    assert result.provider_name == "anthropic"
    assert result.fallback_used is True


@pytest.mark.anyio
async def test_provider_registry_marks_local_only_degraded_when_cloud_is_unavailable(tmp_path) -> None:
    """Local results should be marked degraded when no configured cloud provider is available."""

    @dataclass
    class FakeProvider(ModelProvider):
        """Small provider stub used to verify degraded-mode behavior."""

        available: bool
        response: str

        def __init__(self, name: str, provider_type: str, available: bool, response: str) -> None:
            super().__init__(ProviderConfig(name=name, type=provider_type, options={}))
            self.available = available
            self.response = response

        async def query(self, prompt: str, context: dict[str, Any]) -> str:
            del prompt, context
            return self.response

        async def is_available(self) -> bool:
            return self.available

    config = load_config(tmp_path / "missing.toml")
    registry = ProviderRegistry(config)
    registry.providers = {
        "ollama-local": FakeProvider("ollama-local", "ollama", available=True, response="local answer"),
        "anthropic": FakeProvider("anthropic", "anthropic", available=False, response=""),
    }
    config.models.default = "ollama-local"
    config.models.fallback = ["anthropic"]

    result = await registry.query("hello", {}, preferred_provider_name=None)

    assert result.provider_name == "ollama-local"
    assert result.degraded is True
    assert result.degraded_reason == "local_only"
    assert result.provider_tier == "local"


@pytest.mark.anyio
async def test_provider_registry_prefers_cloud_tier_when_requested(tmp_path) -> None:
    """Tier-aware queries should select a cloud provider before local fallbacks."""

    @dataclass
    class FakeProvider(ModelProvider):
        """Small provider stub used to verify preferred-tier ordering."""

        available: bool
        response: str

        def __init__(self, name: str, provider_type: str, available: bool, response: str) -> None:
            super().__init__(ProviderConfig(name=name, type=provider_type, options={}))
            self.available = available
            self.response = response

        async def query(self, prompt: str, context: dict[str, Any]) -> str:
            del prompt, context
            return self.response

        async def is_available(self) -> bool:
            return self.available

    config = load_config(tmp_path / "missing.toml")
    registry = ProviderRegistry(config)
    registry.providers = {
        "ollama-local": FakeProvider("ollama-local", "ollama", available=True, response="local answer"),
        "anthropic": FakeProvider("anthropic", "anthropic", available=True, response="cloud answer"),
    }
    config.models.default = "ollama-local"
    config.models.fallback = ["anthropic"]

    result = await registry.query("hello", {}, preferred_provider_name=None, preferred_tiers=("cloud",))

    assert result.provider_name == "anthropic"
    assert result.provider_tier == "cloud"
    assert result.degraded is False
