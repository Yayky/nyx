"""Provider abstractions and shared types for Nyx model backends.

This module defines the stable interface used by the intent router and provider
registry. Concrete providers implement the documented HTTP and subprocess CLI
backends behind this abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal

from nyx.config import ProviderConfig


class ProviderError(RuntimeError):
    """Base class for provider-related errors."""


class ProviderConfigurationError(ProviderError):
    """Raised when a provider config is internally inconsistent."""


class ProviderUnavailableError(ProviderError):
    """Raised when a provider cannot be used in the current environment."""


class ProviderQueryError(ProviderError):
    """Raised when a provider call fails or returns an invalid response."""


class UnknownProviderError(ProviderError):
    """Raised when a configured provider name cannot be resolved."""


@dataclass(slots=True)
class ProviderMessage:
    """One structured conversation message routed to a provider."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(slots=True)
class ProviderQueryResult:
    """Structured result returned by the provider registry.

    Attributes:
        provider_name: The named provider selected from config.
        provider_type: The concrete provider backend type.
        model_name: Underlying model identifier when the provider exposes one.
        text: Final text extracted from the provider response.
        fallback_used: Whether this provider was selected from the fallback
            chain rather than the default configured provider.
        degraded: Whether Nyx is currently operating in a degraded mode such as
            local-only operation while configured cloud providers are
            unavailable.
        degraded_reason: Optional machine-readable degraded-mode reason.
        provider_tier: Resolved provider tier such as ``local``, ``cloud``, or
            ``cli``.
    """

    provider_name: str
    provider_type: str
    model_name: str | None
    text: str
    fallback_used: bool
    token_count: int | None = None
    degraded: bool = False
    degraded_reason: str | None = None
    provider_tier: str | None = None


class ModelProvider(ABC):
    """Abstract interface for Nyx model providers."""

    def __init__(self, provider_config: ProviderConfig) -> None:
        """Store the provider configuration for later query operations."""

        self.provider_config = provider_config
        self.name = provider_config.name
        self.type = provider_config.type

    @property
    def model_name(self) -> str | None:
        """Return the configured underlying model name when present."""

        model = self.provider_config.options.get("model")
        return model if isinstance(model, str) else None

    @abstractmethod
    async def query(self, prompt: str, context: dict[str, Any]) -> str:
        """Submit a prompt plus context and return the provider's text output."""

    async def query_messages(
        self,
        messages: list[ProviderMessage],
        context: dict[str, Any],
    ) -> str:
        """Submit structured conversation messages to the provider."""

        return await self.query(self.render_messages_prompt(messages, context), {})

    async def query_with_image(
        self,
        prompt: str,
        image_path: Path,
        context: dict[str, Any],
    ) -> str:
        """Submit a prompt plus image when the provider supports vision."""

        del prompt, image_path, context
        raise ProviderUnavailableError(
            f"Provider '{self.name}' does not support image input."
        )

    @abstractmethod
    async def is_available(self) -> bool:
        """Return whether the provider is usable in the current environment."""

    @property
    def supports_vision(self) -> bool:
        """Return whether the provider supports image input."""

        return False

    def render_prompt(self, prompt: str, context: dict[str, Any]) -> str:
        """Serialize prompt and context into a provider-friendly prompt string."""

        if not context:
            return prompt

        rendered_context = json.dumps(context, indent=2, sort_keys=True, default=str)
        return f"Context:\n{rendered_context}\n\nUser request:\n{prompt}"

    def render_messages_prompt(
        self,
        messages: list[ProviderMessage],
        context: dict[str, Any],
    ) -> str:
        """Render structured messages into a stable transcript fallback."""

        transcript_lines = [
            "Conversation context follows. Answer the latest user message."
        ]
        if context:
            rendered_context = json.dumps(context, indent=2, sort_keys=True, default=str)
            transcript_lines.extend(["", "Context:", rendered_context, ""])

        for message in messages:
            speaker = {
                "system": "System",
                "user": "User",
                "assistant": "Assistant",
            }[message.role]
            transcript_lines.append(f"{speaker}: {message.content}")
        return "\n".join(transcript_lines).strip()

    def require_option(self, key: str) -> Any:
        """Return a required provider option or raise a config error."""

        if key not in self.provider_config.options:
            raise ProviderConfigurationError(
                f"Provider '{self.name}' is missing required option '{key}'."
            )
        return self.provider_config.options[key]
