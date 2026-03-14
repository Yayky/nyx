"""Tests for Nyx configuration loading."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from nyx.config import load_config


def test_missing_config_uses_documented_defaults(tmp_path: Path) -> None:
    """A missing config file should yield the in-memory documented defaults."""

    config = load_config(tmp_path / "missing.toml")

    assert config.models.default == "ollama-local"
    assert config.voice.enabled is True
    assert config.web.fallback_timeout_seconds == 3
    assert config.system.screenshot_tmp == Path("/tmp/nyx-screen.png")


def test_path_values_are_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """User paths in config should be expanded to concrete filesystem paths."""

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    config = load_config(tmp_path / "missing.toml")

    assert config.notes.notes_dir == fake_home / "notes"
    assert config.rag.db_path == fake_home / ".local/share/nyx/rag"


def test_partial_config_merges_over_defaults(tmp_path: Path) -> None:
    """Partial TOML should override defaults while preserving the rest."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[models]
default = "openai"

[system]
yolo = true
""".strip()
    )

    config = load_config(config_path)

    assert config.models.default == "openai"
    assert config.models.fallback == ["anthropic", "codex-cli"]
    assert config.system.yolo is True


def test_provider_specific_options_are_preserved(tmp_path: Path) -> None:
    """Provider tables should allow backend-specific options beyond the Phase 1 set."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[models]
default = "ollama-vision"

[[models.providers]]
name = "ollama-vision"
type = "ollama"
model = "gemma3:4b"
host = "http://localhost:11434"
vision_timeout_seconds = 300
""".strip()
    )

    config = load_config(config_path)

    provider = config.models.providers[0]
    assert provider.name == "ollama-vision"
    assert provider.options["vision_timeout_seconds"] == 300


def test_calendar_config_supports_adc_and_multi_calendar_settings(tmp_path: Path) -> None:
    """Calendar config should preserve auth-mode and multi-calendar options."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[calendar]
provider = "google"
auth_mode = "adc"
default_calendar_id = "work@example.com"
calendar_ids = ["primary", "team@example.com"]
include_all_calendars = true
""".strip()
    )

    config = load_config(config_path)

    assert config.calendar.provider == "google"
    assert config.calendar.auth_mode == "adc"
    assert config.calendar.default_calendar_id == "work@example.com"
    assert config.calendar.calendar_ids == ["primary", "team@example.com"]
    assert config.calendar.include_all_calendars is True


def test_voice_config_can_disable_voice_input(tmp_path: Path) -> None:
    """Voice config should preserve the explicit enabled flag."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[voice]
enabled = false
whisper_model = "ggml-base.bin"
whisper_binary = "/usr/bin/whisper-cli"
""".strip()
    )

    config = load_config(config_path)

    assert config.voice.enabled is False
    assert config.voice.whisper_model == "ggml-base.bin"


def test_unknown_keys_raise_descriptive_error(tmp_path: Path) -> None:
    """Unknown config keys should fail fast with useful context."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[system]
unknown = 1
""".strip()
    )

    with pytest.raises(ValueError, match="Unknown keys in section 'system'"):
        load_config(config_path)


def test_provider_missing_required_keys_raise_descriptive_error(tmp_path: Path) -> None:
    """Provider tables should still require stable identity keys."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[models]

[[models.providers]]
name = "broken-provider"
model = "gemma3:4b"
""".strip()
    )

    with pytest.raises(ValueError, match="Missing required provider keys"):
        load_config(config_path)


def test_invalid_toml_raises_descriptive_error(tmp_path: Path) -> None:
    """Malformed TOML should preserve file path context in the raised error."""

    config_path = tmp_path / "config.toml"
    config_path.write_text("[system\n")

    with pytest.raises(tomllib.TOMLDecodeError, match=str(config_path)):
        load_config(config_path)
