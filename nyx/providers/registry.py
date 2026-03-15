"""Provider registry and fallback orchestration for Nyx.

The registry constructs providers from config, resolves explicit or default
provider selection, and applies the documented fallback chain across configured
backends.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from nyx.config import NyxConfig, ProviderConfig
from nyx.providers.base import (
    ModelProvider,
    ProviderError,
    ProviderMessage,
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
        preferred_tiers: tuple[str, ...] | None = None,
    ) -> ProviderQueryResult:
        """Query the selected provider or walk the configured fallback chain."""

        provider_names = self._provider_chain(
            preferred_provider_name=preferred_provider_name,
            preferred_tiers=preferred_tiers,
        )
        failures: dict[str, str] = {}
        availability: dict[str, bool] = {}

        for index, provider_name in enumerate(provider_names):
            try:
                provider = self.get(provider_name)
            except UnknownProviderError as exc:
                failures[provider_name] = str(exc)
                self.logger.warning("%s", exc)
                if preferred_provider_name:
                    raise
                continue

            provider_available = await provider.is_available()
            availability[provider_name] = provider_available
            if not provider_available:
                reason = "provider unavailable"
                failures[provider_name] = reason
                self.logger.info("Skipping unavailable provider '%s'.", provider_name)
                if preferred_provider_name:
                    raise AllProvidersUnavailableError(failures)
                continue

            try:
                text = await provider.query(prompt=prompt, context=context)
            except ProviderError as exc:
                availability[provider_name] = False
                failures[provider_name] = str(exc)
                self.logger.warning("Provider '%s' failed: %s", provider_name, exc)
                if preferred_provider_name:
                    raise
                continue
            except Exception as exc:
                availability[provider_name] = False
                failures[provider_name] = str(exc)
                self.logger.exception("Unexpected provider error from '%s'.", provider_name)
                if preferred_provider_name:
                    raise ProviderQueryError(str(exc)) from exc
                continue

            degraded, degraded_reason = await self._degraded_state_for_result(
                selected_provider_name=provider.name,
                selected_provider=provider,
                availability=availability,
                preferred_provider_name=preferred_provider_name,
                preferred_tiers=preferred_tiers,
            )
            return ProviderQueryResult(
                provider_name=provider.name,
                provider_type=provider.type,
                model_name=provider.model_name,
                text=text,
                fallback_used=not preferred_provider_name and index > 0,
                degraded=degraded,
                degraded_reason=degraded_reason,
                provider_tier=self._provider_tier(provider),
            )

        raise AllProvidersUnavailableError(failures)

    async def query_messages(
        self,
        messages: list[ProviderMessage],
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
        preferred_tiers: tuple[str, ...] | None = None,
    ) -> ProviderQueryResult:
        """Query the selected provider or fallback chain with structured messages."""

        provider_names = self._provider_chain(
            preferred_provider_name=preferred_provider_name,
            preferred_tiers=preferred_tiers,
        )
        failures: dict[str, str] = {}
        availability: dict[str, bool] = {}

        for index, provider_name in enumerate(provider_names):
            try:
                provider = self.get(provider_name)
            except UnknownProviderError as exc:
                failures[provider_name] = str(exc)
                self.logger.warning("%s", exc)
                if preferred_provider_name:
                    raise
                continue

            provider_available = await provider.is_available()
            availability[provider_name] = provider_available
            if not provider_available:
                reason = "provider unavailable"
                failures[provider_name] = reason
                self.logger.info("Skipping unavailable provider '%s'.", provider_name)
                if preferred_provider_name:
                    raise AllProvidersUnavailableError(failures)
                continue

            try:
                text = await provider.query_messages(messages=messages, context=context)
            except ProviderError as exc:
                availability[provider_name] = False
                failures[provider_name] = str(exc)
                self.logger.warning("Provider '%s' failed: %s", provider_name, exc)
                if preferred_provider_name:
                    raise
                continue
            except Exception as exc:
                availability[provider_name] = False
                failures[provider_name] = str(exc)
                self.logger.exception("Unexpected provider error from '%s'.", provider_name)
                if preferred_provider_name:
                    raise ProviderQueryError(str(exc)) from exc
                continue

            degraded, degraded_reason = await self._degraded_state_for_result(
                selected_provider_name=provider.name,
                selected_provider=provider,
                availability=availability,
                preferred_provider_name=preferred_provider_name,
                preferred_tiers=preferred_tiers,
            )
            return ProviderQueryResult(
                provider_name=provider.name,
                provider_type=provider.type,
                model_name=provider.model_name,
                text=text,
                fallback_used=not preferred_provider_name and index > 0,
                degraded=degraded,
                degraded_reason=degraded_reason,
                provider_tier=self._provider_tier(provider),
            )

        raise AllProvidersUnavailableError(failures)

    async def query_with_image(
        self,
        prompt: str,
        image_path: Path,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
        preferred_tiers: tuple[str, ...] | None = None,
    ) -> ProviderQueryResult:
        """Query a provider chain using one image input plus text prompt."""

        provider_names = self._provider_chain(
            preferred_provider_name=preferred_provider_name,
            preferred_tiers=preferred_tiers,
        )
        failures: dict[str, str] = {}
        availability: dict[str, bool] = {}

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

            provider_available = await provider.is_available()
            availability[provider_name] = provider_available
            if not provider_available:
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
                availability[provider_name] = False
                failures[provider_name] = str(exc)
                self.logger.warning("Provider '%s' vision query failed: %s", provider_name, exc)
                if preferred_provider_name:
                    raise
                continue
            except Exception as exc:
                availability[provider_name] = False
                failures[provider_name] = str(exc)
                self.logger.exception("Unexpected provider error from '%s' vision query.", provider_name)
                if preferred_provider_name:
                    raise ProviderQueryError(str(exc)) from exc
                continue

            degraded, degraded_reason = await self._degraded_state_for_result(
                selected_provider_name=provider.name,
                selected_provider=provider,
                availability=availability,
                preferred_provider_name=preferred_provider_name,
                preferred_tiers=preferred_tiers,
            )
            return ProviderQueryResult(
                provider_name=provider.name,
                provider_type=provider.type,
                model_name=provider.model_name,
                text=text,
                fallback_used=not preferred_provider_name and index > 0,
                degraded=degraded,
                degraded_reason=degraded_reason,
                provider_tier=self._provider_tier(provider),
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

    def _provider_chain(
        self,
        preferred_provider_name: str | None,
        preferred_tiers: tuple[str, ...] | None,
    ) -> list[str]:
        """Return the provider order for the current query strategy."""

        if preferred_provider_name:
            return [preferred_provider_name]

        chain = self._default_chain()
        if not preferred_tiers:
            return chain

        prioritized: list[str] = []
        deferred: list[str] = []
        tier_order = tuple(preferred_tiers)
        for provider_name in chain:
            provider = self.providers.get(provider_name)
            if provider is None:
                deferred.append(provider_name)
                continue
            if self._provider_tier(provider) in tier_order:
                prioritized.append(provider_name)
            else:
                deferred.append(provider_name)
        return [*prioritized, *deferred]

    async def _degraded_state_for_result(
        self,
        *,
        selected_provider_name: str,
        selected_provider: ModelProvider,
        availability: dict[str, bool],
        preferred_provider_name: str | None,
        preferred_tiers: tuple[str, ...] | None,
    ) -> tuple[bool, str | None]:
        """Return whether the current successful result should be marked degraded."""

        selected_tier = self._provider_tier(selected_provider)
        cloud_names = [
            name
            for name in self._default_chain()
            if self._provider_tier(self.providers.get(name)) == "cloud"
        ]
        if not cloud_names:
            return False, None

        if selected_tier == "cloud":
            return False, None

        if preferred_provider_name is not None and self._provider_tier(self.providers.get(preferred_provider_name)) == "cloud":
            return True, "local_only"

        cloud_available = await self._any_cloud_provider_available(cloud_names, availability)
        if cloud_available:
            return False, None

        if preferred_tiers and "cloud" in preferred_tiers:
            return True, "local_only"

        return True, "local_only"

    async def _any_cloud_provider_available(
        self,
        provider_names: list[str],
        availability: dict[str, bool],
    ) -> bool:
        """Return whether any configured cloud provider is currently available."""

        for provider_name in provider_names:
            if provider_name in availability:
                if availability[provider_name]:
                    return True
                continue
            provider = self.providers.get(provider_name)
            if provider is None:
                continue
            available = await provider.is_available()
            availability[provider_name] = available
            if available:
                return True
        return False

    def _provider_tier(self, provider: ModelProvider | None) -> str | None:
        """Classify one provider into the documented local/cloud/cli tiers."""

        if provider is None:
            return None
        if provider.type == "ollama":
            return "local"
        if provider.type == "subprocess-cli":
            return "cli"
        if provider.type in {"anthropic", "openai"}:
            return "cloud"
        if provider.type == "openai-compat":
            base_url = provider.provider_config.options.get("base_url")
            if isinstance(base_url, str):
                hostname = (urlparse(base_url).hostname or "").casefold()
                if hostname in {"localhost", "127.0.0.1", "::1"}:
                    return "local"
            return "cloud"
        return None
