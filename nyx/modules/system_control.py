"""System control feature module for Nyx.

Phase 6 introduces a real feature module that maps natural-language system
requests onto the stable ``SystemBridge`` surface. The module asks the selected
model provider to produce a small JSON action plan, validates that plan against
the documented bridge operations, then executes the selected bridge call.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from nyx.bridges.base import (
    BridgeCommandError,
    BridgeConfirmationRequiredError,
    BridgeSecurityError,
    SystemBridge,
    WindowInfo,
)
from nyx.config import NyxConfig
from nyx.providers.base import ProviderError, ProviderQueryResult
from nyx.providers.registry import ProviderRegistry

_SYSTEM_CONTROL_PATTERNS = (
    re.compile(r"\b(active window|focused window|current window)\b", re.IGNORECASE),
    re.compile(r"\b(list|show)\s+(windows|processes)\b", re.IGNORECASE),
    re.compile(r"\bmove\b.+\bworkspace\b", re.IGNORECASE),
    re.compile(r"\b(workspace|window)\b", re.IGNORECASE),
    re.compile(r"\b(volume|brightness|mute|unmute)\b", re.IGNORECASE),
    re.compile(r"\b(screenshot|screen shot|capture screen)\b", re.IGNORECASE),
    re.compile(r"\b(kill|terminate)\b.+\b(process|pid)\b", re.IGNORECASE),
    re.compile(r"\b(system stats|cpu|memory|ram|disk usage|load average)\b", re.IGNORECASE),
    re.compile(r"\bnotify|notification\b", re.IGNORECASE),
    re.compile(r"\b(run|execute)\b.+\b(command|shell|bash)\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {
    "get_active_window",
    "move_window_to_workspace",
    "list_windows",
    "screenshot",
    "run_command",
    "list_processes",
    "kill_process",
    "set_brightness",
    "set_volume",
    "get_system_stats",
    "notify",
    "reject",
}


@dataclass(slots=True)
class SystemControlAction:
    """Validated action plan produced by the provider layer.

    Attributes:
        operation: Bridge operation name or ``reject``.
        arguments: Arguments consumed by the chosen operation.
        rationale: Optional planner rationale used only for logging/diagnostics.
    """

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class SystemControlResult:
    """Structured result returned by the Phase 6 system-control module.

    Attributes:
        response_text: User-facing summary or command output.
        used_model: Provider name used for planning.
        model_name: Underlying provider model, when exposed.
        token_count: Provider usage metadata when available.
        degraded: Whether a provider fallback path was required.
        operation: Executed bridge operation or ``reject``.
    """

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class SystemControlModule:
    """Plan and execute system operations through ``SystemBridge`` only."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        provider_registry: ProviderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit runtime dependencies."""

        self.config = config
        self.bridge = bridge
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.modules.system_control")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether a request is an obvious Phase 6 system-control prompt."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _SYSTEM_CONTROL_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> SystemControlResult:
        """Plan and execute one system-control request.

        Args:
            request_text: Natural-language request supplied by the user.
            model_override: Optional provider override from the CLI/UI layer.

        Returns:
            A structured system-control result suitable for router conversion.
        """

        prompt = self._build_planner_prompt(request_text)
        provider_result = await self.provider_registry.query(
            prompt=prompt,
            context=self._planner_context(),
            preferred_provider_name=model_override,
        )
        action = self._parse_action(provider_result.text)
        self.logger.info(
            "System-control planner selected operation=%s provider=%s",
            action.operation,
            provider_result.provider_name,
        )

        if action.operation == "reject":
            reason = self._require_string_argument(action.arguments, "reason")
            return self._result_from_provider(
                provider_result=provider_result,
                response_text=reason,
                operation=action.operation,
            )

        try:
            response_text = await self._execute_action(action)
        except BridgeConfirmationRequiredError as exc:
            response_text = str(exc)
        except BridgeSecurityError as exc:
            response_text = f"Blocked by Nyx security policy: {exc}"
        except BridgeCommandError as exc:
            response_text = f"System command failed: {exc}"

        return self._result_from_provider(
            provider_result=provider_result,
            response_text=response_text,
            operation=action.operation,
        )

    def _planner_context(self) -> dict[str, Any]:
        """Return the static planning context exposed to the provider layer."""

        return {
            "module": "system_control",
            "allowed_operations": sorted(_ALLOWED_OPERATIONS - {"reject"}),
            "yolo": self.config.system.yolo,
            "confirmation_required_for_destructive": self.config.system.confirm_destructive,
        }

    def _build_planner_prompt(self, request_text: str) -> str:
        """Return the system-control planning prompt sent to the provider."""

        return (
            "You are Nyx's Phase 6 system-control planner. "
            "Choose exactly one operation from the allowed bridge surface and return "
            "JSON only with keys operation, arguments, and rationale. "
            "Do not return markdown or explanation outside the JSON object. "
            "If the request is not a system-control action, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- get_active_window: arguments = {}\n'
            '- move_window_to_workspace: arguments = {"window": str, "workspace": str}\n'
            '- list_windows: arguments = {}\n'
            '- screenshot: arguments = {"path": str} and prefer the configured screenshot tmp path when the user did not specify one\n'
            '- run_command: arguments = {"command": str}\n'
            '- list_processes: arguments = {}\n'
            '- kill_process: arguments = {"identifier": str}\n'
            '- set_brightness: arguments = {"percent": int}\n'
            '- set_volume: arguments = {"percent": int}\n'
            '- get_system_stats: arguments = {}\n'
            '- notify: arguments = {"title": str, "body": str}\n\n'
            f"User request: {request_text}"
        )

    def _parse_action(self, planner_text: str) -> SystemControlAction:
        """Parse and validate a JSON action returned by the planner model."""

        payload = self._extract_json_object(planner_text)
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"System-control planner returned invalid JSON: {exc}") from exc

        if not isinstance(decoded, dict):
            raise ValueError("System-control planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported system-control operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("System-control planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("System-control planner 'rationale' must be a string when present.")

        return SystemControlAction(operation=operation, arguments=arguments, rationale=rationale)

    def _extract_json_object(self, text: str) -> str:
        """Extract a JSON object from raw provider text or fenced JSON output."""

        fenced_match = _JSON_BLOCK_PATTERN.search(text)
        if fenced_match is not None:
            return fenced_match.group(1).strip()

        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        object_match = _JSON_OBJECT_PATTERN.search(text)
        if object_match is None:
            raise ValueError("System-control planner did not return a JSON object.")
        return object_match.group(0).strip()

    async def _execute_action(self, action: SystemControlAction) -> str:
        """Execute one validated action through the injected bridge."""

        if action.operation == "get_active_window":
            window = await self.bridge.get_active_window()
            return self._format_window(window, label="Active window")
        if action.operation == "move_window_to_workspace":
            window = self._require_string_argument(action.arguments, "window")
            workspace = self._require_string_argument(action.arguments, "workspace")
            moved = await self.bridge.move_window_to_workspace(window=window, workspace=workspace)
            if moved:
                return f"Moved '{window}' to workspace {workspace}."
            return f"Nyx could not find or move '{window}' to workspace {workspace}."
        if action.operation == "list_windows":
            windows = await self.bridge.list_windows()
            if not windows:
                return "No windows are currently available."
            return "\n".join(self._format_window(window) for window in windows)
        if action.operation == "screenshot":
            default_path = str(self.config.system.screenshot_tmp)
            path = action.arguments.get("path", default_path)
            if not isinstance(path, str) or not path.strip():
                raise ValueError("Screenshot path must be a non-empty string.")
            captured = await self.bridge.screenshot(path)
            if captured:
                return f"Screenshot saved to {path}."
            return f"Nyx could not save a screenshot to {path}."
        if action.operation == "run_command":
            command = self._require_string_argument(action.arguments, "command")
            output = await self.bridge.run_command(command)
            return output or "Command completed with no output."
        if action.operation == "list_processes":
            processes = await self.bridge.list_processes()
            if not processes:
                return "No matching processes were returned."
            rendered = [
                f"{process.get('pid', '?'):>5}  {process.get('name', '')}  {process.get('command', '')}".rstrip()
                for process in processes[:25]
            ]
            if len(processes) > 25:
                rendered.append(f"... {len(processes) - 25} more processes omitted")
            return "\n".join(rendered)
        if action.operation == "kill_process":
            identifier = self._require_string_argument(action.arguments, "identifier")
            killed = await self.bridge.kill_process(identifier)
            if killed:
                return f"Termination signal sent for '{identifier}'."
            return f"Nyx could not terminate '{identifier}'."
        if action.operation == "set_brightness":
            percent = self._require_int_argument(action.arguments, "percent")
            changed = await self.bridge.set_brightness(percent)
            if changed:
                return f"Brightness set to {percent}%."
            return f"Nyx could not set brightness to {percent}%."
        if action.operation == "set_volume":
            percent = self._require_int_argument(action.arguments, "percent")
            changed = await self.bridge.set_volume(percent)
            if changed:
                return f"Volume set to {percent}%."
            return f"Nyx could not set volume to {percent}%."
        if action.operation == "get_system_stats":
            stats = await self.bridge.get_system_stats()
            return json.dumps(stats, indent=2, sort_keys=True)
        if action.operation == "notify":
            title = self._require_string_argument(action.arguments, "title")
            body = self._require_string_argument(action.arguments, "body")
            await self.bridge.notify(title=title, body=body)
            return f"Notification sent: {title}"

        raise ValueError(f"Unsupported system-control operation: {action.operation}")

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
    ) -> SystemControlResult:
        """Build the module result while preserving provider metadata."""

        return SystemControlResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used,
            operation=operation,
        )

    def _format_window(self, window: WindowInfo, label: str | None = None) -> str:
        """Render one window record into a concise user-facing string."""

        parts = [window.app_name or "unknown-app"]
        if window.window_title:
            parts.append(window.window_title)
        if window.workspace is not None:
            parts.append(f"workspace {window.workspace}")
        prefix = f"{label}: " if label else ""
        return prefix + " | ".join(parts)

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required string argument from an action payload."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"System-control action is missing string argument '{key}'.")
        return value.strip()

    def _require_int_argument(self, arguments: dict[str, Any], key: str) -> int:
        """Return one required integer argument from an action payload."""

        value = arguments.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"System-control action is missing integer argument '{key}'.")
        return value
