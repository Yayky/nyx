"""Provider registry and fallback orchestration for Nyx.

The registry constructs providers from config, resolves explicit or default
provider selection, and applies the documented fallback chain across configured
backends.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nyx.config import NyxConfig, ProviderConfig
from nyx.providers.base import (
    ModelProvider,
    ProviderError,
    ProviderQueryError,
    ProviderQueryResult,
    UnknownProviderError,
)
from nyx.providers.http import AnthropicProvider, OllamaProvider, OpenAICompatibleProvider, OpenAIProvider
from nyx.providers.subprocess_cli import SubprocessCLIProvider


class AllProvidersUnavailableError(ProviderError):
    """Raised when every candidate provider is unavailable or fails."""

    def __init__(self, failures: dict[str, str]) -> None:
        """Store provider failures for user-facing degraded-mode messages."""

        self.failures = failures
        detail = "; ".join(f"{name}: {reason}" for name, reason in failures.items())
        super().__init__(f"all configured providers failed ({detail})")


class ProviderRegistry:
    """Construct and query model providers defined in Nyx configuration."""

    def __init__(self, config: NyxConfig, logger: logging.Logger | None = None) -> None:
        """Build the provider registry from the loaded Nyx config."""

        self.config = config
        self.logger = logger or logging.getLogger("nyx.providers")
        self.providers = {
            provider_config.name: self._build_provider(provider_config)
            for provider_config in config.models.providers
        }

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Query the selected provider or walk the configured fallback chain."""

        provider_names = (
            [preferred_provider_name]
            if preferred_provider_name
            else self._default_chain()
        )
        failures: dict[str, str] = {}

        for index, provider_name in enumerate(provider_names):
            try:
                provider = self.get(provider_name)
            except UnknownProviderError as exc:
                failures[provider_name] = str(exc)
                self.logger.warning("%s", exc)
                if preferred_provider_name:
                    raise
                continue

            if not await provider.is_available():
                reason = "provider unavailable"
                failures[provider_name] = reason
                self.logger.info("Skipping unavailable provider '%s'.", provider_name)
                if preferred_provider_name:
                    raise AllProvidersUnavailableError(failures)
                continue

            try:
                text = await provider.query(prompt=prompt, context=context)
            except ProviderError as exc:
                failures[provider_name] = str(exc)
                self.logger.warning("Provider '%s' failed: %s", provider_name, exc)
                if preferred_provider_name:
                    raise
                continue
            except Exception as exc:
                failures[provider_name] = str(exc)
                self.logger.exception("Unexpected provider error from '%s'.", provider_name)
                if preferred_provider_name:
                    raise ProviderQueryError(str(exc)) from exc
                continue

            return ProviderQueryResult(
                provider_name=provider.name,
                provider_type=provider.type,
                model_name=provider.model_name,
                text=text,
                fallback_used=not preferred_provider_name and index > 0,
            )

        raise AllProvidersUnavailableError(failures)

    async def query_with_image(
        self,
        prompt: str,
        image_path: Path,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Query a provider chain using one image input plus text prompt."""

        provider_names = (
            [preferred_provider_name]
            if preferred_provider_name
            else self._default_chain()
        )
        failures: dict[str, str] = {}

        for index, provider_name in enumerate(provider_names):
            try:
                provider = self.get(provider_name)
            except UnknownProviderError as exc:
                failures[provider_name] = str(exc)
                self.logger.warning("%s", exc)
                if preferred_provider_name:
                    raise
                continue

            if not provider.supports_vision:
                reason = "provider does not support vision"
                failures[provider_name] = reason
                self.logger.info("Skipping non-vision provider '%s'.", provider_name)
                if preferred_provider_name:
                    raise AllProvidersUnavailableError(failures)
                continue

            if not await provider.is_available():
                reason = "provider unavailable"
                failures[provider_name] = reason
                self.logger.info("Skipping unavailable provider '%s'.", provider_name)
                if preferred_provider_name:
                    raise AllProvidersUnavailableError(failures)
                continue

            try:
                text = await provider.query_with_image(
                    prompt=prompt,
                    image_path=image_path,
                    context=context,
                )
            except ProviderError as exc:
                failures[provider_name] = str(exc)
                self.logger.warning("Provider '%s' vision query failed: %s", provider_name, exc)
                if preferred_provider_name:
                    raise
                continue
            except Exception as exc:
                failures[provider_name] = str(exc)
                self.logger.exception("Unexpected provider error from '%s' vision query.", provider_name)
                if preferred_provider_name:
                    raise ProviderQueryError(str(exc)) from exc
                continue

            return ProviderQueryResult(
                provider_name=provider.name,
                provider_type=provider.type,
                model_name=provider.model_name,
                text=text,
                fallback_used=not preferred_provider_name and index > 0,
            )

        raise AllProvidersUnavailableError(failures)

    def get(self, provider_name: str) -> ModelProvider:
        """Return a named provider or raise a descriptive error."""

        try:
            return self.providers[provider_name]
        except KeyError as exc:
            raise UnknownProviderError(f"Unknown provider '{provider_name}'.") from exc

    def _build_provider(self, provider_config: ProviderConfig) -> ModelProvider:
        """Instantiate the correct provider implementation for a config entry."""

        if provider_config.type == "ollama":
            return OllamaProvider(provider_config)
        if provider_config.type == "anthropic":
            return AnthropicProvider(provider_config)
        if provider_config.type == "openai":
            return OpenAIProvider(provider_config)
        if provider_config.type == "openai-compat":
            return OpenAICompatibleProvider(provider_config)
        if provider_config.type == "subprocess-cli":
            return SubprocessCLIProvider(provider_config)
        raise ProviderQueryError(
            f"Provider '{provider_config.name}' has unsupported type '{provider_config.type}'."
        )

    def _default_chain(self) -> list[str]:
        """Return the deduplicated default-plus-fallback provider order."""

        ordered = [self.config.models.default, *self.config.models.fallback]
        seen: set[str] = set()
        result: list[str] = []
        for name in ordered:
            if name in seen:
                continue
            seen.add(name)
            result.append(name)
        return result
