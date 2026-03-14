"""Persistent monitor rule storage for Nyx."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import secrets
import tomllib


@dataclass(slots=True)
class MonitorRule:
    """One persistent proactive monitor rule stored in ``monitors.toml``."""

    rule_id: str
    name: str
    metric: str
    operator: str
    threshold: float
    message: str
    cooldown_seconds: int
    enabled: bool = True


class MonitorsStore:
    """Load and save monitor rules from the user-scoped TOML file."""

    def __init__(self, store_path: Path) -> None:
        """Store the concrete TOML path used for persistent rules."""

        self.store_path = store_path

    async def load_rules(self) -> list[MonitorRule]:
        """Return all persisted monitor rules, or an empty list when absent."""

        def _sync_load() -> list[MonitorRule]:
            if not self.store_path.exists():
                return []
            with self.store_path.open("rb") as handle:
                decoded = tomllib.load(handle)
            raw_rules = decoded.get("monitors", [])
            if not isinstance(raw_rules, list):
                raise ValueError(f"Monitor store {self.store_path} must use [[monitors]] tables.")

            rules: list[MonitorRule] = []
            for raw in raw_rules:
                if not isinstance(raw, dict):
                    raise ValueError(f"Monitor store {self.store_path} contains a non-table monitor entry.")
                rules.append(
                    MonitorRule(
                        rule_id=str(raw["id"]),
                        name=str(raw["name"]),
                        metric=str(raw["metric"]),
                        operator=str(raw["operator"]),
                        threshold=float(raw["threshold"]),
                        message=str(raw["message"]),
                        cooldown_seconds=int(raw.get("cooldown_seconds", 300)),
                        enabled=bool(raw.get("enabled", True)),
                    )
                )
            return rules

        return await asyncio.to_thread(_sync_load)

    async def save_rules(self, rules: list[MonitorRule]) -> None:
        """Persist the complete set of monitor rules to disk."""

        def _sync_save() -> None:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            lines: list[str] = [
                "# Nyx proactive monitor rules",
                "# This file is managed by Nyx and can also be edited manually.",
            ]
            for rule in rules:
                lines.extend(
                    [
                        "",
                        "[[monitors]]",
                        f'id = "{_escape_toml(rule.rule_id)}"',
                        f'name = "{_escape_toml(rule.name)}"',
                        f'metric = "{_escape_toml(rule.metric)}"',
                        f'operator = "{_escape_toml(rule.operator)}"',
                        f"threshold = {rule.threshold}",
                        f'message = "{_escape_toml(rule.message)}"',
                        f"cooldown_seconds = {rule.cooldown_seconds}",
                        f"enabled = {'true' if rule.enabled else 'false'}",
                    ]
                )
            self.store_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

        await asyncio.to_thread(_sync_save)

    async def add_rule(self, rule: MonitorRule) -> MonitorRule:
        """Append one new rule and persist it."""

        rules = await self.load_rules()
        rules.append(rule)
        await self.save_rules(rules)
        return rule

    async def remove_rule(self, identifier: str) -> MonitorRule | None:
        """Remove one rule by id or case-insensitive name."""

        rules = await self.load_rules()
        remaining: list[MonitorRule] = []
        removed: MonitorRule | None = None
        normalized = identifier.casefold()
        for rule in rules:
            if removed is None and (
                rule.rule_id.casefold() == normalized or rule.name.casefold() == normalized
            ):
                removed = rule
                continue
            remaining.append(rule)
        if removed is None:
            return None
        await self.save_rules(remaining)
        return removed

    async def find_rule(self, identifier: str) -> MonitorRule | None:
        """Return one rule by id or case-insensitive name."""

        normalized = identifier.casefold()
        for rule in await self.load_rules():
            if rule.rule_id.casefold() == normalized or rule.name.casefold() == normalized:
                return rule
        return None

    def new_rule_id(self) -> str:
        """Return a short stable identifier for a new monitor rule."""

        return secrets.token_hex(4)


def _escape_toml(value: str) -> str:
    """Escape one string value for safe TOML output."""

    return value.replace("\\", "\\\\").replace('"', '\\"')
