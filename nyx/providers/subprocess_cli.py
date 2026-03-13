"""Subprocess-backed CLI provider implementation for Nyx.

This provider launches configured command-line tools using asyncio subprocesses,
then extracts text from JSON or JSONL stdout output. It supports both stdin-fed
and positional-prompt invocation styles so the config can accommodate current
CLI behavior without hardcoding binary-specific logic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
import json
from pathlib import Path
import shutil
from typing import Any

from nyx.config import ProviderConfig
from nyx.providers.base import ModelProvider, ProviderQueryError, ProviderUnavailableError

ProcessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]


class SubprocessCLIProvider(ModelProvider):
    """Model provider backed by a non-interactive subprocess CLI."""

    def __init__(
        self,
        provider_config: ProviderConfig,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        """Initialize the provider with its configured binary and arguments."""

        super().__init__(provider_config)
        self._process_factory = process_factory or asyncio.create_subprocess_exec
        self._binary = self.require_option("binary")
        self._args = self.require_option("args")
        self._timeout_seconds = int(self.provider_config.options.get("timeout_seconds", 60))

    async def is_available(self) -> bool:
        """A subprocess provider is available when its binary is present on PATH."""

        return shutil.which(str(self._binary)) is not None

    async def query(self, prompt: str, context: dict[str, Any]) -> str:
        """Run the configured CLI non-interactively and parse stdout text."""

        if not await self.is_available():
            raise ProviderUnavailableError(
                f"CLI provider '{self.name}' binary '{self._binary}' is not installed."
            )

        rendered_prompt = self.render_prompt(prompt, context)
        command = [str(self._binary), *self._command_args(rendered_prompt)]
        stdin_payload = rendered_prompt.encode() if self._uses_stdin() else None

        process = await self._process_factory(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(stdin_payload),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ProviderQueryError(
                f"CLI provider '{self.name}' timed out after {self._timeout_seconds} seconds."
            ) from exc

        stdout_text = stdout_data.decode().strip()
        stderr_text = stderr_data.decode().strip()
        if process.returncode != 0:
            message = stderr_text or stdout_text or "no error output"
            raise ProviderQueryError(
                f"CLI provider '{self.name}' exited with code {process.returncode}: {message}"
            )

        text = _extract_cli_text(stdout_text)
        if not text:
            raise ProviderQueryError(
                f"CLI provider '{self.name}' returned no parseable text output."
            )
        return text

    @property
    def supports_vision(self) -> bool:
        """Return whether the provider has explicit image-argument support configured."""

        return bool(self.provider_config.options.get("image_args"))

    async def query_with_image(
        self,
        prompt: str,
        image_path: Path,
        context: dict[str, Any],
    ) -> str:
        """Run the configured CLI with one attached image file."""

        if not await self.is_available():
            raise ProviderUnavailableError(
                f"CLI provider '{self.name}' binary '{self._binary}' is not installed."
            )

        image_args = self._validated_image_args(image_path)
        if not image_args:
            raise ProviderUnavailableError(
                f"CLI provider '{self.name}' does not have image arguments configured."
            )

        rendered_prompt = self.render_prompt(prompt, context)
        command = [str(self._binary), *self._command_args(rendered_prompt), *image_args]
        stdin_payload = rendered_prompt.encode() if self._uses_stdin() else None

        process = await self._process_factory(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(stdin_payload),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ProviderQueryError(
                f"CLI provider '{self.name}' timed out after {self._timeout_seconds} seconds."
            ) from exc

        stdout_text = stdout_data.decode().strip()
        stderr_text = stderr_data.decode().strip()
        if process.returncode != 0:
            message = stderr_text or stdout_text or "no error output"
            raise ProviderQueryError(
                f"CLI provider '{self.name}' exited with code {process.returncode}: {message}"
            )

        text = _extract_cli_text(stdout_text)
        if not text:
            raise ProviderQueryError(
                f"CLI provider '{self.name}' returned no parseable text output."
            )
        return text

    def _uses_stdin(self) -> bool:
        """Return whether the configured CLI args signal stdin-based prompt input."""

        return any(arg == "-" for arg in self._validated_args())

    def _command_args(self, prompt: str) -> list[str]:
        """Build the subprocess argument list for the current request."""

        args = list(self._validated_args())
        if self._uses_stdin():
            return args
        return [*args, prompt]

    def _validated_args(self) -> list[str]:
        """Return the configured CLI argument list after basic validation."""

        if not isinstance(self._args, list) or not all(isinstance(arg, str) for arg in self._args):
            raise ProviderQueryError(
                f"CLI provider '{self.name}' has invalid 'args'; expected a list of strings."
            )
        return list(self._args)

    def _validated_image_args(self, image_path: Path) -> list[str]:
        """Return configured image arguments with ``{image_path}`` placeholders expanded."""

        raw_args = self.provider_config.options.get("image_args")
        if raw_args is None:
            return []
        if not isinstance(raw_args, list) or not all(isinstance(arg, str) for arg in raw_args):
            raise ProviderQueryError(
                f"CLI provider '{self.name}' has invalid 'image_args'; expected a list of strings."
            )

        resolved_path = str(image_path)
        expanded = [arg.replace("{image_path}", resolved_path) for arg in raw_args]
        if not any(resolved_path == arg for arg in expanded):
            expanded.append(resolved_path)
        return expanded


def _extract_cli_text(stdout_text: str) -> str:
    """Extract final text from CLI stdout that may be JSON or JSONL."""

    if not stdout_text:
        return ""

    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError:
        fragments: list[str] = []
        for line in stdout_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            fragments.extend(_extract_text_fragments(payload))
        return "\n".join(part for part in fragments if part).strip()

    return "\n".join(part for part in _extract_text_fragments(payload) if part).strip()


def _extract_text_fragments(payload: Any) -> list[str]:
    """Recursively extract likely assistant text fragments from JSON payloads."""

    if payload is None:
        return []
    if isinstance(payload, str):
        text = payload.strip()
        return [text] if text else []
    if isinstance(payload, list):
        fragments: list[str] = []
        for item in payload:
            fragments.extend(_extract_text_fragments(item))
        return fragments
    if not isinstance(payload, dict):
        return []

    for key in ("output_text", "response", "completion", "result", "delta", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]

    if "message" in payload:
        fragments = _extract_text_fragments(payload["message"])
        if fragments:
            return fragments

    if "item" in payload:
        fragments = _extract_text_fragments(payload["item"])
        if fragments:
            return fragments

    if "items" in payload:
        fragments = _extract_text_fragments(payload["items"])
        if fragments:
            return fragments

    if "content" in payload:
        fragments = _extract_text_fragments(payload["content"])
        if fragments:
            return fragments

    if "choices" in payload:
        fragments = _extract_text_fragments(payload["choices"])
        if fragments:
            return fragments

    if "output" in payload:
        fragments = _extract_text_fragments(payload["output"])
        if fragments:
            return fragments

    return []
