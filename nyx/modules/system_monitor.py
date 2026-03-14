"""System monitor configuration module for Nyx.

Phase 17 adds proactive monitor rules backed by ``monitors.toml`` and evaluated
by a daemon-side polling service. This module handles explicit user requests to
add, list, and remove monitor rules using provider-planned JSON actions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from nyx.config import NyxConfig
from nyx.monitors import MonitorRule, MonitorsStore
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry

_MONITOR_PATTERNS = (
    re.compile(r"\balert me\b", re.IGNORECASE),
    re.compile(r"\bnotify me\b", re.IGNORECASE),
    re.compile(r"\bmonitor\b", re.IGNORECASE),
    re.compile(r"\bmonitors\b", re.IGNORECASE),
    re.compile(r"\bwatch\b.+\b(cpu|memory|disk|battery)\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {"add_monitor", "list_monitors", "remove_monitor", "reject"}
_SUPPORTED_METRICS = {
    "cpu_percent": "CPU usage percent",
    "memory_percent": "RAM usage percent",
    "disk_percent": "Disk usage percent for /",
    "battery_percent": "Battery charge percent when available",
}
_SUPPORTED_OPERATORS = {"gt", "lt"}


@dataclass(slots=True)
class MonitorPlan:
    """Validated provider-produced plan for one monitor configuration request."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class MonitorModuleResult:
    """Structured result returned by the Phase 17 system-monitor module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class SystemMonitorModule:
    """Configure proactive monitor rules backed by ``monitors.toml``."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        store: MonitorsStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the monitor module with config, providers, and store."""

        self.config = config
        self.provider_registry = provider_registry
        self.store = store or MonitorsStore(config.config_path.parent / "monitors.toml")
        self.logger = logger or logging.getLogger("nyx.modules.system_monitor")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious monitor-management request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _MONITOR_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> MonitorModuleResult:
        """Handle one explicit monitor add/list/remove request."""

        existing_rules = await self.store.load_rules()
        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text, existing_rules),
            context=self._planner_context(existing_rules),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "System-monitor planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        if plan.operation == "list_monitors":
            current_rules = await self.store.load_rules()
            if not current_rules:
                return self._result_from_provider(
                    provider_result,
                    "No proactive monitor rules are configured.",
                    plan.operation,
                )
            return self._result_from_provider(
                provider_result,
                self._format_rules(current_rules),
                plan.operation,
            )

        if plan.operation == "remove_monitor":
            identifier = self._require_string_argument(plan.arguments, "identifier")
            removed = await self.store.remove_rule(identifier)
            if removed is None:
                return self._result_from_provider(
                    provider_result,
                    f"Nyx could not find a monitor rule matching '{identifier}'.",
                    plan.operation,
                )
            return self._result_from_provider(
                provider_result,
                f"Removed monitor '{removed.name}' ({removed.rule_id}).",
                plan.operation,
            )

        metric = self._require_metric(plan.arguments)
        operator = self._require_operator(plan.arguments)
        threshold = self._require_threshold(plan.arguments)
        name = self._require_string_argument(plan.arguments, "name")
        message = self._optional_string_argument(plan.arguments, "message") or (
            "{name} triggered: {metric} is {value}, threshold {operator} {threshold}."
        )
        cooldown_seconds = self._optional_int_argument(plan.arguments, "cooldown_seconds") or 300
        rule = MonitorRule(
            rule_id=self.store.new_rule_id(),
            name=name,
            metric=metric,
            operator=operator,
            threshold=threshold,
            message=message,
            cooldown_seconds=cooldown_seconds,
            enabled=True,
        )
        await self.store.add_rule(rule)
        return self._result_from_provider(
            provider_result,
            (
                f"Added monitor '{rule.name}' ({rule.rule_id}) for {rule.metric} "
                f"{rule.operator} {rule.threshold:.1f}."
            ),
            plan.operation,
        )

    def _planner_context(self, rules: list[MonitorRule]) -> dict[str, Any]:
        """Return planning context for monitor configuration requests."""

        return {
            "module": "system_monitor",
            "supported_metrics": _SUPPORTED_METRICS,
            "supported_operators": sorted(_SUPPORTED_OPERATORS),
            "poll_interval_seconds": self.config.monitors.poll_interval_seconds,
            "existing_rules": [
                {
                    "id": rule.rule_id,
                    "name": rule.name,
                    "metric": rule.metric,
                    "operator": rule.operator,
                    "threshold": rule.threshold,
                    "cooldown_seconds": rule.cooldown_seconds,
                }
                for rule in rules
            ],
        }

    def _build_planner_prompt(self, request_text: str, rules: list[MonitorRule]) -> str:
        """Build the provider prompt for one monitor configuration request."""

        rule_list = ", ".join(f"{rule.name} ({rule.rule_id})" for rule in rules) or "(none)"
        metric_list = ", ".join(f"{key}={value}" for key, value in _SUPPORTED_METRICS.items())
        return (
            "You are Nyx's Phase 17 system-monitor planner. "
            "Return exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown. Allowed operations: add_monitor, list_monitors, remove_monitor, reject. "
            f"Existing monitor rules: {rule_list}. Supported metrics: {metric_list}. "
            "Supported operators are gt (greater than) and lt (less than). "
            "Use add_monitor for requests like 'alert me if memory exceeds 90 percent'. "
            "Use remove_monitor when the user wants to delete or stop one existing monitor. "
            "Use list_monitors for requests to show configured monitors. "
            "If the request is not a monitor-management action, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- add_monitor: {"name": str, "metric": str, "operator": "gt"|"lt", "threshold": number, "message": str|null, "cooldown_seconds": int|null}\n'
            '- list_monitors: {}\n'
            '- remove_monitor: {"identifier": str}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    def _parse_plan(self, planner_text: str) -> MonitorPlan:
        """Parse and validate the JSON monitor plan returned by the provider."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("System-monitor planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported system-monitor operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("System-monitor planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("System-monitor planner 'rationale' must be a string when present.")

        return MonitorPlan(operation=operation, arguments=arguments, rationale=rationale)

    def _extract_json_object(self, text: str) -> str:
        """Extract one JSON object from raw provider output."""

        fenced_match = _JSON_BLOCK_PATTERN.search(text)
        if fenced_match is not None:
            return fenced_match.group(1).strip()

        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        object_match = _JSON_OBJECT_PATTERN.search(text)
        if object_match is None:
            raise ValueError("System-monitor planner did not return a JSON object.")
        return object_match.group(0).strip()

    def _format_rules(self, rules: list[MonitorRule]) -> str:
        """Render the current monitor rules as a concise listing."""

        lines = ["Configured monitor rules:"]
        for rule in rules:
            status = "enabled" if rule.enabled else "disabled"
            lines.append(
                f"- {rule.name} ({rule.rule_id}) [{status}] — {rule.metric} {rule.operator} {rule.threshold:.1f}, cooldown {rule.cooldown_seconds}s"
            )
        return "\n".join(lines)

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required non-empty string argument."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Monitor action is missing string argument '{key}'.")
        return value.strip()

    def _optional_string_argument(self, arguments: dict[str, Any], key: str) -> str | None:
        """Return one optional string argument when present."""

        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Monitor action argument '{key}' must be a string when present.")
        normalized = value.strip()
        return normalized or None

    def _optional_int_argument(self, arguments: dict[str, Any], key: str) -> int | None:
        """Return one optional integer argument when present."""

        value = arguments.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Monitor action argument '{key}' must be numeric when present.")
        return int(value)

    def _require_metric(self, arguments: dict[str, Any]) -> str:
        """Return one validated monitor metric key."""

        metric = self._require_string_argument(arguments, "metric")
        if metric not in _SUPPORTED_METRICS:
            supported = ", ".join(sorted(_SUPPORTED_METRICS))
            raise ValueError(f"Unsupported monitor metric '{metric}'. Supported: {supported}.")
        return metric

    def _require_operator(self, arguments: dict[str, Any]) -> str:
        """Return one validated monitor comparison operator."""

        operator = self._require_string_argument(arguments, "operator")
        if operator not in _SUPPORTED_OPERATORS:
            raise ValueError("Monitor operator must be 'gt' or 'lt'.")
        return operator

    def _require_threshold(self, arguments: dict[str, Any]) -> float:
        """Return one numeric threshold from the planner payload."""

        value = arguments.get("threshold")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Monitor action is missing numeric argument 'threshold'.")
        return float(value)

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
        *,
        degraded: bool | None = None,
    ) -> MonitorModuleResult:
        """Build a monitor-module result while preserving provider metadata."""

        return MonitorModuleResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used if degraded is None else degraded,
            operation=operation,
        )
